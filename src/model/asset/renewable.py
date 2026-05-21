from typing import List

from pydantic import BaseModel


class RenewableCapacity(BaseModel):
    """Defines the maximum capacity of a renewable energy source."""
    renewable_id: str
    capacity: int


class HourlyForecast(BaseModel):
    """A single hour forecast for renewable energy generation (percentage)."""
    hour: int
    pv_forecast: float


class RenewableForecast(BaseModel):
    """Aggregate forecast for a specific renewable energy source."""
    renewable_id: str
    forecasts: List[HourlyForecast]
