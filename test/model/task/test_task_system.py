import os

from src.model.task.task_system import TaskSystem


def test_task_system_load():
    """Test loading TaskSystem from the actual input JSON."""
    current_dir = os.path.dirname(__file__)
    project_root = os.path.abspath(os.path.join(current_dir, "../../.."))
    json_path = os.path.join(project_root, "references", "aperiodic_n_sporadic_template.json")
    
    task_system = TaskSystem.load_from_json(json_path)
    
    assert task_system is not None
    assert len(task_system.periodic_tasks) == 0  # No periodic tasks in this template
    
    assert len(task_system.aperiodic_tasks) == 3
    assert task_system.aperiodic_tasks[0].task_id == "a1"
    assert task_system.aperiodic_tasks[0].r == 1
    
    assert len(task_system.sporadic_tasks) == 2
    assert task_system.sporadic_tasks[0].task_id == "s1"
    assert task_system.sporadic_tasks[0].w == 15
