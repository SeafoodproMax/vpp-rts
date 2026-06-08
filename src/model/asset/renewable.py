from typing import List

from pydantic import BaseModel


class RenewableCapacity(BaseModel):
    """Defines the maximum capacity of a renewable energy source."""
    renewable_id: str  # 再生能源識別碼，例如 "pv1"
    capacity: int      # 裝置容量（MWh）：太陽能板或風機的最大輸出上限


class HourlyForecast(BaseModel):
    """A single hour forecast for renewable energy generation (percentage)."""
    hour: int           # 時刻（tick）：1 ~ 72
    pv_forecast: float  # 發電比例預測（0.0 ~ 1.0）：例如 0.8 表示當下可發 80% 容量


class RenewableForecast(BaseModel):
    """Aggregate forecast for a specific renewable energy source.

    彙整某台再生能源設備整個排程期間（72 小時）逐時的發電比例預測。
    C13 約束使用此資料限制 P[i][t] ≤ capacity × pv_forecast[t]。
    """
    renewable_id: str          # 對應的再生能源識別碼
    forecasts: List[HourlyForecast]  # 每個 tick 的預測列表
