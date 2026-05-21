from dataclasses import dataclass


@dataclass
class ChargingJob:
    """Represents a job assigned to charge a specific target storage."""
    job_id: str
    target_storage: str
