from pydantic import BaseModel


class ChargingJob(BaseModel):
    """Represents a job assigned to charge a specific target storage."""
    job_id: str
    target_storage: str
