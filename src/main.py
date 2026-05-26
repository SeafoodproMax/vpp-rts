import os

from src.advanced_scheduler import AdvancedScheduler
from src.evaluator import Evaluator
from src.generator import TaskSetGenerator
from src.rt_scheduler import RTScheduler
from src.rt_scheduler.relaxation import RelaxationConfig
from src.utils import JsonIO
from src.config import config

_TASK_SET_PATH = config.task_set_path
_SCHEDULE_PATH = config.schedule_result_path


def _load_demo_jobs() -> dict:
    """Loads the demo-provided sporadic / aperiodic jobs, if the file exists.

    These jobs are supplied at demo time (see Appendix I) and drive Phase 3's
    acceptance test (rubric items 4-3 sporadic value and 2-2 aperiodic miss).

    Returns:
        A dict with ``sporadic`` and ``aperiodic`` sections (empty when the
        demo file is absent).
    """
    if not os.path.exists(config.demo_jobs_path):
        return {"sporadic": {}, "aperiodic": {}}
    demo = JsonIO.load(config.demo_jobs_path)
    return {
        "sporadic": demo.get("sporadic", {}),
        "aperiodic": demo.get("aperiodic", {}),
    }


def generate_task_set() -> str:
    """Phase 1: generates a periodic task set and saves it to task_set.json.

    The generated periodic tasks are merged with the demo-provided sporadic and
    aperiodic jobs (``config.demo_jobs_path``) so the acceptance test (Phase 3)
    has real-time jobs to accept/reject and schedule. When no demo file exists,
    the sporadic / aperiodic sections are left empty.

    Returns:
        Path to the saved task set file.
    """
    generator = TaskSetGenerator(horizon=config.horizon, seed=config.task_seed)
    tasks_dict, frame_size = generator.generate()
    demo_jobs = _load_demo_jobs()
    output_data = {
        "frame_size": frame_size,
        "periodic": tasks_dict,
        "sporadic": demo_jobs["sporadic"],
        "aperiodic": demo_jobs["aperiodic"],
    }

    JsonIO.save(output_data, _TASK_SET_PATH)
    print(f"Generated {len(tasks_dict)} periodic tasks with frame size {frame_size}")
    print(
        f"Merged {len(demo_jobs['sporadic'])} sporadic and "
        f"{len(demo_jobs['aperiodic'])} aperiodic demo jobs"
    )
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

    JsonIO.save(result["log"], config.acceptance_test_log_path)
    print(f"Acceptance test log saved to {config.acceptance_test_log_path}")
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


def _load_runtime_config() -> dict:
    """Loads runtime_config.json (Level 2), returning ``{}`` when it is absent."""
    if not os.path.exists(config.runtime_config_path):
        return {}
    return JsonIO.load(config.runtime_config_path)


def run_advanced_scheduler(task_set_path: str = _TASK_SET_PATH) -> dict:
    """Level 2: runs the rolling-horizon dynamic scheduler and evaluates it.

    Reads relaxed-assumption and dynamic-cadence parameters from
    ``runtime_config.json``, runs ``AdvancedScheduler`` over the same task set as the
    static run, and writes the dynamic schedule, acceptance log, per-round run log,
    and evaluation results to their ``*_dynamic`` output files.

    Args:
        task_set_path: Path to the task set JSON file (shared with the static run).

    Returns:
        Dict with the dynamic ``result`` and computed ``metrics``.
    """
    rt = _load_runtime_config()
    relaxation = RelaxationConfig(**rt.get("relaxation", {}))
    dynamic = rt.get("dynamic", {})

    scheduler = AdvancedScheduler(
        processor_settings_path=config.processor_settings_path,
        task_set_path=task_set_path,
        price_path=config.price_path,
        horizon=config.horizon,
        epsilon=config.epsilon,
        relaxation=relaxation,
        **dynamic,
    )
    result = scheduler.run()

    JsonIO.save({"schedule_result": result["schedule_result"]}, config.schedule_result_dynamic_path)
    print(f"Dynamic schedule saved to {config.schedule_result_dynamic_path}")

    JsonIO.save(result["log"], config.acceptance_test_log_dynamic_path)
    print(f"Dynamic acceptance test log saved to {config.acceptance_test_log_dynamic_path}")

    JsonIO.save(
        {
            "run_log": result["run_log"],
            "realized_renewable": result["realized_renewable"],
            "precedence": result["precedence"],
        },
        config.dynamic_run_log_path,
    )
    print(f"Dynamic run log saved to {config.dynamic_run_log_path}")

    evaluator = Evaluator(
        processor_settings_path=config.processor_settings_path,
        task_set_path=task_set_path,
        price_path=config.price_path,
        schedule_result_path=config.schedule_result_dynamic_path,
        horizon=config.horizon,
    )
    metrics = evaluator.evaluate()
    JsonIO.save(metrics, config.evaluation_results_dynamic_path)
    print(f"Dynamic evaluation saved to {config.evaluation_results_dynamic_path}")
    return {"result": result, "metrics": metrics}


def _print_comparison(static: dict, dynamic: dict) -> None:
    """Prints a static-vs-dynamic comparison across the headline metrics."""
    keys = [
        "objective_value",
        "generator_cost",
        "market_revenue",
        "hard_deadline_miss_rate",
        "soft_deadline_miss_rate",
        "average_response_time",
    ]
    print("\n=== Level 1 (static) vs Level 2 (dynamic) ===")
    print(f"{'metric':28} {'static':>14} {'dynamic':>14}")
    for k in keys:
        print(f"{k:28} {static.get(k, 0):>14} {dynamic.get(k, 0):>14}")
    sv_s = static.get("acceptance_test", {}).get("sporadic_value_rate", 0)
    sv_d = dynamic.get("acceptance_test", {}).get("sporadic_value_rate", 0)
    print(f"{'sporadic_value_rate':28} {sv_s:>14} {sv_d:>14}")


def run_level2() -> None:
    """Runs the full Level 2 comparison pipeline on a single shared task set.

    Phase 1 (generate) → Phase 2/3/4 static (Level 1) → advanced dynamic (Level 2).
    Both schedulers run on the same ``task_set.json`` so the resulting metrics are
    directly comparable (rubric item 8-3).
    """
    task_set_path = generate_task_set()
    run_scheduler(task_set_path)
    static_metrics = run_evaluator()
    dynamic = run_advanced_scheduler(task_set_path)
    _print_comparison(static_metrics, dynamic["metrics"])


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
