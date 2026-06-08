from typing import Any, Dict, List

from src.model.asset.charging_job import ChargingJob
from src.model.asset.generator import Generator
from src.model.asset.renewable import HourlyForecast, RenewableCapacity, RenewableForecast
from src.model.asset.storage import Storage
from src.model.base.base_model import AppBaseModel

class ProcessorSettingsSystem(AppBaseModel):
    """Aggregate root for all physical assets in the microgrid.

    對應 input/processor_settings.json，彙整微電網的所有實體設備：
      generator       → Generator（熱機發電機）
      storage         → Storage（電池儲能）
      renewable_capacity  → RenewableCapacity（再生能源裝置容量）
      renewable_forecast  → RenewableForecast（再生能源逐時預測）
      charging_jobs   → ChargingJob（儲能充電 job 定義）
    """
    generators: List[Generator]                    # 所有發電機
    renewable_capacities: List[RenewableCapacity]  # 再生能源裝置容量列表
    renewable_forecasts: List[RenewableForecast]   # 再生能源逐時預測列表
    storages: List[Storage]                        # 所有儲能裝置
    charging_jobs: List[ChargingJob]               # 充電 job 定義（每台儲能對應一個）

    @classmethod
    def _parse(cls, data: Dict[str, Any]) -> "ProcessorSettingsSystem":
        """Parses raw dictionary data into ProcessorSettingsSystem structure.

        processor_settings.json 的格式：
          "generator": [{...}, {...}]  → 直接 list of dict
          "renewable_forecast": [{"pv1": [{hour, pv_forecast}, ...]}, ...]
          → 外層是 list，每個元素是 {renewable_id: [hourly forecasts]}
        """
        generators = [Generator(**item) for item in data.get("generator", [])]
        storages = [Storage(**item) for item in data.get("storage", [])]
        renewable_capacities = [
            RenewableCapacity(**item) for item in data.get("renewable_capacity", [])
        ]

        # renewable_forecast 格式較特殊：[{"pv1": [{hour:1, pv_forecast:0.5}, ...]}]
        # 把 {"pv1": [...]} 解包成 RenewableForecast(renewable_id="pv1", forecasts=[...])
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
