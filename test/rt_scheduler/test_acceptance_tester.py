"""Tests for Phase 3 sporadic and aperiodic acceptance testing."""

from src.model import AperiodicTask, SporadicTask, TaskSystem
from src.rt_scheduler.acceptance_tester import AcceptanceTester


def _schedule(horizon: int = 6) -> list[dict]:
    return [
        {
            "t": t,
            "P": {},
            "k": {},
            "sell": 0.0,
            "soc": {},
            "missed_aperiodic": [],
            "rejected_sporadic": [],
        }
        for t in range(1, horizon + 1)
    ]


def test_accepts_preemptive_sporadic_when_enough_reserve() -> None:
    """A preemptive sporadic job can use non-contiguous reserve slots."""
    schedule = _schedule()
    reserve = {1: 10.0, 2: 3.0, 3: 10.0, 4: 10.0, 5: 0.0, 6: 0.0}
    tasks = TaskSystem(
        periodic_tasks=[],
        sporadic_tasks=[
            SporadicTask(task_id="s1", r=1, e=3, d=4, w=10, preempt=1)
        ],
        aperiodic_tasks=[],
    )

    result = AcceptanceTester(schedule, reserve).run(tasks)

    accepted_ticks = [
        record["t"]
        for record in result["schedule_result"]
        if "s1" in record["accepted_sporadic"]
    ]
    assert accepted_ticks == [1, 3, 4]
    assert result["reserve"][1] == 0.0
    assert result["reserve"][3] == 0.0
    assert result["reserve"][4] == 0.0


def test_rejects_sporadic_when_not_enough_reserve() -> None:
    """A sporadic job is rejected when it cannot finish by its deadline."""
    schedule = _schedule()
    reserve = {1: 10.0, 2: 3.0, 3: 10.0, 4: 0.0, 5: 0.0, 6: 0.0}
    tasks = TaskSystem(
        periodic_tasks=[],
        sporadic_tasks=[
            SporadicTask(task_id="s1", r=1, e=3, d=4, w=10, preempt=1)
        ],
        aperiodic_tasks=[],
    )

    result = AcceptanceTester(schedule, reserve).run(tasks)

    assert result["schedule_result"][0]["rejected_sporadic"] == ["s1"]
    assert result["reserve"] == reserve


def test_non_preemptive_sporadic_requires_contiguous_slots() -> None:
    """A non-preemptive sporadic job needs one continuous reserve window."""
    schedule = _schedule()
    reserve = {1: 10.0, 2: 0.0, 3: 10.0, 4: 10.0, 5: 10.0, 6: 0.0}
    tasks = TaskSystem(
        periodic_tasks=[],
        sporadic_tasks=[
            SporadicTask(task_id="s1", r=1, e=3, d=5, w=10, preempt=0)
        ],
        aperiodic_tasks=[],
    )

    result = AcceptanceTester(schedule, reserve).run(tasks)

    accepted_ticks = [
        record["t"]
        for record in result["schedule_result"]
        if "s1" in record["accepted_sporadic"]
    ]
    assert accepted_ticks == [3, 4, 5]


def test_schedules_aperiodic_when_reserve_available() -> None:
    """An aperiodic job is scheduled when reserve can satisfy it."""
    schedule = _schedule()
    reserve = {1: 5.0, 2: 5.0, 3: 5.0, 4: 0.0, 5: 0.0, 6: 0.0}
    tasks = TaskSystem(
        periodic_tasks=[],
        sporadic_tasks=[],
        aperiodic_tasks=[
            AperiodicTask(task_id="a1", r=1, e=2, d=3, w=5, preempt=1)
        ],
    )

    result = AcceptanceTester(schedule, reserve).run(tasks)

    scheduled_ticks = [
        record["t"]
        for record in result["schedule_result"]
        if "a1" in record["scheduled_aperiodic"]
    ]
    assert scheduled_ticks == [1, 2]
    assert result["reserve"][1] == 0.0
    assert result["reserve"][2] == 0.0


def test_marks_aperiodic_missed_when_insufficient_reserve() -> None:
    """An aperiodic job is marked missed when it cannot be completed."""
    schedule = _schedule()
    reserve = {1: 5.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0, 6: 0.0}
    tasks = TaskSystem(
        periodic_tasks=[],
        sporadic_tasks=[],
        aperiodic_tasks=[
            AperiodicTask(task_id="a1", r=1, e=2, d=3, w=5, preempt=1)
        ],
    )

    result = AcceptanceTester(schedule, reserve).run(tasks)

    assert result["schedule_result"][0]["missed_aperiodic"] == ["a1"]
    assert result["reserve"] == reserve
