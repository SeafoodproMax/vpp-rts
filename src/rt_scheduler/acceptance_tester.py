"""Acceptance testing for sporadic and aperiodic real-time jobs."""

from typing import Any

from src.model import AperiodicTask, SporadicTask, TaskSystem


class AcceptanceTester:
    """Schedules extra jobs against reserves left by the day-ahead schedule."""

    def __init__(
        self,
        schedule_result: list[dict[str, Any]],
        reserve: dict[int, float],
    ) -> None:
        """Initializes the tester with solved schedule records and reserves.

        Args:
            schedule_result: Schedule records produced by SchedulerResultExtractor.
            reserve: Available reserve power indexed by time tick.
        """
        self._schedule_result = schedule_result
        self._reserve = {int(t): float(v) for t, v in reserve.items()}
        self._records_by_tick = {
            int(record["t"]): record for record in self._schedule_result
        }

    def run(self, tasks: TaskSystem) -> dict[str, Any]:
        """Runs acceptance tests and updates schedule annotations.

        Args:
            tasks: Task aggregate containing sporadic and aperiodic jobs.

        Returns:
            Updated schedule result and reserve mapping.
        """
        self._ensure_result_fields()
        self._schedule_sporadic_tasks(tasks.sporadic_tasks)
        self._schedule_aperiodic_tasks(tasks.aperiodic_tasks)

        return {
            "schedule_result": self._schedule_result,
            "reserve": self._reserve,
        }

    def _ensure_result_fields(self) -> None:
        """Ensures Phase 3 result fields exist for every schedule record."""
        for record in self._schedule_result:
            record.setdefault("accepted_sporadic", [])
            record.setdefault("scheduled_aperiodic", [])
            record.setdefault("missed_aperiodic", [])
            record.setdefault("rejected_sporadic", [])

    def _schedule_sporadic_tasks(self, tasks: list[SporadicTask]) -> None:
        """Accepts only sporadic jobs that can complete before deadline."""
        for task in tasks:
            slots = self._find_schedulable_slots(
                release=task.r,
                deadline=self._absolute_deadline(task.r, task.d),
                execution=task.e,
                demand=task.w,
                preemptive=task.preempt == 1,
            )

            if not slots:
                self._mark_task(task.r, "rejected_sporadic", task.task_id)
                continue

            self._assign_task(slots, task.task_id, task.w, "accepted_sporadic")

    def _schedule_aperiodic_tasks(self, tasks: list[AperiodicTask]) -> None:
        """Schedules aperiodic jobs when enough reserve is available."""
        for task in tasks:
            slots = self._find_schedulable_slots(
                release=task.r,
                deadline=self._absolute_deadline(task.r, task.d),
                execution=task.e,
                demand=task.w,
                preemptive=task.preempt == 1,
            )

            if not slots:
                self._mark_task(task.r, "missed_aperiodic", task.task_id)
                continue

            self._assign_task(slots, task.task_id, task.w, "scheduled_aperiodic")

    def _find_schedulable_slots(
        self,
        release: int,
        deadline: int,
        execution: int,
        demand: int,
        preemptive: bool,
    ) -> list[int]:
        """Finds slots that can satisfy a job's execution and demand."""
        if preemptive:
            slots = [
                t
                for t in range(release, deadline + 1)
                if self._reserve.get(t, 0.0) >= demand
            ]
            return slots[:execution] if len(slots) >= execution else []

        return self._find_contiguous_slots(release, deadline, execution, demand)

    def _find_contiguous_slots(
        self,
        release: int,
        deadline: int,
        execution: int,
        demand: int,
    ) -> list[int]:
        """Finds a contiguous non-preemptive execution window."""
        latest_start = deadline - execution + 1
        for start in range(release, latest_start + 1):
            slots = list(range(start, start + execution))
            if all(self._reserve.get(t, 0.0) >= demand for t in slots):
                return slots
        return []

    def _assign_task(
        self,
        slots: list[int],
        task_id: str,
        demand: int,
        field_name: str,
    ) -> None:
        """Assigns a task to reserve slots and annotates schedule records."""
        for t in slots:
            self._reserve[t] = round(self._reserve[t] - demand, 4)
            self._mark_task(t, field_name, task_id)

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
