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
        # Per-job accept/reject decision records, populated during run().
        self._log: dict[str, list[dict[str, Any]]] = {
            "sporadic": [],
            "aperiodic": [],
        }

    def run(self, tasks: TaskSystem) -> dict[str, Any]:
        """Runs acceptance tests and updates schedule annotations.

        Args:
            tasks: Task aggregate containing sporadic and aperiodic jobs.

        Returns:
            Dict with the updated ``schedule_result``, the leftover ``reserve``
            (remaining redirectable budget per tick after allocation), and a
            ``log`` capturing each job's accept/reject decision and rationale.
        """
        self._ensure_result_fields()
        self._schedule_sporadic_tasks(tasks.sporadic_tasks)
        self._schedule_aperiodic_tasks(tasks.aperiodic_tasks)

        reserve = {t: round(sum(s.values()), 4) for t, s in self._spare.items()}
        return {
            "schedule_result": self._schedule_result,
            "reserve": reserve,
            "log": self._compose_log(reserve),
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
            preemptive = task.preempt == 1
            hard_deadline = self._absolute_deadline(task.r, task.d)
            slots = self._find_slots(
                release=task.r,
                latest=hard_deadline,
                execution=task.e,
                demand=task.w,
                preemptive=preemptive,
            )

            entry: dict[str, Any] = {
                "task_id": task.task_id,
                "release": task.r,
                "relative_deadline": task.d,
                "absolute_deadline": hard_deadline,
                "execution": task.e,
                "demand": task.w,
                "preemptive": preemptive,
            }

            if not slots:
                self._mark_task(task.r, "rejected_sporadic", task.task_id)
                entry.update(
                    decision="rejected",
                    scheduled_slots=[],
                    completion_tick=None,
                    reason=self._infeasible_reason(
                        "rejected",
                        task.r,
                        hard_deadline,
                        task.e,
                        task.w,
                        preemptive,
                    ),
                )
                self._log["sporadic"].append(entry)
                continue

            for tick in slots:
                self._route(tick, task.task_id, task.w)
                self._mark_task(tick, "accepted_sporadic", task.task_id)
            entry.update(
                decision="accepted",
                scheduled_slots=slots,
                completion_tick=max(slots),
                reason=(
                    f"accepted: routed {task.e} slot(s) of {task.w} MWh from "
                    f"day-ahead reserve within [{task.r}, {hard_deadline}]"
                ),
            )
            self._log["sporadic"].append(entry)

    # -------------------------------------------------------------- aperiodic

    def _schedule_aperiodic_tasks(self, tasks: list[AperiodicTask]) -> None:
        """Schedules aperiodic jobs to complete by H, flagging soft-deadline misses.

        Constraint C4 requires every aperiodic job to complete all ``e`` slots by
        the horizon end, even when it overruns its soft deadline. A job whose
        last slot lands after the soft deadline is additionally flagged missed.
        """
        for task in tasks:
            preemptive = task.preempt == 1
            soft_deadline = self._absolute_deadline(task.r, task.d)
            slots = self._find_slots(
                release=task.r,
                latest=self._horizon,
                execution=task.e,
                demand=task.w,
                preemptive=preemptive,
            )

            entry: dict[str, Any] = {
                "task_id": task.task_id,
                "release": task.r,
                "relative_deadline": task.d,
                "soft_deadline": soft_deadline,
                "execution": task.e,
                "demand": task.w,
                "preemptive": preemptive,
            }

            if not slots:
                # No reserve to complete it anywhere before H -> pure miss.
                self._mark_task(task.r, "missed_aperiodic", task.task_id)
                entry.update(
                    decision="missed",
                    missed=True,
                    scheduled_slots=[],
                    completion_tick=None,
                    reason=self._infeasible_reason(
                        "missed",
                        task.r,
                        self._horizon,
                        task.e,
                        task.w,
                        preemptive,
                    ),
                )
                self._log["aperiodic"].append(entry)
                continue

            for tick in slots:
                self._route(tick, task.task_id, task.w)
                self._mark_task(tick, "scheduled_aperiodic", task.task_id)

            completion = max(slots)
            late = completion > soft_deadline
            if late:
                self._mark_task(task.r, "missed_aperiodic", task.task_id)
            entry.update(
                decision="scheduled",
                missed=late,
                scheduled_slots=slots,
                completion_tick=completion,
                reason=(
                    f"scheduled {task.e} slot(s); completes at tick {completion} "
                    + (
                        f"after soft deadline {soft_deadline} -> soft miss"
                        if late
                        else f"within soft deadline {soft_deadline}"
                    )
                ),
            )
            self._log["aperiodic"].append(entry)

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

    def _infeasible_reason(
        self,
        verdict: str,
        release: int,
        latest: int,
        execution: int,
        demand: int,
        preemptive: bool,
    ) -> str:
        """Explains why a job could not be placed against the remaining reserve.

        Args:
            verdict: Decision label to prefix the message with (``rejected`` for
                sporadic jobs, ``missed`` for aperiodic jobs that cannot finish
                anywhere before the horizon end).
            release: Earliest tick the job may run.
            latest: Latest tick considered (hard deadline, or H for soft jobs).
            execution: Number of slots the job needs.
            demand: Energy required at each slot (MWh).
            preemptive: Whether the job may use non-contiguous slots.

        Returns:
            A human-readable rationale referencing the actual reserve shortfall.
        """
        feasible = sum(
            1
            for t in range(release, latest + 1)
            if self._budget(t) >= demand - _EPS
        )
        if preemptive:
            return (
                f"{verdict}: only {feasible} of {execution} required tick(s) in "
                f"[{release}, {latest}] have >= {demand} MWh reserve"
            )
        return (
            f"{verdict}: no contiguous {execution}-tick window in "
            f"[{release}, {latest}] has >= {demand} MWh reserve in every tick "
            f"({feasible} feasible tick(s) total)"
        )

    def _compose_log(self, reserve: dict[int, float]) -> dict[str, Any]:
        """Assembles the acceptance-test log in the format required by the grader.

        The top-level ``acceptance_test_log`` array is a flat list combining all
        sporadic and aperiodic decisions, using the field names mandated by the
        demo grading script:

            job_id        – task identifier
            type          – "sporadic" or "aperiodic"
            release_time  – absolute release tick
            abs_deadline  – absolute deadline tick (hard for sporadic, soft for
                            aperiodic)
            execution_time – number of slots required
            energy_demand  – MWh required per slot
            assigned_hours – list of ticks actually scheduled ([] if rejected/missed)
            accepted       – true when slots were allocated; false otherwise

        A ``summary`` block and the original per-job detail lists are retained
        alongside for evaluation and rubric items 4-1 / 4-2.

        Args:
            reserve: Leftover redirectable reserve per tick after allocation.

        Returns:
            A serializable log dict containing ``acceptance_test_log``,
            ``summary``, ``sporadic``, and ``aperiodic`` sections.
        """
        sporadic = self._log["sporadic"]
        aperiodic = self._log["aperiodic"]
        accepted = [e for e in sporadic if e["decision"] == "accepted"]
        total_exec = sum(e["execution"] for e in sporadic)
        done_exec = sum(e["execution"] for e in accepted)
        value_rate = round(done_exec / total_exec, 4) if total_exec else 0.0

        # ── 助教規定的統一格式（扁平陣列，sporadic + aperiodic 合併） ──────────
        acceptance_test_log: list[dict[str, Any]] = []
        for entry in sporadic:
            acceptance_test_log.append({
                "job_id": entry["task_id"],
                "type": "sporadic",
                "release_time": entry["release"],
                "abs_deadline": entry["absolute_deadline"],
                "execution_time": entry["execution"],
                "energy_demand": entry["demand"],
                "assigned_hours": entry.get("scheduled_slots", []),
                "accepted": entry["decision"] == "accepted",
            })
        for entry in aperiodic:
            acceptance_test_log.append({
                "job_id": entry["task_id"],
                "type": "aperiodic",
                "release_time": entry["release"],
                "abs_deadline": entry["soft_deadline"],
                "execution_time": entry["execution"],
                "energy_demand": entry["demand"],
                "assigned_hours": entry.get("scheduled_slots", []),
                # aperiodic：有取得排程槽即視為 accepted（即使超過軟 deadline）
                "accepted": bool(entry.get("scheduled_slots")),
            })

        return {
            # 助教要求的頂層格式
            "acceptance_test_log": acceptance_test_log,
            # 以下保留供內部評估與 4-1/4-2 評分項目使用
            "summary": {
                "horizon": self._horizon,
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
                    "missed": sum(1 for e in aperiodic if e["missed"]),
                },
                "sporadic_value_rate": value_rate,
                "reserve_after_acceptance": reserve,
            },
            "sporadic": sporadic,
            "aperiodic": aperiodic,
        }

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
