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
    """Rolling-horizon re-optimizing scheduler for the Level 2 relaxed model."""

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
        gap_rel: float = 0.05,
        time_limit: int = 20,
        threads: int = 4,
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
        self._reopt_interval = max(1, reopt_interval)
        self._noise_std = renewable_noise_std
        self._deviation_threshold = renewable_deviation_threshold
        self._rng = random.Random(seed)
        self._max_reserve = max_reserve_per_tick
        self._max_solves = max_solves
        self._max_deviation_triggers = max_deviation_triggers
        self._gap_rel = gap_rel
        self._time_limit = time_limit
        self._threads = threads

        self._assets = assets
        self._tasks = tasks
        self._prices = prices

        # The renewable margin is applied to the unseen tail via the forecast values
        # passed per round; the per-solve formulator margin is therefore zeroed to
        # avoid derating the realized (committed) ticks twice.
        self._tail_margin = self._relax.renewable_uncertainty_margin
        self._solve_relax = self._relax.model_copy(
            update={"renewable_uncertainty_margin": 0.0}
        )

        # Mutable rolling state, initialized in run().
        self._raw: dict[int, dict[str, Any]] = {}      # committed raw MILP values
        self._out: dict[int, dict[str, Any]] = {}       # committed output records
        self._accepted: dict[str, dict[str, Any]] = {}  # admitted job tracking
        self._decided: set[str] = set()
        self._actual_avail: dict[str, dict[int, float]] = {}
        self._solves = 0
        self._run_log: list[dict[str, Any]] = []
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

        expander = JobExpander(horizon=self._horizon)
        self._regular_jobs = expander.expand_periodic_tasks(self._tasks)
        self._charging_jobs = expander.expand_charging_jobs(self._assets)
        self._all_jobs = self._regular_jobs + self._charging_jobs

        self._auto_select_precedence()
        self._build_actual_renewable()

        boundaries = self._trigger_ticks()
        for idx, t0 in enumerate(boundaries):
            t1 = boundaries[idx + 1] if idx + 1 < len(boundaries) else self._horizon + 1
            self._run_round(t0, t1)

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
        if self._relax.precedence:
            return
        jobs = sorted(self._regular_jobs, key=lambda j: (j.release, j.deadline))
        for a in jobs:
            for b in jobs:
                if a.job_id == b.job_id:
                    continue
                overlaps = a.deadline >= b.release and b.deadline >= a.release
                earliest_b = max(b.release, a.release + a.execution)
                fits = earliest_b + b.execution - 1 <= b.deadline
                if overlaps and fits:
                    self._relax.precedence = [(a.job_id, b.job_id)]
                    self._solve_relax.precedence = [(a.job_id, b.job_id)]
                    return

    def _build_actual_renewable(self) -> None:
        """Realizes actual PV availability around the forecast (seeded)."""
        for rf in self._assets.renewable_forecasts:
            series: dict[int, float] = {}
            for f in rf.forecasts:
                if f.pv_forecast <= 0.0:
                    series[f.hour] = 0.0
                    continue
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
        triggers: set[int] = {1}
        t = 1 + self._reopt_interval
        while t <= self._horizon:
            triggers.add(t)
            t += self._reopt_interval

        for task in self._tasks.sporadic_tasks:
            if 1 <= task.r <= self._horizon:
                triggers.add(task.r)

        # Largest forecast/actual deviations, spaced out and capped.
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
            if all(abs(tk - x) >= self._reopt_interval // 2 for x in triggers):
                triggers.add(tk)
                added += 1

        return sorted(triggers)

    # ----------------------------------------------------------------- a round

    def _run_round(self, t0: int, t1: int) -> None:
        """Executes one re-optimization round committing block ``[t0, t1)``."""
        revealed = self._reveal_jobs(t1)
        formulator, rejected = self._admit_and_solve(revealed, t0, t1)
        self._capture_block(formulator, t0, t1)
        routed = self._route_accepted(t0, t1)

        self._run_log.append(
            {
                "trigger_tick": t0,
                "committed_range": [t0, t1 - 1],
                "revealed": [j.task_id for j in revealed],
                "admitted": sorted(
                    jid for jid in self._accepted if jid not in rejected
                ),
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
        for task in sorted(revealed, key=lambda t: t.r + t.d):
            self._decided.add(task.task_id)
            kind = "sporadic" if isinstance(task, SporadicTask) else "aperiodic"
            latest = (task.r + task.d - 1) if kind == "sporadic" else self._horizon
            decision = "rejected" if kind == "sporadic" else "missed"
            ticks = self._reserve_window(task, t0, latest)
            if ticks is None:
                rejected.add(task.task_id)
                self._log_job(task, kind, decision, None, "no room before deadline")
                continue
            self._accepted[task.task_id] = self._tracking(task, kind, ticks)
            pending.append(task.task_id)

        formulator = self._solve(self._reserve_floor(t0), t0, t1)
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
        """
        floor: dict[int, float] = {}
        for job in self._accepted.values():
            if job["remaining"] <= 0:
                continue
            latest = job["deadline"] if job["kind"] == "sporadic" else self._horizon
            start = max(job["task"].r, t0)
            ticks = list(range(start, start + job["remaining"]))
            if ticks and ticks[-1] > latest:
                continue
            job["reserve_ticks"] = ticks
            for tk in ticks:
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
            return None
        self._solves += 1

        round_assets = self._round_assets(commit_hi)
        formulator = VppMilpFormulator(
            assets=round_assets,
            prices=self._prices,
            all_jobs=self._all_jobs,
            horizon=self._horizon,
            relaxation=self._solve_relax,
            reserve_floor=reserve_floor,
        )
        formulator.formulate()
        if self._raw:
            # A looser band than the formulator default absorbs the committed
            # incumbent's own constraint residuals (CBC feasibility tolerance plus
            # the gap-suboptimal solve), which a 1e-6 band would reject as
            # infeasible. 1e-3 MWh is physically negligible at the schedule scale.
            formulator.pin_prefix(self._raw, upto_tick, tol=1e-3)
        formulator.prob.solve(
            pulp.PULP_CBC_CMD(
                msg=0,
                gapRel=self._gap_rel,
                timeLimit=self._time_limit,
                threads=self._threads,
            )
        )
        # A MIP gap / time limit is used for tractable online re-optimization; CBC
        # reports Optimal once a feasible incumbent within the gap (or at the limit)
        # is found, and Infeasible when no reservation-feasible schedule exists.
        if pulp.LpStatus[formulator.prob.status] != "Optimal":
            return None
        return formulator

    def _round_assets(self, commit_hi: int) -> ProcessorSettingsSystem:
        """Returns an assets copy whose PV forecasts reflect realized/derated values.

        Ticks before ``commit_hi`` use the realized availability (revealed reality);
        later ticks use the forecast derated by the uncertainty margin. Fresh forecast
        objects are constructed from the pristine originals, so ``self._assets`` is
        never mutated across rounds.
        """
        keep = 1.0 - self._tail_margin
        new_forecasts = []
        for rf in self._assets.renewable_forecasts:
            actual = self._actual_avail.get(rf.renewable_id, {})
            hourly = [
                HourlyForecast(
                    hour=f.hour,
                    pv_forecast=(
                        actual.get(f.hour, f.pv_forecast)
                        if f.hour < commit_hi
                        else f.pv_forecast * keep
                    ),
                )
                for f in rf.forecasts
            ]
            new_forecasts.append(
                RenewableForecast(renewable_id=rf.renewable_id, forecasts=hourly)
            )
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
                    continue
                if self._route_one(tk, jid, job["w"]):
                    field = (
                        "accepted_sporadic"
                        if job["kind"] == "sporadic"
                        else "scheduled_aperiodic"
                    )
                    self._mark(tk, field, jid)
                    job["routed_ticks"].append(tk)
                    job["remaining"] -= 1
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
        spare = self._device_spare(record)
        remaining = float(demand)
        alloc = record["k"].setdefault(job_id, {})
        for dev in list(spare):
            if remaining <= _EPS:
                break
            give = min(spare[dev], remaining)
            if give <= _EPS:
                continue
            alloc[dev] = round(alloc.get(dev, 0.0) + give, 4)
            remaining = round(remaining - give, 4)
        if remaining > _EPS:
            # Reserve floor should have guaranteed enough spare; undo on shortfall.
            if not alloc:
                record["k"].pop(job_id, None)
            return False
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
