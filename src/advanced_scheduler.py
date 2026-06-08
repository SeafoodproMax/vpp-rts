"""Level 2 advanced dynamic scheduler: rolling-horizon re-optimization.

Where Level 1 solves a single static day-ahead MILP over the whole 72-hour
horizon, the Level 2 method walks the horizon forward and re-optimizes the
*remaining* horizon at trigger points, adapting to information that the static
plan cannot see:

* **Renewable uncertainty (Assumption 11).** The realized PV availability is drawn
  from a seeded stochastic model around the forecast. Each committed block uses the
  realized availability; the still-unseen tail uses a conservatively derated
  forecast. A large realized deviation is itself a re-optimization trigger.
* **Realistic storage (Assumption 12).** Every re-optimization uses the relaxed
  storage model (efficiency, self-discharge, cycle and SOC-dependent power limits,
  aging cost) and carries the *actual* SOC forward across boundaries.
* **Job precedence (Assumption 5).** Optional ordering constraints are honoured in
  every solve.
* **Online sporadic/aperiodic admission.** Sporadic (hard) and aperiodic (soft)
  jobs are revealed at their release time. Admission is decided by *reservation
  feasibility*: a job is accepted only if the re-optimization can hold enough
  redirectable surplus (``Sell``) across its execution window. Held reserve is
  carried forward and the job is routed into committed blocks as they freeze, so an
  accepted hard job always completes before its deadline.

Receding-horizon mechanics use the formulator's ``pin_prefix``: at each trigger the
already-executed ticks are pinned to their committed values and only the tail is
re-optimized. Pinning the continuous state (``P``, ``k``, ``SOC``, ``Sell``) carries
generator on/off duration, ramp position and SOC across the boundary, because the
Level 1 constraints derive the binaries from that state. No new decision variable is
introduced anywhere in Level 2.
"""

import random
import time
from typing import Any

import pulp

from src.model import (
    BaseRTTask,
    HourlyForecast,
    PriceSystem,
    ProcessorSettingsSystem,
    RenewableForecast,
    SporadicTask,
    TaskSystem,
)
from src.rt_scheduler.expander import JobExpander
from src.rt_scheduler.formulator import VppMilpFormulator
from src.rt_scheduler.relaxation import RelaxationConfig

_EPS = 1e-4


