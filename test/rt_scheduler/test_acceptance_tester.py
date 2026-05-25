"""Tests for Phase 3 sporadic and aperiodic acceptance testing.

The AcceptanceTester operates on solved day-ahead records. Each record exposes a
per-device output ``P`` and a ``sell`` value; the redirectable reserve at a tick
is the device spare ``P[i] - sum_j k[j][i]`` (which equals ``sell``). These
helpers model that by giving each tick a single device ``g1`` whose entire
output is currently sold, so its spare equals the desired reserve.
"""

from src.model import AperiodicTask, SporadicTask, TaskSystem
from src.rt_scheduler.acceptance_tester import AcceptanceTester


def _schedule(reserve: dict[int, float], horizon: int = 6) -> list[dict]:
    """Builds schedule records whose per-tick spare equals ``reserve``."""
    return [
        {
            "t": t,
            "P": {"g1": float(reserve.get(t, 0.0))},
            "k": {},
            "sell": float(reserve.get(t, 0.0)),
            "soc": {},
            "missed_aperiodic": [],
            "rejected_sporadic": [],
        }
        for t in range(1, horizon + 1)
    ]


def _ticks_with(result: dict, job_id: str, field: str) -> list[int]:
    return [
        record["t"]
        for record in result["schedule_result"]
        if job_id in record[field]
    ]


def test_accepts_preemptive_sporadic_when_enough_reserve() -> None:
    """A preemptive sporadic job can use non-contiguous reserve slots."""
    schedule = _schedule({1: 10.0, 2: 3.0, 3: 10.0, 4: 10.0, 5: 0.0, 6: 0.0})
    tasks = TaskSystem(
        periodic_tasks=[],
        sporadic_tasks=[SporadicTask(task_id="s1", r=1, e=3, d=4, w=10, preempt=1)],
        aperiodic_tasks=[],
    )

    result = AcceptanceTester(schedule).run(tasks)
    by_tick = {r["t"]: r for r in result["schedule_result"]}

    assert _ticks_with(result, "s1", "accepted_sporadic") == [1, 3, 4]
    # Demand is routed into k and the sold surplus shrinks to match.
    assert by_tick[1]["k"]["s1"] == {"g1": 10.0}
    assert by_tick[1]["sell"] == 0.0
    assert result["reserve"][1] == 0.0
    assert result["reserve"][3] == 0.0
    assert result["reserve"][4] == 0.0


def test_rejects_sporadic_when_not_enough_reserve() -> None:
    """A sporadic job is rejected when it cannot finish by its hard deadline."""
    schedule = _schedule({1: 10.0, 2: 3.0, 3: 10.0, 4: 0.0, 5: 0.0, 6: 0.0})
    tasks = TaskSystem(
        periodic_tasks=[],
        sporadic_tasks=[SporadicTask(task_id="s1", r=1, e=3, d=4, w=10, preempt=1)],
        aperiodic_tasks=[],
    )

    result = AcceptanceTester(schedule).run(tasks)

    assert result["schedule_result"][0]["rejected_sporadic"] == ["s1"]
    # Nothing routed: reserve and sell are untouched.
    assert all(not r["k"] for r in result["schedule_result"])
    assert result["reserve"] == {1: 10.0, 2: 3.0, 3: 10.0, 4: 0.0, 5: 0.0, 6: 0.0}


def test_non_preemptive_sporadic_requires_contiguous_slots() -> None:
    """A non-preemptive sporadic job needs one continuous reserve window."""
    schedule = _schedule({1: 10.0, 2: 0.0, 3: 10.0, 4: 10.0, 5: 10.0, 6: 0.0})
    tasks = TaskSystem(
        periodic_tasks=[],
        sporadic_tasks=[SporadicTask(task_id="s1", r=1, e=3, d=5, w=10, preempt=0)],
        aperiodic_tasks=[],
    )

    result = AcceptanceTester(schedule).run(tasks)

    assert _ticks_with(result, "s1", "accepted_sporadic") == [3, 4, 5]


def test_schedules_aperiodic_when_reserve_available() -> None:
    """An aperiodic job scheduled within its soft deadline is not flagged missed."""
    schedule = _schedule({1: 5.0, 2: 5.0, 3: 5.0, 4: 0.0, 5: 0.0, 6: 0.0})
    tasks = TaskSystem(
        periodic_tasks=[],
        sporadic_tasks=[],
        aperiodic_tasks=[AperiodicTask(task_id="a1", r=1, e=2, d=3, w=5, preempt=1)],
    )

    result = AcceptanceTester(schedule).run(tasks)

    assert _ticks_with(result, "a1", "scheduled_aperiodic") == [1, 2]
    assert _ticks_with(result, "a1", "missed_aperiodic") == []
    assert result["reserve"][1] == 0.0
    assert result["reserve"][2] == 0.0


def test_aperiodic_completes_after_soft_deadline_is_scheduled_but_missed() -> None:
    """C4: an aperiodic job still completes all e slots by H, flagged missed if late."""
    # Reserve only at ticks 1 and 4; soft deadline is tick 2 (r=1, d=2).
    schedule = _schedule({1: 5.0, 2: 0.0, 3: 0.0, 4: 5.0, 5: 0.0, 6: 0.0})
    tasks = TaskSystem(
        periodic_tasks=[],
        sporadic_tasks=[],
        aperiodic_tasks=[AperiodicTask(task_id="a1", r=1, e=2, d=2, w=5, preempt=1)],
    )

    result = AcceptanceTester(schedule).run(tasks)

    # All e=2 slots complete by the horizon end (ticks 1 and 4)...
    assert _ticks_with(result, "a1", "scheduled_aperiodic") == [1, 4]
    # ...but the last slot (tick 4) is past the soft deadline (tick 2) -> missed.
    assert "a1" in result["schedule_result"][0]["missed_aperiodic"]


def test_marks_aperiodic_missed_when_cannot_complete_by_horizon() -> None:
    """An aperiodic job is marked missed when it cannot finish even by H."""
    schedule = _schedule({1: 5.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0, 6: 0.0})
    tasks = TaskSystem(
        periodic_tasks=[],
        sporadic_tasks=[],
        aperiodic_tasks=[AperiodicTask(task_id="a1", r=1, e=2, d=3, w=5, preempt=1)],
    )

    result = AcceptanceTester(schedule).run(tasks)

    assert result["schedule_result"][0]["missed_aperiodic"] == ["a1"]
    assert _ticks_with(result, "a1", "scheduled_aperiodic") == []
    # Insufficient reserve -> nothing routed, reserve untouched.
    assert result["reserve"][1] == 5.0
