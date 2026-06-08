from pydantic import BaseModel


class ChargingJob(BaseModel):
    """Represents a job assigned to charge a specific target storage.

    充電 job 是一種特殊 job：它把電從發電機/再生能源「路由」到儲能裝置。
    與普通 job 不同：
    - demand = 0（不消耗用電負載，只是充電）
    - 只能從 gen/renewable 取電（不能從儲能放電）
    - 沒有 x 二元變數（不受 C3/C5 約束，由 k 直接控制充電量）
    """
    job_id: str          # 充電 job 識別碼，例如 "chg_b1"
    target_storage: str  # 目標儲能裝置識別碼，例如 "b1"
