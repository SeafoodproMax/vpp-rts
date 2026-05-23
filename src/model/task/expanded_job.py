"""ExpandedJob model representing concrete instances of tasks and charging jobs."""

from typing import Optional
from pydantic import BaseModel


class ExpandedJob(BaseModel):
    """A concrete job instance expanded from a periodic task or charging job.

    Attributes:
        job_id: Unique identifier for the individual job instance.
        source_task_id: Identifier of the source periodic task or charging job.
        release: The absolute release time tick on the scheduling horizon.
        deadline: The absolute deadline time tick on the scheduling horizon.
        execution: The active execution duration required (in ticks).
        demand: The power/resource demand when active.
        preemptive: Whether the job execution can be preempted.
        is_charging: Whether the job is a storage charging job.
        target_storage: The ID of the target storage if this is a charging job.
    """

    job_id: str
    source_task_id: str
    release: int
    deadline: int
    execution: int
    demand: int
    preemptive: bool
    is_charging: bool = False
    target_storage: Optional[str] = None
