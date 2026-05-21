from pydantic import BaseModel


class Storage(BaseModel):
    """Represents an energy storage system (battery) and its constraints."""
    storage_id: str
    soc_min: int
    soc_max: int
    discharge_max: int
    charge_max: int
    soc_init: int
