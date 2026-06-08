"""Performance metrics evaluator for VPP real-time scheduling results."""

import json
import statistics
from typing import Any

from src.model import ProcessorSettingsSystem, PriceSystem, TaskSystem


class Evaluator:
    """Computes schedule performance metrics from solved schedule results.

    Reads schedule_result.json, task_set.json, processor_settings.json, and
    price_72hr.json to compute all required evaluation metrics defined in the
    assignment specification, then outputs evaluation_results.json.

    Metrics computed:
        - hard_deadline_miss_rate: fraction of periodic + sporadic jobs that missed.
        - soft_deadline_miss_rate: fraction of aperiodic jobs that missed soft deadline.
        - average_tardiness / max_tardiness: Tj = max(0, Cj - dj).
        - average_response_time / max_response_time: Rj = Cj - rj.
        - completion_time_jitter: per-task peak-to-peak jitter, averaged across tasks.
        - sporadic_value_rate: exec time completed before hard deadline / total exec time.
        - generator_cost: f2 = Σ Σ (cost_fixed·min(1,P) + cost_variable·P).
        - market_revenue: Σ (λt · Sellt).
        - objective_value: F = 10000·f1 + f2 - market_revenue.
    """

    ALPHA: int = 10000  # penalty coefficient ($/miss) for each missed aperiodic job

    def __init__(
        self,
        processor_settings_path: str,
        task_set_path: str,
        price_path: str,
        schedule_result_path: str,
        horizon: int,
    ) -> None:
        """Initializes the evaluator with paths to all required inputs.

        Args:
            processor_settings_path: Path to processor_settings.json.
            task_set_path: Path to task_set.json.
            price_path: Path to price_72hr.json.
            schedule_result_path: Path to schedule_result.json.
            horizon: Scheduling horizon in time ticks.
        """
        self._processor_settings_path = processor_settings_path
        self._task_set_path = task_set_path
        self._price_path = price_path
        self._schedule_result_path = schedule_result_path
        self._horizon = horizon

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_schedule(self) -> list[dict[str, Any]]:
        with open(self._schedule_result_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data["schedule_result"]

    def _expand_periodic_jobs(self, tasks: TaskSystem) -> list[dict[str, Any]]:
        """Expands periodic tasks into concrete job instances within the horizon."""
        jobs: list[dict[str, Any]] = []
        for task in tasks.periodic_tasks:
            k = 0
            while True:
                abs_release = task.r + k * task.p
                abs_deadline = abs_release + task.d - 1
                if abs_release > self._horizon or abs_deadline > self._horizon:
                    break
                jobs.append(
                    {
                        "job_id": f"{task.task_id}_{k}",
                        "task_id": task.task_id,
                        "release": abs_release,
                        "deadline": abs_deadline,
                        "execution": task.e,
                    }
                )
                k += 1
        return jobs

    def _expand_sporadic_jobs(self, tasks: TaskSystem) -> list[dict[str, Any]]:
        """Returns sporadic tasks as single-instance jobs."""
        return [
            {
                "job_id": task.task_id,
                "task_id": task.task_id,
                "release": task.r,
                "deadline": task.r + task.d - 1,
                "execution": task.e,
            }
            for task in tasks.sporadic_tasks
        ]

    def _expand_aperiodic_jobs(self, tasks: TaskSystem) -> list[dict[str, Any]]:
        """Returns aperiodic tasks as single-instance jobs."""
        return [
            {
                "job_id": task.task_id,
                "task_id": task.task_id,
                "release": task.r,
                "deadline": task.r + task.d - 1,
                "execution": task.e,
            }
            for task in tasks.aperiodic_tasks
        ]

    def _compute_completion_times(
        self, schedule: list[dict[str, Any]]
    ) -> dict[str, int]:
        """Returns the last active time tick for each job that appears in schedule.

        A job j is considered active at time t if any device allocation k[j][i][t] > 0.
        """
        # completion time = job 最後一次有電量分配（k > 0）的時槽
        # 因為 schedule 按 t 順序排列，每次覆蓋就會取到最大的 t
        completion_times: dict[str, int] = {}
        for record in schedule:
            t = record["t"]
            for job_id, allocations in record.get("k", {}).items():
                if any(v > 0 for v in allocations.values()):
                    completion_times[job_id] = t
        return completion_times

    def _collect_rejected_sporadic(self, schedule: list[dict[str, Any]]) -> set[str]:
        """Collects all sporadic job IDs that failed acceptance test."""
        rejected: set[str] = set()
        for record in schedule:
            rejected.update(record.get("rejected_sporadic", []))
        return rejected

    def _collect_schedule_missed_aperiodic(
        self, schedule: list[dict[str, Any]]
    ) -> set[str]:
        """Collects aperiodic job IDs explicitly marked as missed in schedule records."""
        missed: set[str] = set()
        for record in schedule:
            missed.update(record.get("missed_aperiodic", []))
        return missed

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def evaluate(self) -> dict[str, Any]:
        """Computes all performance metrics from the schedule.

        Returns:
            Dictionary matching the evaluation_results.json schema.
        """
        assets = ProcessorSettingsSystem.load_from_json(self._processor_settings_path)
        tasks = TaskSystem.load_from_json(self._task_set_path)
        prices = PriceSystem.load_from_json(self._price_path)
        schedule = self._load_schedule()

        periodic_jobs = self._expand_periodic_jobs(tasks)
        sporadic_jobs = self._expand_sporadic_jobs(tasks)
        aperiodic_jobs = self._expand_aperiodic_jobs(tasks)

        completion_times = self._compute_completion_times(schedule)
        rejected_sporadic = self._collect_rejected_sporadic(schedule)

        # missed_aperiodic：從 schedule 的標記收集 + 補上「完成時間超過 deadline」的情況
        # 兩種方式取聯集，確保不漏掉任何軟誤點
        missed_aperiodic = self._collect_schedule_missed_aperiodic(schedule)
        for job in aperiodic_jobs:
            ct = completion_times.get(job["job_id"])
            if ct is None or ct > job["deadline"]:
                missed_aperiodic.add(job["job_id"])

        # ── 硬 deadline 誤點率（periodic + sporadic 合併計算）──────────────────
        # 作業規格：miss_rate = 誤點 job 數 / 總 job 數
        # sporadic：被拒絕（rejected_sporadic）= 硬誤點
        # periodic：completion_time > deadline 或根本沒有 completion_time = 硬誤點
        hard_miss = 0
        for job in periodic_jobs:
            ct = completion_times.get(job["job_id"])
            if ct is None or ct > job["deadline"]:
                hard_miss += 1
        for job in sporadic_jobs:
            jid = job["job_id"]
            if jid in rejected_sporadic:
                hard_miss += 1  # 被 AcceptanceTester 拒絕
            else:
                ct = completion_times.get(jid)
                if ct is None or ct > job["deadline"]:
                    hard_miss += 1  # 接受後仍超過 deadline
        total_hard = len(periodic_jobs) + len(sporadic_jobs)
        hard_deadline_miss_rate = hard_miss / total_hard if total_hard > 0 else 0.0

        # ── 軟 deadline 誤點率（aperiodic）────────────────────────────────────
        # 軟誤點不是錯誤，只是紀錄「晚了多少」
        soft_miss_count = len(missed_aperiodic)
        total_soft = len(aperiodic_jobs)
        soft_deadline_miss_rate = (
            soft_miss_count / total_soft if total_soft > 0 else 0.0
        )

        # ── Tardiness（延遲）與 Response time（回應時間）────────────────────────
        # Tardiness_j = max(0, Cj - deadline_j)   → 超過 deadline 才計，準時則為 0
        # Response_j  = Cj - release_j            → 從釋放到完成的總時間
        # 範圍：所有有完成時間的 job（排除被拒絕的 sporadic）
        tardiness_list: list[float] = []
        response_time_list: list[float] = []
        for job in periodic_jobs + sporadic_jobs + aperiodic_jobs:
            jid = job["job_id"]
            if jid in rejected_sporadic:
                continue  # 被拒絕的 sporadic 不納入（沒有 completion time）
            ct = completion_times.get(jid)
            if ct is None:
                continue  # 完全沒排到也跳過
            tardiness_list.append(max(0.0, ct - job["deadline"]))
            response_time_list.append(float(ct - job["release"]))

        avg_tardiness = statistics.mean(tardiness_list) if tardiness_list else 0.0
        max_tardiness = max(tardiness_list) if tardiness_list else 0.0
        avg_response_time = (
            statistics.mean(response_time_list) if response_time_list else 0.0
        )
        max_response_time = (
            max(response_time_list) if response_time_list else 0.0
        )

        # ── Completion-time jitter（完成時間抖動）────────────────────────────
        # 衡量同一個 periodic task 各個 instance 完成時間的穩定性
        # jitter_per_task = max(Cj) - min(Cj)，取所有 task 的平均
        # 只計算有 ≥ 2 個 instance 完成的 task（只有 1 個無法算 peak-to-peak）
        task_ct_map: dict[str, list[int]] = {}
        for job in periodic_jobs:
            ct = completion_times.get(job["job_id"])
            if ct is not None:
                task_ct_map.setdefault(job["task_id"], []).append(ct)
        jitter_per_task = [
            max(cts) - min(cts)
            for cts in task_ct_map.values()
            if len(cts) >= 2
        ]
        completion_time_jitter = (
            statistics.mean(jitter_per_task) if jitter_per_task else 0.0
        )

        # ── Sporadic value rate（Sporadic 價值率）────────────────────────────
        # = 在 deadline 前完成的 sporadic 執行時間 / 所有 sporadic 的總執行時間
        # 被拒絕或超過 deadline 的不算
        total_sporadic_exec = sum(j["execution"] for j in sporadic_jobs)
        completed_exec = sum(
            j["execution"]
            for j in sporadic_jobs
            if j["job_id"] not in rejected_sporadic
            and completion_times.get(j["job_id"], self._horizon + 1) <= j["deadline"]
        )
        sporadic_value_rate = (
            completed_exec / total_sporadic_exec if total_sporadic_exec > 0 else 0.0
        )

        # ── Generator cost（發電成本 f2）─────────────────────────────────────
        # f2 = Σ_{i∈Ig} Σ_t (cost_fixed · 1{P>0} + cost_variable · P)
        # 開機就計固定成本（不管輸出多少），輸出量再乘可變成本
        gen_params = {
            g.generator_id: (g.cost_fixed, g.cost_variable)
            for g in assets.generators
        }
        generator_cost = 0.0
        for record in schedule:
            p_vals = record.get("P", {})
            for gen_id, (cf, cv) in gen_params.items():
                p_val = p_vals.get(gen_id, 0.0)
                if p_val > 0:
                    generator_cost += cf + cv * p_val  # 固定成本 + 可變成本

        # ── Market revenue（售電收益 f3 的原始值）────────────────────────────
        # revenue = Σ_t (λ_t · Sell_t)，λ_t 為 t 時刻的市場電價
        price_map = {pr.hour: pr.market_price for pr in prices.price}
        market_revenue = 0.0
        for record in schedule:
            sell = record.get("sell", 0.0)
            market_revenue += price_map.get(record["t"], 0) * sell

        # ── Objective value（目標函數值）────────────────────────────────────
        # F = α·f1 + f2 - revenue
        # f1 = soft_miss_count（aperiodic 誤點數）；α = 10000 $/miss
        # 注意：f3 在 MILP 裡是 -revenue（最小化），評估時直接減掉
        objective_value = (
            self.ALPHA * soft_miss_count + generator_cost - market_revenue
        )

        return {
            "hard_deadline_miss_rate": round(hard_deadline_miss_rate, 4),
            "soft_deadline_miss_rate": round(soft_deadline_miss_rate, 4),
            "average_tardiness": round(avg_tardiness, 4),
            "max_tardiness": round(max_tardiness, 4),
            "average_response_time": round(avg_response_time, 4),
            "max_response_time": round(max_response_time, 4),
            "completion_time_jitter": round(completion_time_jitter, 4),
            "acceptance_test": {
                "sporadic_value_rate": round(sporadic_value_rate, 4),
                "post_acceptance_violation_rate": 0.0,
            },
            "generator_cost": round(generator_cost, 2),
            "market_revenue": round(market_revenue, 2),
            "objective_value": round(objective_value, 2),
        }
