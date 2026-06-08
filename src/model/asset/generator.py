from pydantic import BaseModel


class Generator(BaseModel):
    """Represents a thermal power generator and its operational constraints."""
    generator_id: str    # 發電機識別碼，例如 "g1"
    output_min: int      # 最小輸出功率（MWh/tick）：開機時輸出不能低於此值
    output_max: int      # 最大輸出功率（MWh/tick）：物理上限
    ramp_up_rate: int    # 每 tick 最大增加量（爬坡率上限）：防止輸出突升
    ramp_down_rate: int  # 每 tick 最大減少量（爬坡率下限）：防止輸出突降
    min_up_time: int     # 最短開機時間（ticks）：啟動後至少維持這麼久
    min_down_time: int   # 最短關機時間（ticks）：關機後至少冷卻這麼久
    cost_fixed: int      # 固定開機成本（$/tick）：只要開著就計費，與輸出無關
    cost_variable: int   # 可變發電成本（$/MWh）：每輸出 1 MWh 的邊際成本
    initial_on_time: int   # 排程開始前已連續開機幾個 tick（影響 C11 強制開機）
    initial_off_time: int  # 排程開始前已連續關機幾個 tick（影響 C12 強制關機）
    initial_energy: int    # 排程開始前的輸出功率（用於 t=1 的爬坡率計算）
