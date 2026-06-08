from pydantic import BaseModel


class Storage(BaseModel):
    """Represents an energy storage system (battery) and its constraints."""
    storage_id: str      # 儲能裝置識別碼，例如 "b1"
    soc_min: int         # SOC 下限（MWh）：電池不能放電低於此值（保護壽命）
    soc_max: int         # SOC 上限（MWh）：電池不能充電超過此值（防止過充）
    discharge_max: int   # 每 tick 最大放電量（MWh）：放電功率物理上限
    charge_max: int      # 每 tick 最大充電量（MWh）：充電功率物理上限
    soc_init: int        # 排程開始前的初始電量（MWh），用於 t=1 的 SOC 平衡
