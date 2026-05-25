"""Acceptance testing for sporadic and aperiodic real-time jobs.

Phase 3 runs after the day-ahead MILP (Phase 2). The MILP leaves a per-tick
*reserve*: the surplus generation it would otherwise dump to the market. This
module redirects that reserve to extra real-time jobs.

The reserve at tick ``t`` equals ``sell[t]`` (the surplus being sold), and it is
distributed across devices as the per-device *spare* ``P[i][t] - sum_j k[j][i][t]``
(this sums to ``sell[t]`` by the power-balance constraint C23). When a job is
accepted at a tick, its demand ``w`` is routed from device spare into
``k[job][device]`` and ``sell[t]`` is reduced by the same amount -- so total
generation is unchanged and both C23 (balance) and C20 (per-device output cap)
stay satisfied.

Sporadic jobs carry hard deadlines: a job is accepted only when ``e`` slots of
demand can be routed within ``[r, r + d - 1]``; otherwise it is rejected.
Aperiodic jobs carry soft deadlines: they are scheduled to complete all ``e``
slots by the horizon end ``H`` (constraint C4), and flagged as missed when
completion falls after the soft deadline.
"""

from typing import Any

from src.model import AperiodicTask, SporadicTask, TaskSystem

# Treat anything below this (in MWh) as zero when matching demand against spare.
_EPS = 1e-4


