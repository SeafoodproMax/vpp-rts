from typing import Any, Dict, List

from src.model.asset.charging_job import ChargingJob
from src.model.asset.generator import Generator
from src.model.asset.renewable import HourlyForecast, RenewableCapacity, RenewableForecast
from src.model.asset.storage import Storage
from src.model.base.base_model import AppBaseModel


class ProcessorSettingsSystem(AppBaseModel):
    """Aggregate root for all physical assets in the microgrid."""
    generators: List[Generator]
    renewable_capacities: List[RenewableCapacity]
    renewable_forecasts: List[RenewableForecast]
    storages: List[Storage]
    charging_jobs: List[ChargingJob]

    @classmethod
    def _parse(cls, data: Dict[str, Any]) -> "ProcessorSettingsSystem":
        """Parses raw dictionary data into ProcessorSettingsSystem structure."""
        generators = [Generator(**item) for item in data.get("generator", [])]
        storages = [Storage(**item) for item in data.get("storage", [])]
        renewable_capacities = [
            RenewableCapacity(**item) for item in data.get("renewable_capacity", [])
        ]
        
        renewable_forecasts = []
        for forecast_dict in data.get("renewable_forecast", []):
            for r_id, hourly_data in forecast_dict.items():
                forecasts = [HourlyForecast(**f) for f in hourly_data]
                renewable_forecasts.append(
                    RenewableForecast(renewable_id=r_id, forecasts=forecasts)
                )
                
        charging_jobs = [ChargingJob(**item) for item in data.get("charging_jobs", [])]
        
        return cls(
            generators=generators,
            renewable_capacities=renewable_capacities,
            renewable_forecasts=renewable_forecasts,
            storages=storages,
            charging_jobs=charging_jobs
        )
