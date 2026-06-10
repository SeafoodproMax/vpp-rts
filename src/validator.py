"""Self-grading validator for Level 1 of the VPP-RTS assignment.

This module re-reads the produced JSON artifacts (``task_set.json``,
``schedule_result.json`` and ``evaluation_results.json``) together with the
fixed inputs and checks them against the Level 1 grading rubric. It is
intentionally written with the standard library only (``json`` / ``math``) so it
stays independent of the scheduler and evaluator under test and can run without
the project's heavy dependencies.

Coverage by rubric item:
    * Items 1, 2, 3 -- fully auto-checked (task-set design, model constraints,
      schedule results & periodic performance).
    * Item 4 (acceptance test) -- 4-3 sporadic value rate is recomputed from the
      schedule and scored by the rubric thresholds; 4-1 / 4-2 are report-graded
      and reported as SKIP.
    * Item 5 (evaluation metrics) -- 5-1..5-5 are independently recomputed from
      the schedule and cross-checked against ``evaluation_results.json``.
    * Item 6 (reserve-strategy analysis) -- 6-1 / 6-2 are report-graded and
      reported as SKIP.

SKIP items (report-graded sub-items, or checks whose inputs are absent) are
excluded from the self-grade total.

【使用方式】：
    python -m src.validator                    # Level 1 自動評分
    python -m src.validator --level 2          # Level 2 自動評分（動態排程）

【評分邏輯】：
    每個 CheckResult 包含 item（子項目編號）、max_score（滿分）、
    score（實際得分）和 violations（違規說明）。
    PASS → score = max_score；FAIL → score = 0；SKIP → 不計入總分。

Run with::

    python -m src.validator
    python -m src.validator --task-set output/task_set.json \\
        --schedule output/schedule_result.json \\
        --settings input/processor_settings.json \\
        --evaluation output/evaluation_results.json
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from typing import Any


# 浮點數比較容差：Extractor 輸出捨入到 4 位小數，單個 tick 的誤差很小；
# 但 SOC 平衡誤差會沿 72 個 tick 累積，所以用稍寬的 1e-2 仍能抓到整 MWh 的違規
_EPS = 1e-2
_HORIZON = 72  # 排程期間：72 小時（ticks）


@dataclass
class CheckResult:
    """Outcome of a single rubric sub-item check.

    Attributes:
        item: Rubric sub-item id (e.g. ``"2-1"``).
        desc: Human-readable description.
        max_score: Maximum points for this sub-item.
        score: Awarded points after applying violations.
        status: One of ``PASS`` / ``FAIL`` / ``SKIP``.
        violations: Human-readable violation messages.
    """

    item: str
    desc: str
    max_score: float
    score: float = 0.0
    status: str = "PASS"
    violations: list[str] = field(default_factory=list)


@dataclass
class _Job:
    """A concrete expanded periodic job instance."""

    job_id: str
    task_id: str
    release: int
    deadline: int
    execution: int
    demand: int
    preemptive: bool


def _pstdev(values: list[int]) -> float:
    """Returns the population standard deviation of ``values``.

    Args:
        values: Sample values (at least one element).

    Returns:
        The population standard deviation.
    """
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def _load_json(path: str) -> Any:
    """Loads a JSON file, surfacing parse errors clearly.

    Args:
        path: File path to read.

    Returns:
        The parsed JSON content.

    Raises:
        SystemExit: If the file is missing or is not valid JSON.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"[FATAL] file not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[FATAL] {path} is not valid JSON: {exc}")


