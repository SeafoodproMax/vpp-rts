from dataclasses import dataclass


@dataclass
class BaseRTTask:
    """Base class for all real-time tasks with common attributes."""
    task_id: str
    r: int
    e: int
    d: int
    w: int
    preempt: int


@dataclass
class PeriodicTask(BaseRTTask):
    """Represents a periodic hard deadline task."""
    p: int


@dataclass
class SporadicTask(BaseRTTask):
    """Represents a sporadic hard deadline task that requires acceptance test."""
    pass


@dataclass
class AperiodicTask(BaseRTTask):
    """Represents an aperiodic soft deadline task."""
    pass