class AdvancedScheduler:
    """Rolling-horizon re-optimizing scheduler for the Level 2 relaxed model.

    【滾動視野 Rolling Horizon 概念】
    Level 1 一次性對整個 72 小時建立 MILP 並求解（日前靜態排程）。
    Level 2 把排程分成多個 round，每個 round 的觸發點包含：
      1. 定期重排（每 reopt_interval ticks）
      2. Sporadic 任務釋放（事件驅動）
      3. 再生能源大幅偏離預測（不確定性觸發）

    每個 round 的動作：
      ① 揭露新到達的 sporadic/aperiodic 任務
      ② 嘗試接受任務（若無法排入則拒絕）
      ③ 以 pin_prefix 凍結已執行的前綴，只重解剩餘 horizon
      ④ 把已接受任務從 Sell 的 reserve 「路由」進 k 變數
      ⑤ 記錄本 round 的決策到 run_log
    """

    def __init__(
        self,
        processor_settings_path: str,
        task_set_path: str,
        price_path: str,
        horizon: int,
        epsilon: float,
        relaxation: RelaxationConfig | None = None,
        reopt_interval: int = 24,
        renewable_noise_std: float = 0.2,
        renewable_deviation_threshold: float = 0.25,
        seed: int = 42,
        max_reserve_per_tick: float = 120.0,
        max_solves: int = 80,
        max_deviation_triggers: int = 3,
        gap_rel: float = 0.20,
        time_limit: int = 20,
        threads: int = 1,
        verbose: bool = False,
        assets: ProcessorSettingsSystem | None = None,
        tasks: TaskSystem | None = None,
        prices: PriceSystem | None = None,
    ) -> None:
        """Initializes the dynamic scheduler.

        Args:
            processor_settings_path: Path to processor_settings.json.
            task_set_path: Path to task_set.json.
            price_path: Path to price_72hr.json.
            horizon: Planning horizon in ticks.
            epsilon: Threshold below which extracted values are treated as zero.
            relaxation: Level 2 relaxed-assumption parameters (storage realism,
                renewable margin, precedence). Defaults to a no-op config.
            reopt_interval: Periodic re-optimization cadence in ticks.
            renewable_noise_std: Std-dev of the multiplicative noise used to realize
                actual PV availability from the forecast.
            renewable_deviation_threshold: Absolute forecast/actual gap that triggers
                an extra (event-driven) re-optimization.
            seed: RNG seed for reproducible renewable realization.
            max_reserve_per_tick: Cap on the per-tick reservation floor (MWh).
            max_solves: Safety cap on the number of MILP solves.
            verbose: When True, prints a per-round progress line so the dynamic
                phase reports progress instead of appearing to hang during its
                several MILP solves.
            assets: Optional pre-loaded assets aggregate (dependency injection).
            tasks: Optional pre-loaded task aggregate (dependency injection).
            prices: Optional pre-loaded price aggregate (dependency injection).
        """
        self._processor_settings_path = processor_settings_path
        self._task_set_path = task_set_path
        self._price_path = price_path
        self._horizon = horizon
        self._eps = epsilon
        self._relax = relaxation or RelaxationConfig()
        self._reopt_interval = max(1, reopt_interval)   # 至少 1 tick 觸發一次
        self._noise_std = renewable_noise_std             # PV 實現值的隨機擾動幅度
        self._deviation_threshold = renewable_deviation_threshold  # 偏離觸發閾值
        self._rng = random.Random(seed)                  # 可重現的隨機數生成器
        self._max_reserve = max_reserve_per_tick         # 單 tick 最大保留電量上限
        self._max_solves = max_solves                    # 安全上限：避免無限重解
        self._max_deviation_triggers = max_deviation_triggers  # 偏離觸發點數量上限
        self._gap_rel = gap_rel                          # CBC MIP 相對 gap（20% 容許次優解）
        self._time_limit = time_limit                    # 每次求解的時間限制（秒）
        self._threads = threads
        self._verbose = verbose

        self._assets = assets
        self._tasks = tasks
        self._prices = prices

        # 再生能源不確定性邊際：applied 到尚未承諾的 tail（未來時槽的預測打折）
        # 已承諾時槽用 actual（實現值），不再打折，所以 _solve_relax 把 margin 歸零
        # 避免對實現值「打折兩次」（一次在 _round_assets，一次在 formulator）
        self._tail_margin = self._relax.renewable_uncertainty_margin
        self._solve_relax = self._relax.model_copy(
            update={"renewable_uncertainty_margin": 0.0}
        )

        # ── 滾動狀態變數（在 run() 中初始化）──────────────────────────────────
        self._raw: dict[int, dict[str, Any]] = {}      # 已承諾 tick 的 MILP 原始值（用於 pin_prefix）
        self._out: dict[int, dict[str, Any]] = {}       # 已承諾 tick 的輸出記錄（最終寫入 schedule_result）
        self._accepted: dict[str, dict[str, Any]] = {}  # 已接受但尚未全部路由的任務追蹤
        self._decided: set[str] = set()                 # 已做決策（接受 or 拒絕）的 task_id 集合
        self._actual_avail: dict[str, dict[int, float]] = {}  # 實現的 PV 可用率（帶隨機擾動）
        self._solves = 0                                # 已執行的 MILP 求解次數
        self._run_log: list[dict[str, Any]] = []        # 每個 round 的決策記錄
        self._job_log: dict[str, list[dict[str, Any]]] = {
            "sporadic": [],
            "aperiodic": [],
        }

    # ------------------------------------------------------------------ public

    def run(self) -> dict[str, Any]:
        """Runs the rolling-horizon schedule and returns the dynamic result.

        Returns:
            Dict with ``schedule_result`` (full-horizon records in the Level 1
            output schema), the acceptance-test ``log``, and a ``run_log`` capturing
            each re-optimization round.
        """
        self._load_data_if_needed()
        assert self._assets is not None and self._tasks is not None
        assert self._prices is not None

        # 展開 periodic tasks → ExpandedJob，同時建立 charging jobs
        expander = JobExpander(horizon=self._horizon)
        self._regular_jobs = expander.expand_periodic_tasks(self._tasks)
        self._charging_jobs = expander.expand_charging_jobs(self._assets)
        self._all_jobs = self._regular_jobs + self._charging_jobs

        # 若 RelaxationConfig 未設定 precedence，自動選一對可行的 job 當示範
        self._auto_select_precedence()
        # 為每台再生能源裝置模擬「實際發電量」（預測值 + 隨機擾動）
        self._build_actual_renewable()

        # 計算觸發點列表，每個觸發點是一個 round 的起始 tick
        boundaries = self._trigger_ticks()
        if self._verbose:
            print(
                f"Dynamic re-optimization (rolling horizon, {len(boundaries)} rounds)..."
            )
        # 依序執行每個 round：[t0, t1) 是本 round 承諾的時槽範圍
        for idx, t0 in enumerate(boundaries):
            t1 = boundaries[idx + 1] if idx + 1 < len(boundaries) else self._horizon + 1
            self._run_round(t0, t1, idx + 1, len(boundaries))

        # 組合最終輸出：schedule_result / acceptance log / run_log / realized PV
        return self._finalize()

    # -------------------------------------------------------------- data setup

    def _load_data_if_needed(self) -> None:
        """Loads inputs from disk when not injected."""
        if self._assets is None:
            self._assets = ProcessorSettingsSystem.load_from_json(
                self._processor_settings_path
            )
        if self._tasks is None:
            self._tasks = TaskSystem.load_from_json(self._task_set_path)
        if self._prices is None:
            self._prices = PriceSystem.load_from_json(self._price_path)

    def _auto_select_precedence(self) -> None:
        """Picks one feasible, non-trivial precedence pair when none was configured.

        A pair ``(a, b)`` is eligible when the windows overlap (so precedence is not
        already implied) yet ``a`` then ``b`` still fit before ``b``'s deadline.
        """
        # 已手動設定 precedence → 直接返回，不覆蓋
        if self._relax.precedence:
            return
        jobs = sorted(self._regular_jobs, key=lambda j: (j.release, j.deadline))
        for a in jobs:
            for b in jobs:
                if a.job_id == b.job_id:
                    continue
                # 條件 1：視窗有重疊（否則先後順序不言而喻，加約束沒意義）
                overlaps = a.deadline >= b.release and b.deadline >= a.release
                # 條件 2：a 先完整執行後，b 還能在自己的 deadline 前完成
                earliest_b = max(b.release, a.release + a.execution)
                fits = earliest_b + b.execution - 1 <= b.deadline
                if overlaps and fits:
                    # 找到第一組可行的 (a, b) → 設為 precedence 示範
                    self._relax.precedence = [(a.job_id, b.job_id)]
                    self._solve_relax.precedence = [(a.job_id, b.job_id)]
                    return

    def _build_actual_renewable(self) -> None:
        """Realizes actual PV availability around the forecast (seeded).

        使用乘法高斯雜訊模擬「實際發電比例」：
          actual = forecast × (1 + N(0, noise_std))  ，夾在 [0, 1]
        forecast=0 的時槽不加擾動（夜晚無太陽，不必模擬擾動）。
        用 seeded RNG 確保每次跑出相同結果（可重現）。
        """
        for rf in self._assets.renewable_forecasts:
            series: dict[int, float] = {}
            for f in rf.forecasts:
                if f.pv_forecast <= 0.0:
                    series[f.hour] = 0.0
                    continue
                # 乘法雜訊：實際值 = 預測值 × (1 + 高斯擾動)，夾在 [0, 1]
                noisy = f.pv_forecast * (1.0 + self._rng.gauss(0.0, self._noise_std))
                series[f.hour] = max(0.0, min(1.0, noisy))
            self._actual_avail[rf.renewable_id] = series

    def _forecast_of(self, rid: str, t: int) -> float:
        """Returns the day-ahead forecast fraction for a renewable at a tick."""
        for rf in self._assets.renewable_forecasts:
            if rf.renewable_id == rid:
                for f in rf.forecasts:
                    if f.hour == t:
                        return f.pv_forecast
        return 0.0

    def _trigger_ticks(self) -> list[int]:
        """Computes the sorted re-optimization trigger ticks.

        Triggers combine three sources: the periodic cadence, sporadic release ticks
        (hard jobs need prompt admission), and a capped number of ticks where the
        realized PV availability deviates most from the forecast. Aperiodic (soft)
        jobs are revealed at the next cadence boundary rather than triggering their
        own re-optimization, keeping the round count bounded.
        """
        # 觸發類型 1：固定週期（t=1, 1+interval, 1+2*interval, ...）
        triggers: set[int] = {1}
        t = 1 + self._reopt_interval
        while t <= self._horizon:
            triggers.add(t)
            t += self._reopt_interval

        # 觸發類型 2：Sporadic 任務的釋放時刻（事件驅動，確保及時決定接受/拒絕）
        for task in self._tasks.sporadic_tasks:
            if 1 <= task.r <= self._horizon:
                triggers.add(task.r)

        # 觸發類型 3：再生能源實現值大幅偏離預測的時刻（不確定性觸發）
        # 選偏差最大的 tick，但要和已有觸發點保持一定間距（避免重解太密）
        deviations: list[tuple[float, int]] = []
        for rid, series in self._actual_avail.items():
            for tk in range(1, self._horizon + 1):
                gap = abs(series.get(tk, 0.0) - self._forecast_of(rid, tk))
                if gap >= self._deviation_threshold:
                    deviations.append((gap, tk))
        deviations.sort(reverse=True)
        added = 0
        for _, tk in deviations:
            if added >= self._max_deviation_triggers:
                break
            # 與現有觸發點間距 ≥ interval/2，避免觸發點過密
            if all(abs(tk - x) >= self._reopt_interval // 2 for x in triggers):
                triggers.add(tk)
                added += 1

        return sorted(triggers)

    # ----------------------------------------------------------------- a round

    def _run_round(
        self, t0: int, t1: int, round_idx: int = 0, round_total: int = 0
    ) -> None:
        """Executes one re-optimization round committing block ``[t0, t1)``.

        Args:
            t0: Trigger tick; the pin boundary and start of the committed block.
            t1: Reveal boundary; the committed block is ``[t0, t1)``.
            round_idx: 1-based index of this round, for progress reporting.
            round_total: Total number of rounds, for progress reporting.
        """
        started = time.perf_counter()
        revealed = self._reveal_jobs(t1)
        formulator, rejected = self._admit_and_solve(revealed, t0, t1)
        self._capture_block(formulator, t0, t1)
        routed = self._route_accepted(t0, t1)

        admitted = sorted(jid for jid in self._accepted if jid not in rejected)
        if self._verbose:
            print(
                f"  round {round_idx}/{round_total} @ tick {t0}: "
                f"committed [{t0},{t1 - 1}], revealed {len(revealed)}, "
                f"admitted {len(admitted)}, rejected {len(rejected)} "
                f"({time.perf_counter() - started:.1f}s)"
            )

        self._run_log.append(
            {
                "trigger_tick": t0,
                "committed_range": [t0, t1 - 1],
                "revealed": [j.task_id for j in revealed],
                "admitted": admitted,
                "rejected": sorted(rejected),
                "routed_jobs": routed,
                "reserve_floor_total": round(
                    sum(self._reserve_floor(t0).values()), 2
                ),
                "solves_so_far": self._solves,
            }
        )

    def _reveal_jobs(self, t1: int) -> list[BaseRTTask]:
        """Returns sporadic/aperiodic jobs released before ``t1`` not yet decided."""
        revealed: list[BaseRTTask] = []
        for task in self._tasks.sporadic_tasks + self._tasks.aperiodic_tasks:
            if task.task_id in self._decided:
                continue
            if task.r < t1:
                revealed.append(task)
        return revealed

    def _admit_and_solve(
        self, revealed: list[BaseRTTask], t0: int, t1: int
    ) -> tuple[VppMilpFormulator, set[str]]:
        """Admits revealed jobs and solves the round in one feasible MILP solve.

        All revealed jobs are tentatively reserved and a single solve is attempted;
        while it is infeasible the lowest-priority job (soft before hard, heaviest
        first) is dropped and the solve retried. The successful formulator doubles as
        the committing solve, so a feasible round costs a single solve. Dropped
        sporadic jobs are rejected (hard miss); dropped aperiodic jobs are left
        unscheduled (soft miss).

        Returns:
            The solved formulator and the set of task IDs rejected this round.
        """
        rejected: set[str] = set()
        pending: list[str] = []
        # 按 deadline 排序，緊急任務優先處理
        for task in sorted(revealed, key=lambda t: t.r + t.d):
            self._decided.add(task.task_id)
            kind = "sporadic" if isinstance(task, SporadicTask) else "aperiodic"
            # sporadic：deadline = r + d - 1（硬 deadline，不能超過）
            # aperiodic：可延到 horizon 結束（軟 deadline）
            latest = (task.r + task.d - 1) if kind == "sporadic" else self._horizon
            decision = "rejected" if kind == "sporadic" else "missed"
            # 嘗試找 e 個可保留的 tick；若 deadline 前剩餘時槽不夠 → 立即拒絕
            ticks = self._reserve_window(task, t0, latest)
            if ticks is None:
                rejected.add(task.task_id)
                self._log_job(task, kind, decision, None, "no room before deadline")
                continue
            # 暫時接受：把任務加入 _accepted，設定保留時槽
            self._accepted[task.task_id] = self._tracking(task, kind, ticks)
            pending.append(task.task_id)

        # 嘗試帶保留約束的求解（Sell[t] ≥ reserve_floor[t]）
        formulator = self._solve(self._reserve_floor(t0), t0, t1)
        # 若求解失敗（infeasible），逐一丟棄優先級最低的任務後重試
        # 優先丟棄：aperiodic（軟 deadline）> sporadic（硬 deadline）；重量最重者先丟
        while formulator is None and pending:
            worst = self._drop_priority(pending)
            job = self._accepted[worst]
            kind = job["kind"]
            pending.remove(worst)
            del self._accepted[worst]
            rejected.add(worst)
            self._log_job(
                job["task"], kind, "rejected" if kind == "sporadic" else "missed",
                None, "reservation infeasible: cannot hold reserve over window",
            )
            formulator = self._solve(self._reserve_floor(t0), t0, t1)

        # 萬一完全沒有保留時也求解失敗，強制無保留求解（不計入 solve 上限）
        if formulator is None:
            formulator = self._solve({}, t0, t1, force=True)
        assert formulator is not None, "no feasible schedule even without reservations"
        return formulator, rejected

    def _drop_priority(self, pending: list[str]) -> str:
        """Selects the job to drop: soft (aperiodic) before hard, heaviest first."""
        soft = [j for j in pending if self._accepted[j]["kind"] == "aperiodic"]
        pool = soft or pending
        return max(
            pool,
            key=lambda j: self._accepted[j]["w"] * self._accepted[j]["task"].e,
        )

    def _tracking(
        self, task: BaseRTTask, kind: str, ticks: list[int]
    ) -> dict[str, Any]:
        """Builds the rolling tracking record for an admitted job."""
        return {
            "task": task,
            "kind": kind,
            "w": task.w,
            "remaining": task.e,
            "preemptive": task.preempt == 1,
            "deadline": task.r + task.d - 1,
            "reserve_ticks": ticks,
            "routed_ticks": [],
        }

    def _reserve_window(
        self, task: BaseRTTask, t0: int, latest: int
    ) -> list[int] | None:
        """Returns the earliest ``e`` ticks to reserve for a job, or None if infeasible.

        Reserving the earliest contiguous block satisfies both preemptive and
        non-preemptive jobs. Returns None when fewer than ``e`` ticks remain.
        """
        start = max(task.r, t0)
        if latest - start + 1 < task.e:
            return None
        return list(range(start, start + task.e))

    def _reserve_floor(self, t0: int) -> dict[int, float]:
        """Computes the per-tick reservation floor from accepted, unfinished jobs.

        For each accepted job the earliest ``remaining`` uncommitted ticks are
        reserved; the floor at a tick is the sum of demands of jobs reserving it,
        capped to keep the solve feasible.

        這個 floor 就是傳給 VppMilpFormulator 的 reserve_floor 參數，
        對應的 MILP 約束是 Sell[t] ≥ floor[t]。
        等到 _route_accepted() 時，再把 floor 中的電量從 Sell 「轉移」給 job 的 k 變數。
        """
        floor: dict[int, float] = {}
        for job in self._accepted.values():
            if job["remaining"] <= 0:
                continue  # 已完全路由的任務不需要再保留
            latest = job["deadline"] if job["kind"] == "sporadic" else self._horizon
            start = max(job["task"].r, t0)
            # 選最早的 remaining 個 tick 當保留時槽
            ticks = list(range(start, start + job["remaining"]))
            if ticks and ticks[-1] > latest:
                continue  # 超過 deadline 的保留方案無效，跳過
            job["reserve_ticks"] = ticks
            for tk in ticks:
                # 累加同一 tick 上所有任務的需求，並套用上限避免過度保留
                floor[tk] = min(self._max_reserve, floor.get(tk, 0.0) + job["w"])
        return floor

    def _solve(
        self,
        reserve_floor: dict[int, float],
        upto_tick: int,
        commit_hi: int,
        force: bool = False,
    ) -> VppMilpFormulator | None:
        """Builds, pins and solves the relaxed MILP for the remaining horizon.

        Args:
            reserve_floor: Per-tick ``Sell`` floor enforcing held reserve.
            upto_tick: Ticks below this are pinned to committed values (the pin
                boundary ``t0``).
            commit_hi: Reveal boundary ``t1``; ticks below it use the realized PV
                availability (the block about to be committed is planned against
                reality), ticks at or beyond it use the derated forecast.
            force: When True the safety solve cap is ignored.

        Returns:
            The solved formulator on success, or None when infeasible / over budget.
        """
        if self._solves >= self._max_solves and not force:
            return None  # 超過安全上限，不再重解
        self._solves += 1

        # 為本 round 準備資產：[t0, t1) 用實現值，[t1, horizon] 用打折後預測
        round_assets = self._round_assets(commit_hi)
        formulator = VppMilpFormulator(
            assets=round_assets,
            prices=self._prices,
            all_jobs=self._all_jobs,
            horizon=self._horizon,
            relaxation=self._solve_relax,   # 已把 renewable_uncertainty_margin 歸零
            reserve_floor=reserve_floor,     # Sell[t] ≥ floor[t] 保留約束
        )
        formulator.formulate()
        if self._raw:
            # 凍結已承諾的前綴（tol=1e-3 比 default 1e-6 寬鬆，
            # 容許 CBC gap 求解 + 浮點殘差，避免 presolve 把近似可行解認定為 infeasible）
            formulator.pin_prefix(self._raw, upto_tick, tol=1e-3)
        # 強制單執行緒：CBC 多執行緒 + timeLimit 在 Windows 上會卡死（worker 不退出）
        # 單執行緒同時保證每次求解結果完全可重現
        formulator.prob.solve(
            pulp.PULP_CBC_CMD(
                msg=0,
                gapRel=self._gap_rel,    # 允許 gap_rel 內的次優解（加速求解）
                timeLimit=self._time_limit,  # 超時就用目前最佳解
                threads=1,
            )
        )
        # CBC 狀態：Optimal = 找到可行解（在 gap 或時限內）；Infeasible = 無可行解
        if pulp.LpStatus[formulator.prob.status] != "Optimal":
            return None
        return formulator

    def _round_assets(self, commit_hi: int) -> ProcessorSettingsSystem:
        """Returns an assets copy whose PV forecasts reflect realized/derated values.

        Ticks before ``commit_hi`` use the realized availability (revealed reality);
        later ticks use the forecast derated by the uncertainty margin. Fresh forecast
        objects are constructed from the pristine originals, so ``self._assets`` is
        never mutated across rounds.

        【兩段式預測策略】
        - t < commit_hi（本 round 承諾範圍）：用實現值（actual），精確反映現實
        - t ≥ commit_hi（尚未揭露的未來）：用預測值 × (1 - tail_margin)，保留 headroom
        這樣 MILP 在規劃未來時更保守，實際輸出低時不會缺電。
        """
        keep = 1.0 - self._tail_margin  # 未來時槽的折扣因子
        new_forecasts = []
        for rf in self._assets.renewable_forecasts:
            actual = self._actual_avail.get(rf.renewable_id, {})
            hourly = [
                HourlyForecast(
                    hour=f.hour,
                    pv_forecast=(
                        actual.get(f.hour, f.pv_forecast)  # 已揭露 → 實現值
                        if f.hour < commit_hi
                        else f.pv_forecast * keep            # 未揭露 → 打折預測
                    ),
                )
                for f in rf.forecasts
            ]
            new_forecasts.append(
                RenewableForecast(renewable_id=rf.renewable_id, forecasts=hourly)
            )
        # model_copy 不修改原本的 self._assets，確保跨 round 的獨立性
        return self._assets.model_copy(update={"renewable_forecasts": new_forecasts})

    # ------------------------------------------------------------- commit/route

    def _capture_block(self, formulator: VppMilpFormulator, t0: int, t1: int) -> None:
        """Stores raw and rounded committed records for ticks ``[t0, t1)``."""
        for t in range(t0, t1):
            raw = self._read_tick(formulator, t, rounded=False)
            self._raw[t] = raw
            self._out[t] = self._read_tick(formulator, t, rounded=True)

    def _read_tick(
        self, formulator: VppMilpFormulator, t: int, rounded: bool
    ) -> dict[str, Any]:
        """Reads one tick's decision values from the solved formulator.

        When ``rounded`` the record is the output form (values rounded, zeros
        dropped); otherwise it is the raw form used for pinning (full precision,
        only sub-nanowatt noise dropped). Missing entries pin to zero, so dropping
        negligible values is safe.
        """
        drop = self._eps if rounded else 1e-9

        def val(v: float | None) -> float:
            if v is None:
                return 0.0
            return (0.0 if abs(v) < self._eps else round(v, 4)) if rounded else float(v)

        p_dict: dict[str, float] = {}
        for i in formulator.all_device_ids:
            v = val(pulp.value(formulator.P[i][t]))
            if abs(v) > drop:
                p_dict[i] = v

        k_dict: dict[str, dict[str, float]] = {}
        for job in self._all_jobs:
            allowed = (
                formulator.gen_ren_ids if job.is_charging else formulator.all_device_ids
            )
            alloc: dict[str, float] = {}
            for i in allowed:
                if t in formulator.k[job.job_id].get(i, {}):
                    v = val(pulp.value(formulator.k[job.job_id][i][t]))
                    if abs(v) > drop:
                        alloc[i] = v
            if alloc:
                k_dict[job.job_id] = alloc

        soc_dict = {
            i: val(pulp.value(formulator.SOC[i][t])) for i in formulator.sto_ids
        }
        record: dict[str, Any] = {
            "t": t,
            "P": p_dict,
            "k": k_dict,
            "sell": val(pulp.value(formulator.Sell[t])),
            "soc": soc_dict,
        }
        if rounded:
            record.update(
                accepted_sporadic=[],
                scheduled_aperiodic=[],
                missed_aperiodic=[],
                rejected_sporadic=[],
            )
        return record

    def _route_accepted(self, t0: int, t1: int) -> list[str]:
        """Routes accepted jobs into their reserved ticks inside ``[t0, t1)``.

        Returns:
            The IDs of jobs that received any allocation this round.
        """
        routed_jobs: list[str] = []
        for jid, job in self._accepted.items():
            placed = False
            for tk in list(job["reserve_ticks"]):
                if not (t0 <= tk < t1):
                    continue  # 只處理本 round 承諾範圍內的保留時槽
                # 嘗試從本 tick 的裝置剩餘電量（spare）中取出 w MWh
                if self._route_one(tk, jid, job["w"]):
                    # 在輸出記錄上標記（accepted_sporadic 或 scheduled_aperiodic）
                    field = (
                        "accepted_sporadic"
                        if job["kind"] == "sporadic"
                        else "scheduled_aperiodic"
                    )
                    self._mark(tk, field, jid)
                    job["routed_ticks"].append(tk)
                    job["remaining"] -= 1  # 剩餘需要路由的 tick 數減 1
                    placed = True
            if placed:
                routed_jobs.append(jid)
        return routed_jobs

    def _route_one(self, tick: int, job_id: str, demand: float) -> bool:
        """Routes ``demand`` MWh from device spare into ``k`` at a committed tick.

        Mirrors the Level 1 acceptance routing: pulls from per-device spare, records
        the allocation and reduces ``sell`` so power balance (C23) is preserved.
        """
        record = self._out.get(tick)
        if record is None:
            return False
        # spare = 裝置總輸出 P[i][t] 減去已分配給其他 job 的部分
        # 這些 spare 對應到 MILP 裡的 Sell[t]（因為 C23：supply = k_total + Sell）
        spare = self._device_spare(record)
        remaining = float(demand)
        alloc = record["k"].setdefault(job_id, {})
        # 從可用裝置中依序取電，直到滿足 demand
        for dev in list(spare):
            if remaining <= _EPS:
                break
            give = min(spare[dev], remaining)
            if give <= _EPS:
                continue
            alloc[dev] = round(alloc.get(dev, 0.0) + give, 4)
            remaining = round(remaining - give, 4)
        if remaining > _EPS:
            # reserve_floor 應該已保證足夠的 spare，此處為安全回退
            if not alloc:
                record["k"].pop(job_id, None)
            return False
        # 把「從 Sell 取走的電量」反映到輸出記錄（維持 C23 電力平衡）
        record["sell"] = round(float(record.get("sell", 0.0)) - demand, 4)
        return True

    def _device_spare(self, record: dict[str, Any]) -> dict[str, float]:
        """Returns per-device output not yet routed to any job at a tick."""
        routed: dict[str, float] = {}
        for alloc in record.get("k", {}).values():
            for dev, val in alloc.items():
                routed[dev] = routed.get(dev, 0.0) + float(val)
        return {
            dev: max(0.0, round(float(p) - routed.get(dev, 0.0), 4))
            for dev, p in record.get("P", {}).items()
        }

    def _mark(self, tick: int, field: str, job_id: str) -> None:
        """Adds a job ID to an output-record annotation field."""
        record = self._out.get(tick)
        if record is not None and job_id not in record[field]:
            record[field].append(job_id)

    # ---------------------------------------------------------------- finalize

    def _finalize(self) -> dict[str, Any]:
        """Assembles the full-horizon schedule, acceptance log and run log."""
        # Log accepted jobs and flag soft misses by completion time.
        for jid, job in self._accepted.items():
            completion = max(job["routed_ticks"]) if job["routed_ticks"] else None
            dl = job["deadline"]
            if job["kind"] == "sporadic":
                self._log_job(
                    job["task"], "sporadic", "accepted", completion,
                    f"routed {job['task'].e} slot(s) within hard deadline {dl}",
                )
            else:
                late = completion is None or completion > dl
                self._log_job(
                    job["task"], "aperiodic",
                    "missed" if late else "scheduled", completion,
                    "completes after soft deadline" if late else "within soft deadline",
                )
                if late and completion is not None:
                    self._mark(job["task"].r, "missed_aperiodic", jid)

        # Mark rejected sporadic / missed aperiodic on the record at their release,
        # so the evaluator counts them (it reads these annotation fields).
        for entry in self._job_log["sporadic"]:
            if entry["decision"] == "rejected":
                self._mark(entry["release"], "rejected_sporadic", entry["task_id"])
        for entry in self._job_log["aperiodic"]:
            if entry["decision"] == "missed":
                self._mark(entry["release"], "missed_aperiodic", entry["task_id"])

        schedule = [self._out[t] for t in sorted(self._out)]
        return {
            "schedule_result": schedule,
            "log": self._compose_log(),
            "run_log": self._run_log,
            # Exported so the Level 2 self-check can verify the dynamic schedule:
            # the realized PV availability bounds renewable output per committed
            # tick, and the precedence pairs are auto-selected at runtime.
            "realized_renewable": {
                rid: {str(h): round(v, 6) for h, v in series.items()}
                for rid, series in self._actual_avail.items()
            },
            "precedence": [list(pair) for pair in self._relax.precedence],
        }

    def _log_job(
        self,
        task: BaseRTTask,
        kind: str,
        decision: str,
        completion: int | None,
        reason: str,
    ) -> None:
        """Appends a per-job decision record to the acceptance log."""
        entry: dict[str, Any] = {
            "task_id": task.task_id,
            "release": task.r,
            "relative_deadline": task.d,
            "execution": task.e,
            "demand": task.w,
            "preemptive": task.preempt == 1,
            "decision": decision,
            "completion_tick": completion,
            "reason": reason,
        }
        if kind == "sporadic":
            entry["absolute_deadline"] = task.r + task.d - 1
        else:
            entry["soft_deadline"] = task.r + task.d - 1
            entry["missed"] = decision != "scheduled"
        # Replace any earlier provisional entry for this task (admission may revise).
        self._job_log[kind] = [
            e for e in self._job_log[kind] if e["task_id"] != task.task_id
        ]
        self._job_log[kind].append(entry)

    def _compose_log(self) -> dict[str, Any]:
        """Builds the acceptance-test log with a summary and per-job records."""
        sporadic = self._job_log["sporadic"]
        aperiodic = self._job_log["aperiodic"]
        accepted = [e for e in sporadic if e["decision"] == "accepted"]
        total_exec = sum(e["execution"] for e in sporadic)
        done_exec = sum(
            e["execution"]
            for e in accepted
            if e["completion_tick"] is not None
            and e["completion_tick"] <= e["absolute_deadline"]
        )
        value_rate = round(done_exec / total_exec, 4) if total_exec else 0.0

        return {
            "summary": {
                "horizon": self._horizon,
                "method": "rolling-horizon re-optimization",
                "reopt_solves": self._solves,
                "reopt_rounds": len(self._run_log),
                "sporadic": {
                    "total": len(sporadic),
                    "accepted": len(accepted),
                    "rejected": len(sporadic) - len(accepted),
                },
                "aperiodic": {
                    "total": len(aperiodic),
                    "scheduled": sum(
                        1 for e in aperiodic if e["decision"] == "scheduled"
                    ),
                    "missed": sum(1 for e in aperiodic if e.get("missed")),
                },
                "sporadic_value_rate": value_rate,
            },
            "sporadic": sporadic,
            "aperiodic": aperiodic,
        }


if __name__ == "__main__":
    # Standalone Level 2 entry: dynamic schedule + evaluation on the existing
    # task set. (Deferred import keeps src.main / src.advanced_scheduler acyclic.)
    from src.main import run_advanced_scheduler

    run_advanced_scheduler()
