import json
import os

from src.model.task.task_system import TaskSystem


def test_task_system_load():
    """Test loading TaskSystem from the actual input JSON."""
    current_dir = os.path.dirname(__file__)
    project_root = os.path.abspath(os.path.join(current_dir, "../../.."))
    json_path = os.path.join(project_root, "input", "aperiodic_n_sporadic.json")

    with open(json_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    task_system = TaskSystem.load_from_json(json_path)

    assert task_system is not None
    assert len(task_system.periodic_tasks) == len(raw.get("periodic", {}))
    assert len(task_system.sporadic_tasks) == len(raw.get("sporadic", {}))
    assert len(task_system.aperiodic_tasks) == len(raw.get("aperiodic", {}))

    # 逐欄位比對第一個 sporadic / aperiodic task 與原始 JSON 一致
    if task_system.sporadic_tasks:
        first_id = task_system.sporadic_tasks[0].task_id
        assert task_system.sporadic_tasks[0].w == raw["sporadic"][first_id]["w"]
    if task_system.aperiodic_tasks:
        first_id = task_system.aperiodic_tasks[0].task_id
        assert task_system.aperiodic_tasks[0].r == raw["aperiodic"][first_id]["r"]
