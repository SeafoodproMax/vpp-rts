"""Level 2 relaxed-assumption parameters for the VPP MILP formulation.

Level 1 makes several simplifying assumptions (Section 1.3 of the brief). Level 2
relaxes three of them and models the relaxation as extra parameters and modified
constraints, *without introducing any new decision variable* -- every relaxed
constraint is expressed using the Level 1 decision variables (``P``, ``k``, ``SOC``,
``Sell``, ``x``).

The relaxations modelled here:

* **Assumption 12 -- realistic storage.** Round-trip (charge/discharge) efficiency,
  self-discharge, a charge/discharge cycle (throughput) limit, a state-of-charge
  dependent power limit, and a throughput aging cost.
* **Assumption 11 -- renewable uncertainty.** A conservative derating margin applied
  to the renewable forecast so the day-ahead plan keeps headroom against
  over-forecast.
* **Assumption 5 -- job precedence.** Ordering constraints forcing one regular job to
  finish before another may start.

A ``RelaxationConfig`` left at its defaults is a no-op: the formulation it produces
is mathematically identical to the Level 1 model, so the day-ahead static schedule
is unchanged.
"""

from pydantic import BaseModel, Field


class RelaxationConfig(BaseModel):
    """Tunable parameters for the Level 2 relaxed-assumption constraints.

    All fields default to values that disable the relaxation, so an unset config
    reproduces the Level 1 formulation exactly.

    Attributes:
        charge_efficiency: Fraction ``eta_c in (0, 1]`` of charge energy that
            reaches the cell (the rest is conversion loss). 1.0 disables.
        discharge_efficiency: Fraction ``eta_d in (0, 1]`` of cell energy that
            reaches the bus when discharging. 1.0 disables.
        self_discharge_rate: Per-tick self-discharge fraction ``sigma in [0, 1)``.
            0.0 disables.
        cycle_limit: Maximum full-equivalent cycles over the horizon. Each storage
            may discharge at most ``cycle_limit * (soc_max - soc_min)`` MWh in
            total. ``None`` disables.
        soc_power_floor: Floor fraction ``in (0, 1]`` of rated charge/discharge
            power available at the unfavourable SOC extreme; power scales linearly
            from this floor (empty for discharge / full for charge) up to rated
            power at the favourable extreme. 1.0 disables (rated power always).
        aging_cost: Battery aging cost in $/MWh of energy discharged, added to the
            objective. 0.0 disables.
        renewable_uncertainty_margin: Derating fraction ``beta in [0, 1)`` applied
            to the renewable forecast cap: the usable forecast becomes
            ``forecast * (1 - beta)``. 0.0 disables.
        precedence: Ordered ``(job_a, job_b)`` pairs requiring ``job_a`` to finish
            before ``job_b`` becomes active. Only pairs whose job IDs are both
            regular (non-charging) scheduled jobs take effect.
    """

    # ── 儲能現實模型參數（對應 Level 2 Assumption 12）─────────────────────────
    # 預設值全部是「no-op」，不影響 Level 1 的結果

    # 充電效率 η_c：充入 1 MWh 的電能，實際進入電池的比例（損耗 = 1 - η_c）
    # 預設 1.0 = 理想（無損耗）
    charge_efficiency: float = 1.0

    # 放電效率 η_d：從電池取出 1 MWh，實際送到電網的比例
    # 預設 1.0 = 理想（無損耗）
    discharge_efficiency: float = 1.0

    # 自耗損率 σ：每個 tick SOC 自然減少的比例（電池靜置也會慢慢放電）
    # 預設 0.0 = 無自耗損
    self_discharge_rate: float = 0.0

    # 循環壽命限制：整個 horizon 內最多放電 cycle_limit × (soc_max - soc_min) MWh
    # None = 不限制
    cycle_limit: float | None = None

    # SOC 依存功率下限：電量接近極限時，可用充放電功率會線性縮減
    # 1.0 = 不縮減（任何 SOC 都能全速充放電）
    soc_power_floor: float = 1.0

    # 老化成本：每放電 1 MWh 加入目標函數的懲罰（$/MWh）
    # 0.0 = 不計老化
    aging_cost: float = 0.0

    # ── 再生能源不確定性參數（對應 Level 2 Assumption 11）──────────────────────
    # 對日前預測值打折，預留 headroom 避免實際輸出不足
    # 0.0 = 不打折（完全信任預測）；0.1 = 保守 10%
    renewable_uncertainty_margin: float = 0.0

    # ── Job 優先順序（對應 Level 2 Assumption 5）────────────────────────────────
    # [(a, b), ...] 表示 job a 必須完整完成後，job b 才能開始執行
    # 空列表 = 無限制（Level 1 預設）
    precedence: list[tuple[str, str]] = Field(default_factory=list)

    @property
    def storage_relaxed(self) -> bool:
        """Whether any storage-realism relaxation is active."""
        # 任何一個儲能參數偏離預設值，就表示啟用了 Level 2 儲能放寬
        return (
            self.charge_efficiency < 1.0
            or self.discharge_efficiency < 1.0
            or self.self_discharge_rate > 0.0
            or self.cycle_limit is not None
            or self.soc_power_floor < 1.0
            or self.aging_cost > 0.0
        )
