import os

from src.generator import TaskSetGenerator
from src.scheduler import Scheduler
from src.utils import JsonIO

_TASK_SET_PATH = os.path.join("output", "task_set.json")
_SCHEDULE_PATH = os.path.join("output", "schedule_result.json")


def generate_task_set() -> str:
    """Phase 1: generates a periodic task set and saves it to task_set.json.

    Returns:
        Path to the saved task set file.
    """
    generator = TaskSetGenerator()
    tasks_dict, frame_size = generator.generate()
    output_data = {
        "frame_size": frame_size,
        "periodic": tasks_dict,
    }

    JsonIO.save(output_data, _TASK_SET_PATH)
    print(f"Generated {len(tasks_dict)} periodic tasks with frame size {frame_size}")
    print(f"Saved to {_TASK_SET_PATH}")
    return _TASK_SET_PATH


def run_scheduler(task_set_path: str = _TASK_SET_PATH) -> dict:
    """Phase 2: runs the MILP day-ahead scheduler.

    Args:
        task_set_path: Path to the task set JSON file.

    Returns:
        Scheduler output containing schedule_result and reserve.
    """
    scheduler = Scheduler(task_set_path=task_set_path)
    result = scheduler.run()

    JsonIO.save({"schedule_result": result["schedule_result"]}, _SCHEDULE_PATH)
    print(f"Schedule saved to {_SCHEDULE_PATH}")
    return result


def main() -> None:
    """Runs the full pipeline: task generation then MILP scheduling."""
    task_set_path = generate_task_set()
    run_scheduler(task_set_path)


if __name__ == "__main__":
    main()
