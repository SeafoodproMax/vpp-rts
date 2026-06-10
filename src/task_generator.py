"""Phase 1 entry point: periodic task set generation.

Thin entry module exposing the Phase 1 implementation that lives in
``src/generator/``. Run with::

    python -m src.task_generator
"""

from src.generator import FrameSizeCalculator, TaskSetGenerator, TaskSetValidator

__all__ = ["FrameSizeCalculator", "TaskSetGenerator", "TaskSetValidator"]


def main() -> None:
    """Generates a task set and saves it to ``output/task_set.json``."""
    # 延遲 import 避免在僅取用類別時載入整條 pipeline
    from src.main import generate_task_set

    generate_task_set()


if __name__ == "__main__":
    main()
