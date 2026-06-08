from typing import Any, Dict, List

from src.model.base.base_model import AppBaseModel
from src.model.task.rt_task import AperiodicTask, PeriodicTask, SporadicTask

class TaskSystem(AppBaseModel):
    """Aggregate root for all real-time scheduling tasks.

    對應 output/task_set.json，三種任務分開存放：
      periodic  → PeriodicTask（週期性，硬 deadline）
      sporadic  → SporadicTask（非週期，硬 deadline，需 acceptance test）
      aperiodic → AperiodicTask（非週期，軟 deadline）
    """
    periodic_tasks: List[PeriodicTask]
    sporadic_tasks: List[SporadicTask]
    aperiodic_tasks: List[AperiodicTask]

    @classmethod
    def _parse(cls, data: Dict[str, Any]) -> "TaskSystem":
        """Parses raw dictionary data into TaskSystem structure.

        task_set.json 的格式是 {"periodic": {"p1": {...}, "p2": {...}}, ...}
        這裡把 dict key（"p1"）當作 task_id 傳入，展開成各自的 Task 物件。
        """
        # periodic：{"p1": {r, p, e, d, w, preempt}, ...}
        periodic_tasks = []
        for t_id, t_data in data.get("periodic", {}).items():
            periodic_tasks.append(PeriodicTask(task_id=t_id, **t_data))

        # sporadic：{"s1": {r, e, d, w, preempt}, ...}（無 period 欄位）
        sporadic_tasks = []
        for t_id, t_data in data.get("sporadic", {}).items():
            sporadic_tasks.append(SporadicTask(task_id=t_id, **t_data))

        # aperiodic：{"a1": {r, e, d, w, preempt}, ...}
        aperiodic_tasks = []
        for t_id, t_data in data.get("aperiodic", {}).items():
            aperiodic_tasks.append(AperiodicTask(task_id=t_id, **t_data))

        return cls(
            periodic_tasks=periodic_tasks,
            sporadic_tasks=sporadic_tasks,
            aperiodic_tasks=aperiodic_tasks
        )
