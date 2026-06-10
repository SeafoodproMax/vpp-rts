"""Phase 2 + 3 entry point: day-ahead MILP scheduling and acceptance test.

Thin entry module exposing the scheduler implementation that lives in
``src/rt_scheduler/``. Run with::

    python -m src.scheduler
"""

from src.rt_scheduler import AcceptanceTester, RTScheduler

__all__ = ["AcceptanceTester", "RTScheduler"]


def main() -> None:
    """Runs the MILP scheduler and saves ``output/schedule_result.json``."""
    # 延遲 import 避免在僅取用類別時載入整條 pipeline
    from src.main import run_scheduler

    run_scheduler()


if __name__ == "__main__":
    main()
