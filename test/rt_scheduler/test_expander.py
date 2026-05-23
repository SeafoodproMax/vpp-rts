"""Tests for the JobExpander class."""

from src.model import (
    ChargingJob,
    PeriodicTask,
    ProcessorSettingsSystem,
    TaskSystem,
)
from src.rt_scheduler.expander import JobExpander


def test_periodic_task_expansion() -> None:
    """Tests that periodic tasks are correctly expanded to concrete jobs."""
    expander = JobExpander(horizon=72)
    tasks = TaskSystem(
        periodic_tasks=[
            PeriodicTask(
                task_id="p1", r=8, p=8, e=4, d=4, w=10, preempt=0
            ),
            PeriodicTask(
                task_id="p2", r=10, p=20, e=2, d=15, w=5, preempt=1
            ),
        ],
        sporadic_tasks=[],
        aperiodic_tasks=[],
    )

    expanded_jobs = expander.expand_periodic_tasks(tasks)

    # p1 releases at 8, 16, 24, 32, 40, 48, 56, 64 (8 instances).
    # Next release is 72, absolute deadline is 72 + 4 - 1 = 75 > 72 (filtered out).
    # So 8 instances for p1.
    # p2 releases at 10 (deadline 24), 30 (deadline 44), 50 (deadline 64).
    # Next release is 70, deadline 70 + 15 - 1 = 84 > 72 (filtered out).
    # So 3 instances for p2.
    # Total jobs: 8 + 3 = 11.
    assert len(expanded_jobs) == 11

    # Verify first instance of p1
    p1_jobs = [j for j in expanded_jobs if j.source_task_id == "p1"]
    assert len(p1_jobs) == 8
    assert p1_jobs[0].job_id == "p1_0"
    assert p1_jobs[0].release == 8
    assert p1_jobs[0].deadline == 11
    assert p1_jobs[0].execution == 4
    assert p1_jobs[0].demand == 10
    assert p1_jobs[0].preemptive is False
    assert p1_jobs[0].is_charging is False

    # Verify first instance of p2
    p2_jobs = [j for j in expanded_jobs if j.source_task_id == "p2"]
    assert len(p2_jobs) == 3
    assert p2_jobs[0].job_id == "p2_0"
    assert p2_jobs[0].release == 10
    assert p2_jobs[0].deadline == 24
    assert p2_jobs[0].execution == 2
    assert p2_jobs[0].demand == 5
    assert p2_jobs[0].preemptive is True
    assert p2_jobs[0].is_charging is False


def test_charging_job_expansion() -> None:
    """Tests that storage charging configs are expanded to 72-hour jobs."""
    expander = JobExpander(horizon=72)
    assets = ProcessorSettingsSystem(
        generators=[],
        renewable_capacities=[],
        renewable_forecasts=[],
        storages=[],
        charging_jobs=[
            ChargingJob(job_id="battery_1_chg", target_storage="battery_1")
        ],
    )

    charging_jobs = expander.expand_charging_jobs(assets)

    assert len(charging_jobs) == 1

    job = charging_jobs[0]
    assert job.job_id == "battery_1_chg"
    assert job.source_task_id == "battery_1_chg"
    assert job.release == 1
    assert job.deadline == 72
    assert job.execution == 72
    assert job.demand == 0
    assert job.preemptive is True
    assert job.is_charging is True
    assert job.target_storage == "battery_1"
