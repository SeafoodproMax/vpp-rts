"""Integration tests for the RTScheduler orchestrator."""

import os
from src.rt_scheduler.rt_scheduler import RTScheduler


def test_rt_scheduler_integration_run() -> None:
    """Tests the end-to-end scheduling pipeline on the actual input files."""
    current_dir = os.path.dirname(__file__)
    project_root = os.path.abspath(os.path.join(current_dir, "../.."))

    processor_settings_path = os.path.join(
        project_root, "input", "processor_settings.json"
    )
    task_set_path = os.path.join(project_root, "output", "task_set.json")
    price_path = os.path.join(project_root, "input", "price_72hr.json")

    # If task_set.json doesn't exist yet, we can skip or run with default assets.
    # But since output/task_set.json exists and we viewed it earlier, we can safely run it!
    if os.path.exists(task_set_path):
        scheduler = RTScheduler(
            processor_settings_path=processor_settings_path,
            task_set_path=task_set_path,
            price_path=price_path,
            horizon=72,
            epsilon=1e-6,
        )

        results = scheduler.run()

        assert results is not None
        assert "schedule_result" in results
        assert "reserve" in results

        # Horizon is 72 hours
        assert len(results["schedule_result"]) == 72
        assert len(results["reserve"]) == 72

        # Check fields of the first time tick
        tick_1 = results["schedule_result"][0]
        assert tick_1["t"] == 1
        assert "P" in tick_1
        assert "k" in tick_1
        assert "sell" in tick_1
        assert "soc" in tick_1
