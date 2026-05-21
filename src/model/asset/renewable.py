from dataclasses import dataclass
from typing import List


@dataclass
class RenewableCapacity:
    """Defines the maximum capacity of a renewable energy source."""
    renewable_id: str
    capacity: int


@dataclass
class HourlyForecast:
    """A single hour forecast for renewable energy generation (percentage)."""
    hour: int
    pv_forecast: float


@dataclass
class RenewableForecast:
    """Aggregate forecast for a specific renewable energy source."""
    renewable_id: str
    forecasts: List[HourlyForecast]