def _load_json_optional(path: str) -> Any | None:
    """Loads a JSON file if present, returning ``None`` when it is missing.

    Args:
        path: File path to read.

    Returns:
        The parsed JSON content, or ``None`` if the file does not exist.

    Raises:
        SystemExit: If the file exists but is not valid JSON.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[FATAL] {path} is not valid JSON: {exc}")


class Level1Validator:
    """Validates Level 1 rubric items against the produced JSON artifacts."""

    def __init__(
        self,
        task_set: dict[str, Any],
        schedule: dict[str, Any],
        settings: dict[str, Any],
        evaluation: dict[str, Any] | None = None,
        horizon: int = _HORIZON,
        eps: float = _EPS,
    ) -> None:
        """Initializes the validator with already-loaded JSON dictionaries.

        Args:
            task_set: Parsed ``task_set.json`` content.
            schedule: Parsed ``schedule_result.json`` content.
            settings: Parsed ``processor_settings.json`` content.
            evaluation: Parsed ``evaluation_results.json`` content, or ``None``
                when the file is absent (item 5 checks then report as SKIP).
            horizon: Scheduling horizon length in ticks.
            eps: Floating-point comparison tolerance.
        """
        self._eps = eps
        self._horizon = horizon
        self._evaluation = evaluation

        self._frame_size = task_set.get("frame_size")
        self._tasks: dict[str, dict[str, Any]] = task_set.get("periodic", {})
        self._sporadic_tasks: dict[str, dict[str, Any]] = task_set.get(
            "sporadic", {}
        )
        self._aperiodic_tasks: dict[str, dict[str, Any]] = task_set.get(
            "aperiodic", {}
        )

        # Index schedule ticks by t for O(1) lookup.
        self._ticks: dict[int, dict[str, Any]] = {
            rec["t"]: rec for rec in schedule.get("schedule_result", [])
        }

        self._parse_settings(settings)
        self._jobs = self._expand_periodic_jobs()

    # ------------------------------------------------------------------ setup

    def _parse_settings(self, s: dict[str, Any]) -> None:
        """Extracts device ids, parameters and forecasts from settings."""
        self._gen = {g["generator_id"]: g for g in s.get("generator", [])}
        self._sto = {b["storage_id"]: b for b in s.get("storage", [])}
        self._ren_cap = {
            r["renewable_id"]: r["capacity"] for r in s.get("renewable_capacity", [])
        }

        self._ren_forecast: dict[str, dict[int, float]] = {}
        for entry in s.get("renewable_forecast", []):
            for ren_id, hourly in entry.items():
                self._ren_forecast[ren_id] = {
                    h["hour"]: h["pv_forecast"] for h in hourly
                }

        # storage_id -> charging job_id
        self._chg_by_sto = {
            cj["target_storage"]: cj["job_id"] for cj in s.get("charging_jobs", [])
        }
        self._chg_job_ids = {cj["job_id"] for cj in s.get("charging_jobs", [])}

        self._gen_ids = list(self._gen)
        self._ren_ids = list(self._ren_cap)
        self._sto_ids = list(self._sto)
        self._gen_ren_ids = self._gen_ids + self._ren_ids
        self._all_device_ids = self._gen_ids + self._ren_ids + self._sto_ids

    def _expand_periodic_jobs(self) -> list[_Job]:
        """Expands periodic tasks into concrete jobs (mirrors JobExpander)."""
        jobs: list[_Job] = []
        for tid, t in self._tasks.items():
            k = 1  # 1-indexed instance numbering (p1_1 is the first job)
            while True:
                rel = t["r"] + (k - 1) * t["p"]
                if rel > self._horizon:
                    break
                dl = rel + t["d"] - 1
                window_end = min(dl, self._horizon)
                if window_end - rel + 1 < t["e"]:
                    # truncated tail window cannot fit e ticks; later ones are worse
                    break
                jobs.append(
                    _Job(
                        job_id=f"{tid}_{k}",
                        task_id=tid,
                        release=rel,
                        deadline=window_end,
                        execution=t["e"],
                        demand=t["w"],
                        preemptive=(t["preempt"] == 1),
                    )
                )
                k += 1
        return jobs

    def _expand_sporadic_jobs(self) -> list[_Job]:
        """Expands sporadic tasks into single-instance jobs (mirrors JobExpander).

        ``deadline`` is the absolute hard deadline ``r + d - 1``.
        """
        jobs: list[_Job] = []
        for tid, t in self._sporadic_tasks.items():
            if t["r"] > self._horizon:
                continue
            jobs.append(
                _Job(
                    job_id=tid,
                    task_id=tid,
                    release=t["r"],
                    deadline=t["r"] + t["d"] - 1,
                    execution=t["e"],
                    demand=t["w"],
                    preemptive=(t["preempt"] == 1),
                )
            )
        return jobs

    def _expand_aperiodic_jobs(self) -> list[_Job]:
        """Expands aperiodic tasks into single-instance jobs (mirrors JobExpander).

        ``deadline`` holds the soft deadline; the execution window itself runs
        to the horizon end since aperiodic jobs may complete late.
        """
        jobs: list[_Job] = []
        for tid, t in self._aperiodic_tasks.items():
            if t["r"] > self._horizon:
                continue
            jobs.append(
                _Job(
                    job_id=tid,
                    task_id=tid,
                    release=t["r"],
                    deadline=t["r"] + t["d"] - 1,
                    execution=t["e"],
                    demand=t["w"],
                    preemptive=(t["preempt"] == 1),
                )
            )
        return jobs

    # --------------------------------------------------------------- helpers

    def _P(self, dev: str, t: int) -> float:
        """Returns device output P[dev][t], defaulting to 0."""
        return float(self._ticks.get(t, {}).get("P", {}).get(dev, 0.0))

    def _sell(self, t: int) -> float:
        """Returns sell[t], defaulting to 0."""
        return float(self._ticks.get(t, {}).get("sell", 0.0))

    def _soc(self, sto: str, t: int) -> float:
        """Returns SOC[sto][t], defaulting to 0."""
        return float(self._ticks.get(t, {}).get("soc", {}).get(sto, 0.0))

    def _k_job_total(self, job_id: str, t: int) -> float:
        """Returns total energy routed to job_id at t across all devices."""
        alloc = self._ticks.get(t, {}).get("k", {}).get(job_id, {})
        return float(sum(alloc.values()))

    def _k_dev_total(self, dev: str, t: int) -> float:
        """Returns total energy supplied by device dev at t across all jobs."""
        total = 0.0
        for alloc in self._ticks.get(t, {}).get("k", {}).values():
            total += float(alloc.get(dev, 0.0))
        return total

    def _charge_in(self, sto: str, t: int) -> float:
        """Returns total energy charged into storage sto at t."""
        chg_job = self._chg_by_sto.get(sto)
        if chg_job is None:
            return 0.0
        alloc = self._ticks.get(t, {}).get("k", {}).get(chg_job, {})
        return float(sum(alloc.values()))

    def _completion_times(self) -> dict[str, int]:
        """Returns the last tick at which each job receives energy.

        A job is active at tick ``t`` when any device allocates positive energy
        to it. Iterating ticks in ascending order means the final assignment
        wins, giving the completion time used for deadline / response metrics.
        """
        completion: dict[str, int] = {}
        for t in range(1, self._horizon + 1):
            for job_id, alloc in self._ticks.get(t, {}).get("k", {}).items():
                if any(float(v) > self._eps for v in alloc.values()):
                    completion[job_id] = t
        return completion

    def _executed_slot_counts(self) -> dict[str, int]:
        """Returns the number of ticks at which each job receives energy.

        A job is completed only when its executed slot count reaches its
        execution time ``e`` (matching the external checker's convention).
        """
        counts: dict[str, int] = {}
        for t in range(1, self._horizon + 1):
            for job_id, alloc in self._ticks.get(t, {}).get("k", {}).items():
                if any(float(v) > self._eps for v in alloc.values()):
                    counts[job_id] = counts.get(job_id, 0) + 1
        return counts

    def _collect_field(self, field_name: str) -> set[str]:
        """Returns the union of a per-tick list field across all schedule ticks."""
        collected: set[str] = set()
        for rec in self._ticks.values():
            collected.update(rec.get(field_name, []))
        return collected

    # ------------------------------------------------------------- item 1

    def check_item1(self) -> list[CheckResult]:
        """Checks rubric item 1 (periodic task set design, 17 pts)."""
        out: list[CheckResult] = []
        tasks = self._tasks
        n = len(tasks)

        # 1-1: required fields present.
        r = CheckResult("1-1", "task fields r/p/e/d/w/preempt present", 3)
        required = {"r", "p", "e", "d", "w", "preempt"}
        for tid, t in tasks.items():
            missing = required - set(t)
            if missing:
                r.violations.append(f"{tid}: missing fields {sorted(missing)}")
        self._finalize(r)
        out.append(r)

        # 1-2: 6 <= |Jp| <= 10
        r = CheckResult("1-2", "6 <= task count <= 10", 2)
        if not (6 <= n <= 10):
            r.violations.append(f"task count = {n}")
        self._finalize(r)
        out.append(r)

        # 1-3: expanded periodic jobs > 30
        r = CheckResult("1-3", "expanded periodic jobs > 30", 2)
        job_count = len(self._jobs)
        if not job_count > 30:
            r.violations.append(f"expanded job count = {job_count}")
        r.desc += f" (got {job_count})"
        self._finalize(r)
        out.append(r)

        # 1-4: parameter ranges (all-or-nothing).
        r = CheckResult("1-4", "parameter ranges", 2)
        periods = {t["p"] for t in tasks.values()}
        if len(periods) < 3:
            r.violations.append(f"distinct periods = {len(periods)} (<3)")
        if sum(1 for t in tasks.values() if t["e"] == 2) < 2:
            r.violations.append("fewer than 2 tasks with e==2")
        if sum(1 for t in tasks.values() if t["e"] >= 3) < 1:
            r.violations.append("no task with e>=3")
        if sum(1 for t in tasks.values() if t["w"] >= 14) < 2:
            r.violations.append("fewer than 2 tasks with w>=14")
        for tid, t in tasks.items():
            if not (1 <= t["r"] <= t["p"]):
                r.violations.append(f"{tid}: r={t['r']} not in [1, p={t['p']}]")
            if not (6 <= t["p"] <= 24):
                r.violations.append(f"{tid}: p={t['p']} not in [6,24]")
            if not (1 <= t["e"] <= 4):
                r.violations.append(f"{tid}: e={t['e']} not in [1,4]")
            if not (t["e"] <= t["d"] <= t["p"]):
                r.violations.append(f"{tid}: d={t['d']} not in [e,p]")
            if not (6 <= t["w"] <= 18):
                r.violations.append(f"{tid}: w={t['w']} not in [6,18]")
        self._finalize(r)
        out.append(r)

        # 1-5: density >= 0.7
        r = CheckResult("1-5", "workload density >= 0.7", 2)
        density = sum(t["e"] / t["p"] for t in tasks.values()) if tasks else 0.0
        if density < 0.7:
            r.violations.append(f"density = {density:.3f}")
        r.desc += f" (got {density:.3f})"
        self._finalize(r)
        out.append(r)

        # 1-6: >= 20% tasks with d == e
        r = CheckResult("1-6", ">=20% tasks with d==e", 2)
        d_eq_e = sum(1 for t in tasks.values() if t["d"] == t["e"])
        if n == 0 or d_eq_e / n < 0.2:
            r.violations.append(f"d==e tasks = {d_eq_e}/{n}")
        self._finalize(r)
        out.append(r)

        # 1-7: >= 2 non-preemptive tasks with e != 1
        r = CheckResult("1-7", ">=2 non-preemptive tasks with e!=1", 2)
        np_tasks = sum(
            1 for t in tasks.values() if t["preempt"] == 0 and t["e"] != 1
        )
        if np_tasks < 2:
            r.violations.append(f"non-preemptive (e!=1) tasks = {np_tasks}")
        self._finalize(r)
        out.append(r)

        # 1-8: frame size validity (all-or-nothing).
        r = CheckResult("1-8", "frame size validity", 2)
        f = self._frame_size
        if not isinstance(f, int):
            r.violations.append(f"frame_size missing or not int: {f!r}")
        else:
            max_e = max((t["e"] for t in tasks.values()), default=0)
            if f < max_e:
                r.violations.append(f"f={f} < max(e)={max_e}")
            if self._horizon % f != 0:
                r.violations.append(f"{self._horizon} % f={f} != 0")
            for tid, t in tasks.items():
                if 2 * f - math.gcd(f, t["p"]) > t["d"]:
                    r.violations.append(
                        f"{tid}: 2f-gcd(f,p)={2 * f - math.gcd(f, t['p'])} > d={t['d']}"
                    )
        self._finalize(r)
        out.append(r)
        return out

    # ------------------------------------------------------------- item 2

    def check_item2(self) -> list[CheckResult]:
        """Checks rubric item 2 (model constraints, 27 pts)."""
        out: list[CheckResult] = []

        # 2-1 (5 pts): C1, C2, C3, C5, C20 -- one point off per violated class.
        v = {
            "C1": self._c1_demand(),
            "C2": self._c2_release(),
            "C3": self._c3_execution(),
            "C5": self._c5_non_preemptive(),
            "C20": self._c20_device_alloc(),
        }
        out.append(self._grouped("2-1", "base constraints C1,C2,C3,C5,C20", 5, v))

        # 2-2 (2 pts): C4 aperiodic miss definition.
        out.append(self._c4_aperiodic())

        # 2-3 (7 pts): C6..C12 generator constraints.
        v = {
            "C6": self._c6_output_bounds(),
            "C7": self._c7_ramp(),
            "C8": self._c8_min_ramp_feasibility(),
            "C9": self._c9_min_up(),
            "C10": self._c10_min_down(),
            "C11": self._c11_initial_up(),
            "C12": self._c12_initial_down(),
        }
        out.append(self._grouped("2-3", "generator constraints C6..C12", 7, v))

        # 2-4 (1 pt): C13 renewable cap.
        v = {"C13": self._c13_renewable()}
        out.append(self._grouped("2-4", "renewable cap C13", 1, v))

        # 2-5 (7 pts): C14..C19, C21 storage constraints.
        v = {
            "C14": self._c14_discharge_cap(),
            "C15": self._c15_charge_cap(),
            "C16": self._c16_soc_balance(),
            "C17": self._c17_soc_bounds(),
            "C18": self._c18_discharge_vs_soc(),
            "C19": self._c19_no_simultaneous(),
            "C21": self._c21_charge_source(),
        }
        out.append(self._grouped("2-5", "storage constraints C14..C19,C21", 7, v))

        # 2-6 (1 pt): C22 sell >= 0.
        v = {"C22": self._c22_sell_nonneg()}
        out.append(self._grouped("2-6", "sell >= 0 (C22)", 1, v))

        # 2-7 (4 pts): C23 power balance, -0.5 per violated hour.
        out.append(self._c23_balance())
        return out

    # ------------------------------------------------------------- item 3

    def check_item3(self) -> list[CheckResult]:
        """Checks rubric item 3 (schedule result & periodic performance, 8 pts)."""
        out: list[CheckResult] = []

        # 3-1 (2 pts): output structure.
        r = CheckResult("3-1", "schedule_result structure & 72 ticks", 2)
        if len(self._ticks) != self._horizon:
            r.violations.append(f"tick count = {len(self._ticks)} (expected 72)")
        req = {"t", "P", "k", "sell", "soc", "missed_aperiodic", "rejected_sporadic"}
        for t in range(1, self._horizon + 1):
            rec = self._ticks.get(t)
            if rec is None:
                r.violations.append(f"missing tick t={t}")
                continue
            missing = req - set(rec)
            if missing:
                r.violations.append(f"t={t}: missing keys {sorted(missing)}")
        self._finalize(r)
        out.append(r)

        # 3-2 (2 pts): each periodic job runs exactly e ticks, each supplying w.
        r = CheckResult("3-2", "periodic jobs: exec==e and each step supplies w", 2)
        for job in self._jobs:
            active, partial = self._active_ticks(job)
            if len(active) != job.execution:
                r.violations.append(
                    f"{job.job_id}: active ticks {len(active)} != e {job.execution}"
                )
            if partial:
                r.violations.append(
                    f"{job.job_id}: partial supply (!=w) at t={partial}"
                )
        self._finalize(r)
        out.append(r)

        # 3-3 (4 pts): no periodic job misses its absolute deadline.
        r = CheckResult("3-3", "all periodic jobs meet deadline", 4)
        responses: list[int] = []
        for job in self._jobs:
            active, _ = self._active_ticks(job)
            if len(active) != job.execution:
                r.violations.append(
                    f"{job.job_id}: only {len(active)}/{job.execution} ticks done"
                )
                continue
            completion = max(active)
            if completion > job.deadline:
                r.violations.append(
                    f"{job.job_id}: completes at t={completion} > deadline {job.deadline}"
                )
            responses.append(completion - job.release + 1)
        self._finalize(r)
        if responses and not r.violations:
            avg = sum(responses) / len(responses)
            r.desc += f" | avg response = {avg:.2f}, max = {max(responses)} ticks"
        out.append(r)
        return out

    # ------------------------------------------------------------- item 4

    def check_item4(self) -> list[CheckResult]:
        """Checks rubric item 4 (acceptance test, 11 pts).

        Sub-items 4-1 (method description) and 4-2 (accept/reject rationality)
        are graded from the written report and cannot be auto-verified, so they
        are reported as SKIP. Sub-item 4-3 (sporadic value rate) is recomputed
        from the schedule and scored against the rubric thresholds.
        """
        out: list[CheckResult] = []
        for item, desc in (
            ("4-1", "acceptance-test method description"),
            ("4-2", "accept/reject decision rationality"),
        ):
            r = CheckResult(item, desc, 3)
            r.status = "SKIP"
            r.violations.append("report-graded -- not auto-verifiable")
            out.append(r)
        out.append(self._sporadic_value_rate())
        return out

    def _sporadic_value_rate(self) -> CheckResult:
        """Item 4-3: sporadic value rate, scored by the rubric thresholds.

        Rate = (execution slots of sporadic jobs completed before their hard
        deadline) / (total sporadic execution slots). Scoring: 0 -> 0 pts,
        (0, 0.4) -> 1, [0.4, 0.7) -> 2, >= 0.7 -> 3. The rubric lists 5 pts for
        this row, but only this 0-3 threshold scale is concretely defined, so 3
        is used as the auto-checkable maximum. SKIP when no sporadic jobs exist.
        """
        r = CheckResult("4-3", "sporadic value rate (max 3 of 5 auto-checkable)", 3)
        sporadic = self._expand_sporadic_jobs()
        if not sporadic:
            r.status = "SKIP"
            r.violations.append("no sporadic jobs in task_set -- nothing to check")
            return r

        completion = self._completion_times()
        slot_counts = self._executed_slot_counts()
        rejected = self._collect_field("rejected_sporadic")
        total_exec = sum(j.execution for j in sporadic)
        done_exec = sum(
            j.execution
            for j in sporadic
            if j.job_id not in rejected
            and slot_counts.get(j.job_id, 0) >= j.execution
            and completion.get(j.job_id, self._horizon + 1) <= j.deadline
        )
        rate = done_exec / total_exec if total_exec else 0.0

        if rate <= 0.0:
            r.score = 0.0
        elif rate < 0.4:
            r.score = 1.0
        elif rate < 0.7:
            r.score = 2.0
        else:
            r.score = 3.0
        r.status = "PASS" if r.score > 0.0 else "FAIL"
        r.desc += f" | rate = {rate:.4f} ({done_exec}/{total_exec} slots)"

        # Cross-check against the evaluator's reported value, if available.
        reported = (self._evaluation or {}).get("acceptance_test", {}).get(
            "sporadic_value_rate"
        )
        if reported is not None and abs(float(reported) - rate) > 1e-3:
            r.violations.append(
                f"evaluator reports sporadic_value_rate={float(reported):.4f}"
                f" but recomputed {rate:.4f}"
            )
        return r

    # ------------------------------------------------------------- item 5

    def check_item5(self) -> list[CheckResult]:
        """Checks rubric item 5 (evaluation metrics, 7 pts).

        Each metric is recomputed independently from the schedule and task set,
        then cross-checked against ``evaluation_results.json``. A sub-item passes
        when every value it covers matches the reported value within tolerance.
        Reported as SKIP when no evaluation results were provided.
        """
        specs = [
            ("5-1", "hard_deadline_miss_rate", 1.0),
            ("5-2", "soft_deadline_miss_rate", 1.0),
            ("5-3", "average/max tardiness", 2.0),
            ("5-4", "average/max response time", 2.0),
            ("5-5", "completion_time_jitter", 1.0),
        ]
        if self._evaluation is None:
            out = []
            for item, desc, max_score in specs:
                r = CheckResult(item, desc, max_score)
                r.status = "SKIP"
                r.violations.append("evaluation_results.json not provided")
                out.append(r)
            return out

        metrics = self._recompute_metrics()
        out: list[CheckResult] = []
        out.append(self._compare("5-1", "hard deadline miss rate", 1.0,
                                  {"hard_deadline_miss_rate": metrics["hard_miss_rate"]}))
        out.append(self._compare("5-2", "soft deadline miss rate", 1.0,
                                  {"soft_deadline_miss_rate": metrics["soft_miss_rate"]}))
        out.append(self._compare("5-3", "average/max tardiness", 2.0, {
            "average_tardiness": metrics["avg_tardiness"],
            "max_tardiness": metrics["max_tardiness"],
        }))
        out.append(self._compare("5-4", "average/max response time", 2.0, {
            "average_response_time": metrics["avg_response"],
            "max_response_time": metrics["max_response"],
        }))
        out.append(self._compare("5-5", "completion time jitter", 1.0,
                                  {"completion_time_jitter": metrics["jitter"]}))
        return out

    def _recompute_metrics(self) -> dict[str, float]:
        """Recomputes item-5 metrics from the raw schedule (mirrors Evaluator)."""
        periodic = self._jobs
        sporadic = self._expand_sporadic_jobs()
        aperiodic = self._expand_aperiodic_jobs()
        completion = self._completion_times()
        slot_counts = self._executed_slot_counts()
        rejected = self._collect_field("rejected_sporadic")

        def _is_missed(job: _Job) -> bool:
            # Matches the external checker: a job misses when its executed slot
            # count is below e or its last executed tick exceeds the deadline.
            ct = completion.get(job.job_id)
            if slot_counts.get(job.job_id, 0) < job.execution:
                return True
            return ct is not None and ct > job.deadline

        # Hard deadline miss rate over periodic + accepted sporadic jobs.
        # Rejected sporadic jobs are excluded from both numerator and
        # denominator (matching the external checker).
        hard_jobs = periodic + [j for j in sporadic if j.job_id not in rejected]
        hard_miss = sum(1 for job in hard_jobs if _is_missed(job))
        hard_miss_rate = hard_miss / len(hard_jobs) if hard_jobs else 0.0

        total_soft = len(aperiodic)
        soft_miss = sum(1 for job in aperiodic if _is_missed(job))
        soft_miss_rate = soft_miss / total_soft if total_soft else 0.0

        # Tardiness / response over all jobs with at least one executed slot.
        tardiness: list[float] = []
        response: list[float] = []
        for job in periodic + sporadic + aperiodic:
            ct = completion.get(job.job_id)
            if ct is None:
                continue
            tardiness.append(max(0.0, ct - job.deadline))
            response.append(float(ct - job.release))

        # Completion-time jitter: mean of per-periodic-task population standard
        # deviation of completion times (single-instance tasks contribute 0).
        task_cts: dict[str, list[int]] = {}
        for job in periodic:
            ct = completion.get(job.job_id)
            if ct is not None:
                task_cts.setdefault(job.task_id, []).append(ct)
        jitter_vals = [
            _pstdev(cts) if len(cts) > 1 else 0.0 for cts in task_cts.values()
        ]

        return {
            "hard_miss_rate": hard_miss_rate,
            "soft_miss_rate": soft_miss_rate,
            "avg_tardiness": sum(tardiness) / len(tardiness) if tardiness else 0.0,
            "max_tardiness": max(tardiness) if tardiness else 0.0,
            "avg_response": sum(response) / len(response) if response else 0.0,
            "max_response": max(response) if response else 0.0,
            "jitter": sum(jitter_vals) / len(jitter_vals) if jitter_vals else 0.0,
        }

    def _compare(
        self,
        item: str,
        desc: str,
        max_score: float,
        recomputed: dict[str, float],
        tol: float = 1e-3,
    ) -> CheckResult:
        """Builds a result that fails if any reported metric differs from ours.

        Args:
            item: Rubric sub-item id.
            desc: Human-readable description.
            max_score: Points for this sub-item.
            recomputed: Mapping of ``evaluation_results.json`` key -> our value.
            tol: Absolute tolerance for the comparison.
        """
        r = CheckResult(item, desc, max_score)
        shown: list[str] = []
        assert self._evaluation is not None
        for key, ours in recomputed.items():
            reported = self._evaluation.get(key)
            shown.append(f"{key}={ours:.4f}")
            if reported is None:
                r.violations.append(f"{key} missing from evaluation_results.json")
            elif abs(float(reported) - ours) > tol:
                r.violations.append(
                    f"{key}: recomputed {ours:.4f} != reported {float(reported):.4f}"
                )
        self._finalize(r)
        r.desc += " | " + ", ".join(shown)
        return r

    # ------------------------------------------------------------- item 6

    def check_item6(self) -> list[CheckResult]:
        """Checks rubric item 6 (reserve-strategy analysis, 10 pts).

        Both sub-items (reserve-strategy description and objective trade-off
        analysis) are graded from the written report using actual scheduling
        data, so neither can be auto-verified; both report as SKIP.
        """
        out: list[CheckResult] = []
        for item, desc in (
            ("6-1", "reserve-strategy algorithm description"),
            ("6-2", "objective-function trade-off analysis"),
        ):
            r = CheckResult(item, desc, 5)
            r.status = "SKIP"
            r.violations.append("report-graded -- not auto-verifiable")
            out.append(r)
        return out

    # ---------------------------------------------------- constraint checks

    def _active_ticks(self, job: _Job) -> tuple[list[int], list[int]]:
        """Returns (active ticks, partial-supply ticks) for a regular job.

        A tick is *active* when total routed energy exceeds eps. A tick is
        *partial* when energy is positive but not equal to the demand w.
        """
        active: list[int] = []
        partial: list[int] = []
        for t in range(job.release, job.deadline + 1):
            ksum = self._k_job_total(job.job_id, t)
            if ksum > self._eps:
                active.append(t)
                if abs(ksum - job.demand) > self._eps:
                    partial.append(t)
        return active, partial

    def _c1_demand(self) -> list[str]:
        """C1: when a regular job runs, it receives exactly w (else 0)."""
        bad: list[str] = []
        for job in self._jobs:
            _, partial = self._active_ticks(job)
            for t in partial:
                bad.append(
                    f"{job.job_id}@t{t}: got {self._k_job_total(job.job_id, t):.2f}"
                    f" != w {job.demand}"
                )
        return bad

    def _c2_release(self) -> list[str]:
        """C2: no energy routed to a job outside its [release, deadline] window."""
        bad: list[str] = []
        windows = {j.job_id: (j.release, j.deadline) for j in self._jobs}
        for t, rec in self._ticks.items():
            for job_id, alloc in rec.get("k", {}).items():
                if job_id in self._chg_job_ids or job_id not in windows:
                    continue
                rel, dl = windows[job_id]
                if (t < rel or t > dl) and sum(alloc.values()) > self._eps:
                    bad.append(f"{job_id}@t{t}: outside window [{rel},{dl}]")
        return bad

    def _c3_execution(self) -> list[str]:
        """C3: each regular job executes for exactly e ticks within its window."""
        bad: list[str] = []
        for job in self._jobs:
            active, _ = self._active_ticks(job)
            if len(active) != job.execution:
                bad.append(
                    f"{job.job_id}: {len(active)} active ticks != e {job.execution}"
                )
        return bad

    def _c5_non_preemptive(self) -> list[str]:
        """C5: non-preemptive jobs run on contiguous ticks."""
        bad: list[str] = []
        for job in self._jobs:
            if job.preemptive:
                continue
            active, _ = self._active_ticks(job)
            if active and (max(active) - min(active) + 1) != len(active):
                bad.append(f"{job.job_id}: non-contiguous active ticks {active}")
        return bad

    def _c4_aperiodic(self) -> CheckResult:
        """C4: aperiodic miss flags are consistent and all work completes by H.

        For each aperiodic job we recompute its activity over [release, H] and
        check (a) all e steps complete by the horizon end, and (b) the schedule's
        ``missed_aperiodic`` flag matches whether fewer than e steps landed
        within the soft deadline. Reported as SKIP when no aperiodic jobs exist.
        """
        r = CheckResult("2-2", "aperiodic miss definition (C4)", 2)
        ap_jobs = self._expand_aperiodic_jobs()
        if not ap_jobs:
            r.status = "SKIP"
            r.score = 0.0
            r.violations.append("no aperiodic jobs in task_set -- nothing to check")
            return r

        reported_missed: set[str] = set()
        for rec in self._ticks.values():
            reported_missed.update(rec.get("missed_aperiodic", []))

        for job in ap_jobs:
            jid = job.job_id
            done_by_h = [
                t
                for t in range(job.release, self._horizon + 1)
                if self._k_job_total(jid, t) > self._eps
            ]
            done_by_deadline = [t for t in done_by_h if t <= job.deadline]

            # Every aperiodic job must complete all e steps by the horizon end.
            if len(done_by_h) != job.execution:
                r.violations.append(
                    f"{jid}: {len(done_by_h)} steps by H != e {job.execution}"
                )

            # Miss flag must match actual completion within the soft deadline.
            actual_miss = len(done_by_deadline) < job.execution
            flagged = jid in reported_missed
            if actual_miss != flagged:
                r.violations.append(
                    f"{jid}: {len(done_by_deadline)}/{job.execution} done by deadline"
                    f" -> miss={actual_miss}, but missed_aperiodic flag={flagged}"
                )

        self._finalize(r)
        return r

    def _c20_device_alloc(self) -> list[str]:
        """C20: per device, total routed energy never exceeds its output."""
        bad: list[str] = []
        for t in range(1, self._horizon + 1):
            for dev in self._all_device_ids:
                alloc = self._k_dev_total(dev, t)
                if alloc - self._P(dev, t) > self._eps:
                    bad.append(
                        f"{dev}@t{t}: alloc {alloc:.2f} > P {self._P(dev, t):.2f}"
                    )
        return bad

    def _c6_output_bounds(self) -> list[str]:
        """C6: a running generator stays within [output_min, output_max]."""
        bad: list[str] = []
        for gid, g in self._gen.items():
            for t in range(1, self._horizon + 1):
                p = self._P(gid, t)
                if p > g["output_max"] + self._eps:
                    bad.append(f"{gid}@t{t}: P {p:.2f} > max {g['output_max']}")
                if self._eps < p < g["output_min"] - self._eps:
                    bad.append(f"{gid}@t{t}: P {p:.2f} < min {g['output_min']}")
        return bad

    def _c7_ramp(self) -> list[str]:
        """C7: generator output respects ramp-up / ramp-down limits."""
        bad: list[str] = []
        for gid, g in self._gen.items():
            prev = float(g["initial_energy"])
            for t in range(1, self._horizon + 1):
                p = self._P(gid, t)
                if p - prev > g["ramp_up_rate"] + self._eps:
                    bad.append(f"{gid}@t{t}: ramp-up {p - prev:.2f} > {g['ramp_up_rate']}")
                if prev - p > g["ramp_down_rate"] + self._eps:
                    bad.append(
                        f"{gid}@t{t}: ramp-down {prev - p:.2f} > {g['ramp_down_rate']}"
                    )
                prev = p
        return bad

    def _c8_min_ramp_feasibility(self) -> list[str]:
        """C8: output_min must be reachable within one ramp-up step."""
        bad: list[str] = []
        for gid, g in self._gen.items():
            if g["output_min"] > g["ramp_up_rate"]:
                bad.append(
                    f"{gid}: output_min {g['output_min']} > ramp_up {g['ramp_up_rate']}"
                )
        return bad

    def _gen_on(self, gid: str) -> dict[int, int]:
        """Infers on/off state (1/0) per tick from positive output."""
        g = self._gen[gid]
        state = {0: 1 if g["initial_on_time"] > 0 else 0}
        for t in range(1, self._horizon + 1):
            state[t] = 1 if self._P(gid, t) > self._eps else 0
        return state

    def _c9_min_up(self) -> list[str]:
        """C9: after a start, a generator stays on for min_up_time ticks."""
        bad: list[str] = []
        for gid, g in self._gen.items():
            u = self._gen_on(gid)
            ut = g["min_up_time"]
            for t in range(1, self._horizon + 1):
                if u[t] == 1 and u[t - 1] == 0:  # start
                    end = min(t + ut - 1, self._horizon)
                    off = [s for s in range(t, end + 1) if u[s] == 0]
                    if off:
                        bad.append(f"{gid}: started t={t} but off at {off}")
        return bad

    def _c10_min_down(self) -> list[str]:
        """C10: after a stop, a generator stays off for min_down_time ticks."""
        bad: list[str] = []
        for gid, g in self._gen.items():
            u = self._gen_on(gid)
            dt = g["min_down_time"]
            for t in range(1, self._horizon + 1):
                if u[t] == 0 and u[t - 1] == 1:  # stop
                    end = min(t + dt - 1, self._horizon)
                    on = [s for s in range(t, end + 1) if u[s] == 1]
                    if on:
                        bad.append(f"{gid}: stopped t={t} but on at {on}")
        return bad

    def _c11_initial_up(self) -> list[str]:
        """C11: honour residual minimum up-time carried from before t=1."""
        bad: list[str] = []
        for gid, g in self._gen.items():
            if g["initial_on_time"] <= 0:
                continue
            remaining = max(0, g["min_up_time"] - g["initial_on_time"])
            for t in range(1, min(remaining, self._horizon) + 1):
                if self._P(gid, t) <= self._eps:
                    bad.append(f"{gid}@t{t}: must stay on (residual up-time)")
        return bad

    def _c12_initial_down(self) -> list[str]:
        """C12: honour residual minimum down-time carried from before t=1."""
        bad: list[str] = []
        for gid, g in self._gen.items():
            if g["initial_off_time"] <= 0:
                continue
            remaining = max(0, g["min_down_time"] - g["initial_off_time"])
            for t in range(1, min(remaining, self._horizon) + 1):
                if self._P(gid, t) > self._eps:
                    bad.append(f"{gid}@t{t}: must stay off (residual down-time)")
        return bad

    def _c13_renewable(self) -> list[str]:
        """C13: renewable output never exceeds the forecast-derived cap."""
        bad: list[str] = []
        for rid in self._ren_ids:
            cap = self._ren_cap[rid]
            fc = self._ren_forecast.get(rid, {})
            for t in range(1, self._horizon + 1):
                limit = cap * fc.get(t, 0.0)
                if self._P(rid, t) - limit > self._eps:
                    bad.append(f"{rid}@t{t}: P {self._P(rid, t):.2f} > cap {limit:.2f}")
        return bad

    def _c14_discharge_cap(self) -> list[str]:
        """C14: storage discharge never exceeds discharge_max."""
        bad: list[str] = []
        for sid, b in self._sto.items():
            for t in range(1, self._horizon + 1):
                if self._P(sid, t) - b["discharge_max"] > self._eps:
                    bad.append(
                        f"{sid}@t{t}: discharge {self._P(sid, t):.2f} > {b['discharge_max']}"
                    )
        return bad

    def _c15_charge_cap(self) -> list[str]:
        """C15: storage charge intake never exceeds charge_max."""
        bad: list[str] = []
        for sid, b in self._sto.items():
            for t in range(1, self._horizon + 1):
                ci = self._charge_in(sid, t)
                if ci - b["charge_max"] > self._eps:
                    bad.append(f"{sid}@t{t}: charge {ci:.2f} > {b['charge_max']}")
        return bad

    def _c16_soc_balance(self) -> list[str]:
        """C16: SOC[t] == SOC[t-1] + charge_in[t] - discharge[t]."""
        bad: list[str] = []
        for sid, b in self._sto.items():
            prev = float(b["soc_init"])
            for t in range(1, self._horizon + 1):
                expected = prev + self._charge_in(sid, t) - self._P(sid, t)
                actual = self._soc(sid, t)
                if abs(actual - expected) > self._eps:
                    bad.append(
                        f"{sid}@t{t}: SOC {actual:.2f} != expected {expected:.2f}"
                    )
                prev = actual
        return bad

    def _c17_soc_bounds(self) -> list[str]:
        """C17: SOC stays within [soc_min, soc_max]."""
        bad: list[str] = []
        for sid, b in self._sto.items():
            for t in range(1, self._horizon + 1):
                soc = self._soc(sid, t)
                if soc < b["soc_min"] - self._eps or soc > b["soc_max"] + self._eps:
                    bad.append(
                        f"{sid}@t{t}: SOC {soc:.2f} out of [{b['soc_min']},{b['soc_max']}]"
                    )
        return bad

    def _c18_discharge_vs_soc(self) -> list[str]:
        """C18: discharge cannot exceed available energy above soc_min."""
        bad: list[str] = []
        for sid, b in self._sto.items():
            prev = float(b["soc_init"])
            for t in range(1, self._horizon + 1):
                if self._P(sid, t) - (prev - b["soc_min"]) > self._eps:
                    bad.append(
                        f"{sid}@t{t}: discharge {self._P(sid, t):.2f} > "
                        f"SOC_prev-soc_min {prev - b['soc_min']:.2f}"
                    )
                prev = self._soc(sid, t)
        return bad

    def _c19_no_simultaneous(self) -> list[str]:
        """C19: a storage never charges and discharges in the same tick."""
        bad: list[str] = []
        for sid in self._sto_ids:
            for t in range(1, self._horizon + 1):
                if self._charge_in(sid, t) > self._eps and self._P(sid, t) > self._eps:
                    bad.append(f"{sid}@t{t}: charge and discharge simultaneously")
        return bad

    def _c21_charge_source(self) -> list[str]:
        """C21: charging energy comes only from generators / renewables."""
        bad: list[str] = []
        for t, rec in self._ticks.items():
            for job_id, alloc in rec.get("k", {}).items():
                if job_id not in self._chg_job_ids:
                    continue
                for dev, val in alloc.items():
                    if dev not in self._gen_ren_ids and val > self._eps:
                        bad.append(f"{job_id}@t{t}: charged by non-source {dev}")
        return bad

    def _c22_sell_nonneg(self) -> list[str]:
        """C22: market sales are non-negative."""
        return [
            f"t{t}: sell {self._sell(t):.2f} < 0"
            for t in range(1, self._horizon + 1)
            if self._sell(t) < -self._eps
        ]

    def _c23_balance(self) -> CheckResult:
        """C23: hourly supply equals routed demand plus sales (-0.5 per hour)."""
        r = CheckResult("2-7", "hourly power balance (C23)", 4)
        bad_hours = 0
        for t in range(1, self._horizon + 1):
            supply = sum(self._P(d, t) for d in self._all_device_ids)
            routed = sum(self._k_dev_total(d, t) for d in self._all_device_ids)
            if abs(supply - (routed + self._sell(t))) > self._eps:
                bad_hours += 1
                if len(r.violations) < 8:
                    r.violations.append(
                        f"t{t}: supply {supply:.2f} != routed {routed:.2f}"
                        f" + sell {self._sell(t):.2f}"
                    )
        r.score = max(0.0, r.max_score - 0.5 * bad_hours)
        r.status = "PASS" if bad_hours == 0 else "FAIL"
        if bad_hours:
            r.desc += f" ({bad_hours} bad hours)"
        return r

    # ------------------------------------------------------------- scoring

    def _finalize(self, r: CheckResult) -> None:
        """Sets binary score/status from presence of violations."""
        if r.violations:
            r.status = "FAIL"
            r.score = 0.0
        else:
            r.status = "PASS"
            r.score = r.max_score

    def _grouped(
        self, item: str, desc: str, max_score: float, checks: dict[str, list[str]]
    ) -> CheckResult:
        """Builds a grouped result, deducting one point per violated class."""
        r = CheckResult(item, desc, max_score)
        violated = [code for code, bad in checks.items() if bad]
        for code in violated:
            for msg in checks[code][:4]:
                r.violations.append(f"[{code}] {msg}")
            extra = len(checks[code]) - 4
            if extra > 0:
                r.violations.append(f"[{code}] ... (+{extra} more)")
        r.score = max(0.0, max_score - len(violated))
        r.status = "PASS" if not violated else "FAIL"
        return r

    def validate(self) -> list[CheckResult]:
        """Runs all Level 1 checks and returns the ordered results."""
        return (
            self.check_item1()
            + self.check_item2()
            + self.check_item3()
            + self.check_item4()
            + self.check_item5()
            + self.check_item6()
        )


class Level2Validator(Level1Validator):
    """Validates Level 2 against the *dynamic* schedule and the relaxed model.

    Level 2 re-uses every Level 1 rubric item (task set, model constraints,
    acceptance test, schedule result, evaluation metrics, reserve analysis) but
    grades them on the advanced dynamic scheduler's output, with the storage and
    renewable constraints replaced by their relaxed forms. It then adds the two new
    Level 2 items: item 3 (relaxed-assumption constraints, 1 pt each, cap 10) and
    item 8 (advanced dynamic scheduling method).

    Report-graded sub-items remain SKIP: 4-1/4-2 (acceptance write-up), the reserve
    analysis, 8-1 (method design) and 8-3 (static-vs-dynamic discussion). The item 3
    *modelling description* is report-graded too; what is auto-checked here is the
    *implementation* — that the dynamic schedule actually satisfies each relaxation.
    """

    _RELAX_CAP = 10  # rubric item 3 caps relaxed-assumption credit at 10 points

    def __init__(
        self,
        task_set: dict[str, Any],
        schedule: dict[str, Any],
        settings: dict[str, Any],
        relaxation: dict[str, Any],
        evaluation: dict[str, Any] | None = None,
        realized_renewable: dict[str, Any] | None = None,
        precedence: list[list[str]] | None = None,
        horizon: int = _HORIZON,
        eps: float = _EPS,
    ) -> None:
        """Initializes the Level 2 validator.

        Args:
            task_set: Parsed ``task_set.json``.
            schedule: Parsed ``schedule_result_dynamic.json`` (Level 1 schema).
            settings: Parsed ``processor_settings.json``.
            relaxation: ``relaxation`` block from ``runtime_config.json``.
            evaluation: Parsed ``evaluation_results_dynamic.json``, or ``None``.
            realized_renewable: ``{renewable_id: {hour: fraction}}`` realized PV
                availability exported in ``dynamic_run_log.json``; bounds renewable
                output per committed tick.
            precedence: Auto-selected/configured ``[a, b]`` job-id pairs.
            horizon: Scheduling horizon length in ticks.
            eps: Floating-point comparison tolerance.
        """
        super().__init__(task_set, schedule, settings, evaluation, horizon, eps)
        self._relax = relaxation or {}
        self._eta_c = float(self._relax.get("charge_efficiency", 1.0))
        self._eta_d = float(self._relax.get("discharge_efficiency", 1.0))
        self._sigma = float(self._relax.get("self_discharge_rate", 0.0))
        self._cycle = self._relax.get("cycle_limit", None)
        self._soc_floor = float(self._relax.get("soc_power_floor", 1.0))
        self._aging = float(self._relax.get("aging_cost", 0.0))
        self._beta = float(self._relax.get("renewable_uncertainty_margin", 0.0))
        self._precedence = precedence or []
        self._realized: dict[str, dict[int, float]] = {
            rid: {int(h): float(v) for h, v in series.items()}
            for rid, series in (realized_renewable or {}).items()
        }

    # ----------------------------------------- relaxed constraint overrides

    def _c13_renewable(self) -> list[str]:
        """C13′: renewable output respects the realized availability cap.

        The dynamic schedule commits each block against the *realized* PV
        availability (which may be above or below the forecast), so the bound is
        ``capacity · realized``. Without an exported realized series we fall back to
        the installed-capacity bound, which can never be a false positive.
        """
        bad: list[str] = []
        for rid in self._ren_ids:
            cap = self._ren_cap[rid]
            realized = self._realized.get(rid)
            fc = self._ren_forecast.get(rid, {})
            for t in range(1, self._horizon + 1):
                if realized is not None:
                    limit = cap * realized.get(t, fc.get(t, 0.0))
                else:
                    limit = float(cap)  # installed-capacity bound
                if self._P(rid, t) - limit > self._eps:
                    bad.append(
                        f"{rid}@t{t}: P {self._P(rid, t):.2f} > realized cap {limit:.2f}"
                    )
        return bad

    def _c16_soc_balance(self) -> list[str]:
        """C16′: SOC[t] == (1−σ)·SOC[t−1] + η_c·charge_in − (1/η_d)·discharge."""
        bad: list[str] = []
        for sid, b in self._sto.items():
            prev = float(b["soc_init"])
            for t in range(1, self._horizon + 1):
                expected = (
                    (1.0 - self._sigma) * prev
                    + self._eta_c * self._charge_in(sid, t)
                    - (1.0 / self._eta_d) * self._P(sid, t)
                )
                actual = self._soc(sid, t)
                if abs(actual - expected) > self._eps:
                    bad.append(
                        f"{sid}@t{t}: SOC {actual:.2f} != expected {expected:.2f}"
                    )
                prev = actual
        return bad

    def _c18_discharge_vs_soc(self) -> list[str]:
        """C18′: (1/η_d)·discharge ≤ (1−σ)·SOC[t−1] − soc_min."""
        bad: list[str] = []
        for sid, b in self._sto.items():
            prev = float(b["soc_init"])
            for t in range(1, self._horizon + 1):
                drawn = (1.0 / self._eta_d) * self._P(sid, t)
                usable_prev = (1.0 - self._sigma) * prev - b["soc_min"]
                if drawn - usable_prev > self._eps:
                    bad.append(
                        f"{sid}@t{t}: drawn {drawn:.2f} > usable SOC {usable_prev:.2f}"
                    )
                prev = self._soc(sid, t)
        return bad

    # ----------------------------------------- new relaxed constraints (item 3)

    def _r_cycle(self) -> list[str]:
        """Throughput limit: total discharge ≤ cycle_limit · (soc_max − soc_min)."""
        bad: list[str] = []
        if self._cycle is None:
            return bad
        for sid, b in self._sto.items():
            usable = b["soc_max"] - b["soc_min"]
            total = sum(self._P(sid, t) for t in range(1, self._horizon + 1))
            if total - float(self._cycle) * usable > self._eps:
                bad.append(
                    f"{sid}: discharge {total:.2f} > {self._cycle}×usable {self._cycle * usable:.2f}"
                )
        return bad

    def _r_soc_power(self, charging: bool) -> list[str]:
        """SOC-dependent power taper for discharge (or charge when ``charging``)."""
        bad: list[str] = []
        if self._soc_floor >= 1.0:
            return bad
        for sid, b in self._sto.items():
            usable = b["soc_max"] - b["soc_min"]
            if usable <= 0:
                continue
            prev = float(b["soc_init"])
            for t in range(1, self._horizon + 1):
                if charging:
                    frac = self._soc_floor + (1.0 - self._soc_floor) * (
                        (b["soc_max"] - prev) / usable
                    )
                    limit = b["charge_max"] * frac
                    val = self._charge_in(sid, t)
                    label = "charge"
                else:
                    frac = self._soc_floor + (1.0 - self._soc_floor) * (
                        (prev - b["soc_min"]) / usable
                    )
                    limit = b["discharge_max"] * frac
                    val = self._P(sid, t)
                    label = "discharge"
                if val - limit > self._eps:
                    bad.append(f"{sid}@t{t}: {label} {val:.2f} > SOC-dep cap {limit:.2f}")
                prev = self._soc(sid, t)
        return bad

    def _r_precedence(self) -> list[str]:
        """Precedence: job b stays inactive until job a has run its e_a ticks."""
        bad: list[str] = []
        for pair in self._precedence:
            if len(pair) != 2:
                continue
            a, b = pair
            a_task = a.rsplit("_", 1)[0]
            e_a = self._tasks.get(a_task, {}).get("e")
            if e_a is None:
                continue
            a_ticks = [
                t for t in range(1, self._horizon + 1) if self._k_job_total(a, t) > self._eps
            ]
            b_ticks = [
                t for t in range(1, self._horizon + 1) if self._k_job_total(b, t) > self._eps
            ]
            if not b_ticks:
                continue
            a_before = [t for t in a_ticks if t < b_ticks[0]]
            if len(a_before) < e_a:
                bad.append(
                    f"{b} active at t={b_ticks[0]} but {a} only ran "
                    f"{len(a_before)}/{e_a} ticks before it"
                )
        return bad

    def check_item3_relaxations(self) -> list[CheckResult]:
        """Item 3: one point per relaxed constraint correctly implemented (cap 10).

        Each relaxation that is *enabled* in ``runtime_config.json`` is awarded a
        point when the dynamic schedule satisfies it; disabled relaxations are SKIP.
        The relaxation modelling description itself is report-graded.
        """
        soc_bad = self._c16_soc_balance()
        c18_bad = self._c18_discharge_vs_soc()
        c13_bad = self._c13_renewable()
        cyc_bad = self._r_cycle()
        dis_bad = self._r_soc_power(charging=False)
        chg_bad = self._r_soc_power(charging=True)
        prec_bad = self._r_precedence()

        renewable_realized = bool(self._realized) and any(
            abs(self._realized[rid].get(t, 0.0) - self._ren_forecast.get(rid, {}).get(t, 0.0))
            > 1e-3
            for rid in self._realized
            for t in range(1, self._horizon + 1)
        )

        def make(item: str, desc: str, active: bool, ok: bool,
                 off_msg: str, fail: list[str]) -> CheckResult:
            r = CheckResult(item, desc, 1)
            if not active:
                r.status = "SKIP"
                r.violations.append(off_msg)
            elif ok:
                r.status, r.score = "PASS", 1.0
            else:
                r.status, r.score = "FAIL", 0.0
                r.violations.extend(fail[:4])
            return r

        checks = [
            make("R1", "renewable uncertainty (realized cap C13′)",
                 self._beta > 0 or bool(self._realized),
                 renewable_realized and not c13_bad,
                 "renewable_uncertainty_margin=0 and no realized series",
                 c13_bad or ["no realized series differing from forecast"]),
            make("R2", "charge efficiency η_c (C16′)", self._eta_c < 1.0,
                 not soc_bad, "charge_efficiency=1.0", soc_bad),
            make("R3", "discharge efficiency η_d (C16′/C18′)", self._eta_d < 1.0,
                 not soc_bad and not c18_bad, "discharge_efficiency=1.0",
                 soc_bad + c18_bad),
            make("R4", "self-discharge σ (C16′)", self._sigma > 0.0,
                 not soc_bad, "self_discharge_rate=0", soc_bad),
            make("R5", "discharge vs usable SOC (C18′)", self._eta_d < 1.0 or self._sigma > 0.0,
                 not c18_bad, "no efficiency/self-discharge", c18_bad),
            make("R6", "cycle / throughput limit", self._cycle is not None,
                 not cyc_bad, "cycle_limit not set", cyc_bad),
            make("R7", "SOC-dependent discharge power", self._soc_floor < 1.0,
                 not dis_bad, "soc_power_floor=1.0", dis_bad),
            make("R8", "SOC-dependent charge power", self._soc_floor < 1.0,
                 not chg_bad, "soc_power_floor=1.0", chg_bad),
            make("R9", "battery aging cost (objective)", self._aging > 0.0,
                 True, "aging_cost=0",
                 []),  # objective term: presence verified from config, not schedule
            make("R10", "job precedence", bool(self._precedence),
                 not prec_bad, "no precedence pairs", prec_bad),
        ]
        if self._aging > 0.0:
            checks[8].desc += " (config-verified; objective term)"

        # Cap the awarded total at 10 (rubric item 3 ceiling).
        awarded = sum(c.score for c in checks)
        if awarded > self._RELAX_CAP:
            overflow = awarded - self._RELAX_CAP
            for c in reversed(checks):
                if overflow <= 0:
                    break
                if c.score > 0:
                    cut = min(c.score, overflow)
                    c.score -= cut
                    overflow -= cut
                    c.desc += " (capped)"
        return checks

    def check_item8(self, prior: list[CheckResult]) -> list[CheckResult]:
        """Item 8: 8-1/8-3 report-graded (SKIP); 8-2 schedule correctness auto-checked."""
        out: list[CheckResult] = []

        r = CheckResult("8-1", "advanced dynamic scheduling method design", 2)
        r.status = "SKIP"
        r.violations.append("report-graded -- not auto-verifiable")
        out.append(r)

        # 8-2: the dynamic schedule must satisfy all model + relaxed constraints,
        # hard deadlines, energy balance and SOC feasibility.
        r = CheckResult("8-2", "dynamic schedule correctness", 4)
        relevant = [
            res for res in prior
            if (res.item.startswith(("2-", "3-")) or res.item.startswith("R"))
            and res.status == "FAIL"
        ]
        if relevant:
            r.status, r.score = "FAIL", 0.0
            r.violations.append(
                "failing checks: " + ", ".join(res.item for res in relevant)
            )
        else:
            r.status, r.score = "PASS", 4.0
            r.desc += " | all model/relaxed constraints, deadlines & balance hold"
        out.append(r)

        r = CheckResult("8-3", "static-vs-dynamic comparison", 4)
        r.status = "SKIP"
        r.violations.append("report-graded -- see printed comparison")
        out.append(r)
        return out

    def check_item6(self) -> list[CheckResult]:
        """Item 7 (reserve-strategy analysis): report-graded, both sub-items SKIP."""
        out: list[CheckResult] = []
        for item, desc in (
            ("7-1", "reserve-strategy algorithm description"),
            ("7-2", "objective-function trade-off analysis"),
        ):
            r = CheckResult(item, desc, 5)
            r.status = "SKIP"
            r.violations.append("report-graded -- not auto-verifiable")
            out.append(r)
        return out

    def validate(self) -> list[CheckResult]:
        """Runs all Level 2 checks against the dynamic schedule and returns results.

        Rubric mapping (Level 2 numbering): item 1 = task set, item 2 = model
        constraints (relaxed), item 3 = R1..R10 relaxations, item 4 = acceptance,
        item 5 = schedule result (the ``3-x`` rows), item 6 = evaluation (``5-x``),
        item 7 = reserve analysis (``7-x``, SKIP), item 8 = dynamic method.
        """
        results: list[CheckResult] = []
        results += self.check_item1()
        results += self.check_item2()
        results += self.check_item3_relaxations()
        results += self.check_item3()   # L1 schedule-result rows -> L2 item 5
        results += self.check_item4()
        results += self.check_item5()    # eval rows -> L2 item 6
        results += self.check_item6()    # reserve analysis -> L2 item 7 (SKIP)
        results += self.check_item8(results)
        return results


def _print_report(
    results: list[CheckResult],
    title: str = "VPP-RTS Level 1 self-check",
    skip_note: str = (
        "SKIP rows (4-1, 4-2, 6-1, 6-2 and any check whose inputs are\n"
        "        absent) are report-graded or not applicable; excluded above."
    ),
) -> float:
    """Prints a human-readable report and returns the total self-grade."""
    symbol = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}
    total = 0.0
    total_max = 0.0
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)
    for r in results:
        if r.status != "SKIP":
            total += r.score
            total_max += r.max_score
        head = f"  [{symbol[r.status]}] {r.item:<4} {r.score:>4.1f}/{r.max_score:<4.0f} {r.desc}"
        print(head)
        for msg in r.violations[:10]:
            print(f"            - {msg}")
        if len(r.violations) > 10:
            print(f"            - ... (+{len(r.violations) - 10} more)")
    print("-" * 72)
    print(f"  Self-grade (excluding SKIP): {total:.1f} / {total_max:.0f}")
    print(f"  NOTE: {skip_note}")
    print("=" * 72)
    return total


def _print_comparison(static_eval: dict[str, Any], dynamic_eval: dict[str, Any]) -> None:
    """Prints the static-vs-dynamic metric comparison (informational, item 8-3)."""
    keys = [
        "objective_value", "generator_cost", "market_revenue",
        "hard_deadline_miss_rate", "soft_deadline_miss_rate",
        "average_response_time",
    ]
    print("\n  Static (L1) vs dynamic (L2) — informational for report item 8-3:")
    print(f"    {'metric':28} {'static':>14} {'dynamic':>14}")
    for k in keys:
        print(f"    {k:28} {static_eval.get(k, 0):>14} {dynamic_eval.get(k, 0):>14}")
    sv_s = static_eval.get("acceptance_test", {}).get("sporadic_value_rate", 0)
    sv_d = dynamic_eval.get("acceptance_test", {}).get("sporadic_value_rate", 0)
    print(f"    {'sporadic_value_rate':28} {sv_s:>14} {sv_d:>14}")


def _run_level1(args: argparse.Namespace) -> list[CheckResult]:
    """Builds and runs the Level 1 validator, returning the check results."""
    validator = Level1Validator(
        task_set=_load_json(args.task_set),
        schedule=_load_json(args.schedule),
        settings=_load_json(args.settings),
        evaluation=_load_json_optional(args.evaluation),
        horizon=args.horizon,
        eps=args.eps,
    )
    results = validator.validate()
    _print_report(results)
    return results


def _run_level2(args: argparse.Namespace) -> list[CheckResult]:
    """Builds and runs the Level 2 validator against the dynamic artifacts."""
    runtime = _load_json_optional(args.runtime_config) or {}
    run_log = _load_json_optional(args.run_log) or {}

    validator = Level2Validator(
        task_set=_load_json(args.task_set),
        schedule=_load_json(args.schedule),
        settings=_load_json(args.settings),
        relaxation=runtime.get("relaxation", {}),
        evaluation=_load_json_optional(args.evaluation),
        realized_renewable=run_log.get("realized_renewable"),
        precedence=run_log.get("precedence"),
        horizon=args.horizon,
        eps=args.eps,
    )
    results = validator.validate()
    _print_report(
        results,
        title="VPP-RTS Level 2 self-check (dynamic schedule)",
        skip_note=(
            "SKIP rows (4-1, 4-2, 7-1, 7-2, 8-1, 8-3, the item 3 modelling\n"
            "        write-up, and any check with absent inputs) are report-graded\n"
            "        or not applicable; excluded above."
        ),
    )
    static_eval = _load_json_optional(args.static_evaluation)
    dynamic_eval = _load_json_optional(args.evaluation)
    if static_eval and dynamic_eval:
        _print_comparison(static_eval, dynamic_eval)
    return results


def main() -> None:
    """CLI entry point for the self-check (Level 1 by default, ``--level 2``)."""
    parser = argparse.ArgumentParser(description="VPP-RTS self-validator")
    parser.add_argument("--level", type=int, choices=(1, 2), default=1)
    parser.add_argument("--task-set", default="output/task_set.json")
    parser.add_argument("--schedule", default=None,
                        help="defaults to schedule_result.json (L1) / "
                             "schedule_result_dynamic.json (L2)")
    parser.add_argument("--settings", default="input/processor_settings.json")
    parser.add_argument("--evaluation", default=None,
                        help="defaults to evaluation_results.json (L1) / "
                             "evaluation_results_dynamic.json (L2)")
    parser.add_argument("--runtime-config", default="runtime_config.json")
    parser.add_argument("--run-log", default="output/dynamic_run_log.json")
    parser.add_argument("--static-evaluation", default="output/evaluation_results.json")
    parser.add_argument("--horizon", type=int, default=_HORIZON)
    parser.add_argument("--eps", type=float, default=_EPS)
    args = parser.parse_args()

    if args.level == 2:
        args.schedule = args.schedule or "output/schedule_result_dynamic.json"
        args.evaluation = args.evaluation or "output/evaluation_results_dynamic.json"
        results = _run_level2(args)
    else:
        args.schedule = args.schedule or "output/schedule_result.json"
        args.evaluation = args.evaluation or "output/evaluation_results.json"
        results = _run_level1(args)

    # Non-zero exit if any covered constraint is violated, for CI use.
    failed = any(r.status == "FAIL" for r in results)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
