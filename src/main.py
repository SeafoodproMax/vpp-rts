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

    Returns:
        Path to the saved task set file.
    """
    generator = TaskSetGenerator(horizon=config.horizon)
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


def run_acceptance_test(
    schedule_result_path: str = config.schedule_result_path,
    task_set_path: str = _TASK_SET_PATH,
) -> str:
    """Phase 3: sporadic / aperiodic job acceptance test — NOT YET IMPLEMENTED.

    This function should be implemented before run_evaluator() is called so that
    the Phase 4 evaluation metrics reflect real sporadic/aperiodic scheduling
    decisions.

    Expected behaviour once implemented:
        1. Read the day-ahead schedule from schedule_result_path.
        2. For each sporadic job (hard deadline) arriving at runtime:
               - Check available slack, energy headroom, SOC, and generator
                 constraints in the current schedule window.
               - Accept the job if it can be inserted without violating any
                 existing periodic job, already-accepted hard-deadline job, or
                 system constraint (Constraints 1–23).
               - Reject and record in ``rejected_sporadic`` otherwise.
        3. For each aperiodic job (soft deadline):
               - Enqueue it; schedule it opportunistically whenever slack exists.
               - If not completed by its soft deadline, record in
                 ``missed_aperiodic`` and track tardiness.
        4. Write the updated schedule (with populated ``rejected_sporadic`` and
           ``missed_aperiodic`` per time-step) back to schedule_result_path so
           that run_evaluator() can compute accurate metrics.

    Args:
        schedule_result_path: Path to the schedule result JSON to read and update.
        task_set_path: Path to task_set.json containing sporadic/aperiodic tasks.

    Returns:
        Path to the updated schedule_result.json.

    Raises:
        NotImplementedError: Always, until this phase is implemented.
    """
    raise NotImplementedError(
        "Phase 3 (sporadic acceptance test) is not yet implemented. "
        "Implement this function and call it before run_evaluator()."
    )


def run_evaluator(
    schedule_result_path: str = config.schedule_result_path,
    task_set_path: str = _TASK_SET_PATH,
) -> dict:
    """Phase 4: evaluates schedule quality and writes evaluation_results.json.

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
    """Runs the full pipeline: Phase 1 → 2 → 3 → 4.

    Phase 3 (sporadic acceptance test) is intentionally skipped until implemented.
    Once run_acceptance_test() is ready, insert it between run_scheduler() and
    run_evaluator() so Phase 4 evaluation reflects real sporadic/aperiodic outcomes:

        run_scheduler(task_set_path)
        run_acceptance_test()   # ← uncomment when Phase 3 is implemented
        run_evaluator()
    """
    task_set_path = generate_task_set()
    run_scheduler(task_set_path)
    # TODO Phase 3: run_acceptance_test() — implement before enabling this line
    run_evaluator()


if __name__ == "__main__":
    main()
