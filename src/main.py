import os

from src.evaluator import Evaluator
from src.generator import TaskSetGenerator
from src.rt_scheduler import RTScheduler
from src.utils import JsonIO
from src.config import config

_TASK_SET_PATH = config.task_set_path
_SCHEDULE_PATH = config.schedule_result_path


def generate_task_set() -> str:
    """Phase 1: generates a periodic task set and saves it to task_set.json.

    The output JSON includes empty ``sporadic`` and ``aperiodic`` sections so
    that users can add real-time jobs before running the scheduler.  The
    acceptance test (Phase 3) will only exercise those jobs if entries are
    present.

    Returns:
        Path to the saved task set file.
    """
    generator = TaskSetGenerator(horizon=config.horizon)
    tasks_dict, frame_size = generator.generate()
    output_data = {
        "frame_size": frame_size,
        "periodic": tasks_dict,
        "sporadic": {},
        "aperiodic": {},
    }

    JsonIO.save(output_data, _TASK_SET_PATH)
    print(f"Generated {len(tasks_dict)} periodic tasks with frame size {frame_size}")
    print(f"Saved to {_TASK_SET_PATH}")
    return _TASK_SET_PATH


def run_scheduler(task_set_path: str = _TASK_SET_PATH) -> dict:
    """Phase 2 + 3: runs the MILP day-ahead scheduler then the acceptance test.

    ``RTScheduler.run()`` automatically calls ``AcceptanceTester`` at the end
    of the solve, consuming the post-MILP reserve to accept/reject any sporadic
    or aperiodic jobs found in ``task_set_path``.  The returned schedule already
    contains ``accepted_sporadic``, ``scheduled_aperiodic``, ``rejected_sporadic``,
    and ``missed_aperiodic`` annotations per time tick.

    To exercise Phase 3, add ``sporadic`` / ``aperiodic`` entries to
    ``task_set.json`` before calling this function.

    Args:
        task_set_path: Path to the task set JSON file.

    Returns:
        Scheduler output dict with keys ``schedule_result`` and ``reserve``.
    """
    scheduler = RTScheduler(
        processor_settings_path=config.processor_settings_path,
        task_set_path=task_set_path,
        price_path=config.price_path,
        horizon=config.horizon,
        epsilon=config.epsilon,
    )
    result = scheduler.run()

    JsonIO.save({"schedule_result": result["schedule_result"]}, _SCHEDULE_PATH)
    print(f"Schedule saved to {_SCHEDULE_PATH}")
    return result


def run_evaluator(
    schedule_result_path: str = config.schedule_result_path,
    task_set_path: str = _TASK_SET_PATH,
) -> dict:
    """Phase 4: evaluates schedule quality and writes evaluation_results.json.

    Reads the schedule produced by Phase 2/3 (including acceptance-test
    annotations) and computes all performance metrics defined in the assignment.

    Args:
        schedule_result_path: Path to the schedule result JSON file.
        task_set_path: Path to the task set JSON file.

    Returns:
        Dictionary of computed evaluation metrics.
    """
    evaluator = Evaluator(
        processor_settings_path=config.processor_settings_path,
        task_set_path=task_set_path,
        price_path=config.price_path,
        schedule_result_path=schedule_result_path,
        horizon=config.horizon,
    )
    metrics = evaluator.evaluate()

    JsonIO.save(metrics, config.evaluation_results_path)
    print(f"Evaluation results saved to {config.evaluation_results_path}")
    return metrics


def main() -> None:
    """Runs the full pipeline: Phase 1 → 2+3 → 4.

    Phase 3 (acceptance test) is integrated inside ``run_scheduler()`` via
    ``AcceptanceTester``.  To exercise it, add ``sporadic`` / ``aperiodic``
    tasks to ``output/task_set.json`` after Phase 1 and before Phase 2.
    """
    task_set_path = generate_task_set()
    run_scheduler(task_set_path)
    run_evaluator()


if __name__ == "__main__":
    main()
