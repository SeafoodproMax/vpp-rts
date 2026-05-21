from pydantic import BaseModel


class BaseRTTask(BaseModel):
    """Base class for all real-time tasks with common attributes."""
    task_id: str
    r: int
    e: int
    d: int
    w: int
    preempt: int


class PeriodicTask(BaseRTTask):
    """Represents a periodic hard deadline task."""
    p: int


class SporadicTask(BaseRTTask):
    """Represents a sporadic hard deadline task that requires acceptance test."""
    pass


class AperiodicTask(BaseRTTask):
    """Represents an aperiodic soft deadline task."""
    pass
