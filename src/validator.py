"""Self-grading validator for Level 1 of the VPP-RTS assignment.

This module re-reads the produced JSON artifacts (``task_set.json`` and
``schedule_result.json``) together with the fixed inputs and checks them against
the Level 1 grading rubric (items 1, 2 and 3). It is intentionally written with
the standard library only (``json`` / ``math``) so it stays independent of the
scheduler under test and can run without the project's heavy dependencies.

Constraint C4 (aperiodic miss, rubric item 2-2) is reported as SKIPPED because
aperiodic handling is not implemented yet.

Run with::

    python -m src.validator
    python -m src.validator --task-set output/task_set.json \\
        --schedule output/schedule_result.json \\
        --settings input/processor_settings.json
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from typing import Any


# Tolerance for floating-point comparisons. The extractor rounds to 4 decimals,
# so per-tick residuals are tiny; SOC balance can accumulate over the horizon,
# hence a slightly loose threshold that still catches whole-MWh violations.
_EPS = 1e-2
_HORIZON = 72


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


class Level1Validator:
    """Validates Level 1 rubric items against the produced JSON artifacts."""

    def __init__(
        self,
        task_set: dict[str, Any],
        schedule: dict[str, Any],
        settings: dict[str, Any],
        horizon: int = _HORIZON,
        eps: float = _EPS,
    ) -> None:
        """Initializes the validator with already-loaded JSON dictionaries.

        Args:
            task_set: Parsed ``task_set.json`` content.
            schedule: Parsed ``schedule_result.json`` content.
            settings: Parsed ``processor_settings.json`` content.
            horizon: Scheduling horizon length in ticks.
            eps: Floating-point comparison tolerance.
        """
        self._eps = eps
        self._horizon = horizon

        self._frame_size = task_set.get("frame_size")
        self._tasks: dict[str, dict[str, Any]] = task_set.get("periodic", {})

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
            k = 0
            while True:
                rel = t["r"] + k * t["p"]
                dl = rel + t["d"] - 1
                if rel > self._horizon or dl > self._horizon:
                    break
                jobs.append(
                    _Job(
                        job_id=f"{tid}_{k}",
                        task_id=tid,
                        release=rel,
                        deadline=dl,
                        execution=t["e"],
                        demand=t["w"],
                        preemptive=(t["preempt"] == 1),
                    )
                )
                k += 1
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

        # 2-2 (2 pts): C4 aperiodic -- not implemented yet.
        skipped = CheckResult(
            "2-2", "aperiodic constraint C4", 2, status="SKIP"
        )
        skipped.violations.append("aperiodic not implemented -- cannot verify")
        out.append(skipped)

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
        return self.check_item1() + self.check_item2() + self.check_item3()


def _print_report(results: list[CheckResult]) -> float:
    """Prints a human-readable report and returns the total self-grade."""
    symbol = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}
    total = 0.0
    total_max = 0.0
    print("=" * 72)
    print("  VPP-RTS Level 1 self-check")
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
    print("  NOTE: items 2-2 (aperiodic), 4, 5, 6 are not covered here.")
    print("=" * 72)
    return total


def main() -> None:
    """CLI entry point for the Level 1 self-check."""
    parser = argparse.ArgumentParser(description="VPP-RTS Level 1 self-validator")
    parser.add_argument("--task-set", default="output/task_set.json")
    parser.add_argument("--schedule", default="output/schedule_result.json")
    parser.add_argument("--settings", default="input/processor_settings.json")
    parser.add_argument("--horizon", type=int, default=_HORIZON)
    parser.add_argument("--eps", type=float, default=_EPS)
    args = parser.parse_args()

    validator = Level1Validator(
        task_set=_load_json(args.task_set),
        schedule=_load_json(args.schedule),
        settings=_load_json(args.settings),
        horizon=args.horizon,
        eps=args.eps,
    )
    total = _print_report(validator.validate())
    # Non-zero exit if any covered constraint is violated, for CI use.
    failed = any(r.status == "FAIL" for r in validator.validate())
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