class AcceptanceTester:
    """Schedules extra jobs against the reserve left by the day-ahead schedule."""

    def __init__(self, schedule_result: list[dict[str, Any]]) -> None:
        """Initializes the tester with the solved day-ahead schedule records.

        Args:
            schedule_result: Schedule records produced by SchedulerResultExtractor,
                each carrying ``t``, ``P``, ``k``, ``sell`` and ``soc`` fields.
        """
        self._schedule_result = schedule_result
        self._records_by_tick = {
            int(record["t"]): record for record in self._schedule_result
        }
        self._horizon = max(self._records_by_tick, default=0)
        self._spare = self._compute_device_spare()

    def run(self, tasks: TaskSystem) -> dict[str, Any]:
        """Runs acceptance tests and updates schedule annotations.

        Args:
            tasks: Task aggregate containing sporadic and aperiodic jobs.

        Returns:
            Dict with the updated ``schedule_result`` and the leftover ``reserve``
            (remaining redirectable budget per tick after allocation).
        """
        self._ensure_result_fields()
        self._schedule_sporadic_tasks(tasks.sporadic_tasks)
        self._schedule_aperiodic_tasks(tasks.aperiodic_tasks)

        return {
            "schedule_result": self._schedule_result,
            "reserve": {t: round(sum(s.values()), 4) for t, s in self._spare.items()},
        }

    # ------------------------------------------------------------------ setup

    def _compute_device_spare(self) -> dict[int, dict[str, float]]:
        """Builds the per-tick, per-device spare output left unused by Phase 2.

        Spare for device ``i`` at tick ``t`` is its output ``P[i][t]`` minus the
        energy already routed from it to existing (periodic and charging) jobs.
        """
        spare: dict[int, dict[str, float]] = {}
        for tick, record in self._records_by_tick.items():
            routed: dict[str, float] = {}
            for alloc in record.get("k", {}).values():
                for dev, val in alloc.items():
                    routed[dev] = routed.get(dev, 0.0) + float(val)
            spare[tick] = {
                dev: max(0.0, round(float(p) - routed.get(dev, 0.0), 4))
                for dev, p in record.get("P", {}).items()
            }
        return spare

    def _ensure_result_fields(self) -> None:
        """Ensures Phase 3 result fields exist for every schedule record."""
        for record in self._schedule_result:
            record.setdefault("accepted_sporadic", [])
            record.setdefault("scheduled_aperiodic", [])
            record.setdefault("missed_aperiodic", [])
            record.setdefault("rejected_sporadic", [])

    # -------------------------------------------------------------- sporadic

    def _schedule_sporadic_tasks(self, tasks: list[SporadicTask]) -> None:
        """Accepts only sporadic jobs that can complete before their hard deadline."""
        for task in tasks:
            slots = self._find_slots(
                release=task.r,
                latest=self._absolute_deadline(task.r, task.d),
                execution=task.e,
                demand=task.w,
                preemptive=task.preempt == 1,
            )

            if not slots:
                self._mark_task(task.r, "rejected_sporadic", task.task_id)
                continue

            for tick in slots:
                self._route(tick, task.task_id, task.w)
                self._mark_task(tick, "accepted_sporadic", task.task_id)

    # -------------------------------------------------------------- aperiodic

    def _schedule_aperiodic_tasks(self, tasks: list[AperiodicTask]) -> None:
        """Schedules aperiodic jobs to complete by H, flagging soft-deadline misses.

        Constraint C4 requires every aperiodic job to complete all ``e`` slots by
        the horizon end, even when it overruns its soft deadline. A job whose
        last slot lands after the soft deadline is additionally flagged missed.
        """
        for task in tasks:
            soft_deadline = self._absolute_deadline(task.r, task.d)
            slots = self._find_slots(
                release=task.r,
                latest=self._horizon,
                execution=task.e,
                demand=task.w,
                preemptive=task.preempt == 1,
            )

            if not slots:
                # No reserve to complete it anywhere before H -> pure miss.
                self._mark_task(task.r, "missed_aperiodic", task.task_id)
                continue

            for tick in slots:
                self._route(tick, task.task_id, task.w)
                self._mark_task(tick, "scheduled_aperiodic", task.task_id)

            if max(slots) > soft_deadline:
                self._mark_task(task.r, "missed_aperiodic", task.task_id)

    # ----------------------------------------------------------- allocation

    def _find_slots(
        self,
        release: int,
        latest: int,
        execution: int,
        demand: int,
        preemptive: bool,
    ) -> list[int]:
        """Finds ticks whose remaining reserve can satisfy the job's demand.

        Args:
            release: Earliest tick the job may run.
            latest: Latest tick the job may run (hard deadline, or H for soft).
            execution: Number of slots the job needs.
            demand: Energy required at each slot (MWh).
            preemptive: Whether the job may use non-contiguous slots.

        Returns:
            The chosen execution ticks, or an empty list if it cannot fit.
        """
        if preemptive:
            slots = [
                t
                for t in range(release, latest + 1)
                if self._budget(t) >= demand - _EPS
            ]
            return slots[:execution] if len(slots) >= execution else []

        latest_start = latest - execution + 1
        for start in range(release, latest_start + 1):
            window = list(range(start, start + execution))
            if all(self._budget(t) >= demand - _EPS for t in window):
                return window
        return []

    def _budget(self, tick: int) -> float:
        """Returns the remaining redirectable reserve at a tick."""
        return sum(self._spare.get(tick, {}).values())

    def _route(self, tick: int, job_id: str, demand: int) -> None:
        """Routes ``demand`` MWh from device spare into k[job][device] at ``tick``.

        Pulls greedily from each device's spare, records the allocation, shrinks
        the spare, and reduces ``sell`` by the routed amount so power balance (C23)
        is preserved.
        """
        record = self._records_by_tick.get(tick)
        if record is None:
            return

        spare_t = self._spare[tick]
        alloc = record["k"].setdefault(job_id, {})
        remaining = float(demand)
        for dev in list(spare_t):
            if remaining <= _EPS:
                break
            give = min(spare_t[dev], remaining)
            if give <= _EPS:
                continue
            alloc[dev] = round(alloc.get(dev, 0.0) + give, 4)
            spare_t[dev] = round(spare_t[dev] - give, 4)
            remaining = round(remaining - give, 4)

        routed = demand - remaining
        record["sell"] = round(float(record.get("sell", 0.0)) - routed, 4)

    def _mark_task(self, tick: int, field_name: str, task_id: str) -> None:
        """Adds a task ID to a schedule record field when the tick exists."""
        record = self._records_by_tick.get(tick)
        if record is None:
            return
        if task_id not in record[field_name]:
            record[field_name].append(task_id)

    def _absolute_deadline(self, release: int, relative_deadline: int) -> int:
        """Converts a relative deadline into an absolute schedule tick."""
        return release + relative_deadline - 1
