from dataclasses import dataclass
from typing import Any, Dict, List

from src.model.base.base_model import AppBaseModel
from src.model.task.rt_task import AperiodicTask, PeriodicTask, SporadicTask


@dataclass
class TaskSystem(AppBaseModel):
    """Aggregate root for all real-time scheduling tasks."""
    periodic_tasks: List[PeriodicTask]
    sporadic_tasks: List[SporadicTask]
    aperiodic_tasks: List[AperiodicTask]

    @classmethod
    def _parse(cls, data: Dict[str, Any]) -> "TaskSystem":
        """Parses raw dictionary data into TaskSystem structure."""
        periodic_tasks = []
        for t_id, t_data in data.get("periodic", {}).items():
            periodic_tasks.append(PeriodicTask(task_id=t_id, **t_data))
            
        sporadic_tasks = []
        for t_id, t_data in data.get("sporadic", {}).items():
            sporadic_tasks.append(SporadicTask(task_id=t_id, **t_data))
            
        aperiodic_tasks = []
        for t_id, t_data in data.get("aperiodic", {}).items():
            aperiodic_tasks.append(AperiodicTask(task_id=t_id, **t_data))
            
        return cls(
            periodic_tasks=periodic_tasks,
            sporadic_tasks=sporadic_tasks,
            aperiodic_tasks=aperiodic_tasks
        )
