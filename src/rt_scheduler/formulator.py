"""PuLP MILP formulation for the Virtual Power Plant scheduling problem."""

from typing import Any

import pulp
from src.model import (
    ExpandedJob,
    Generator,
    ProcessorSettingsSystem,
    PriceSystem,
    Storage,
)
from src.rt_scheduler.relaxation import RelaxationConfig


class VppMilpFormulator:
    """Formulates the mixed-integer linear programming (MILP) model.

    This class handles variable creation, objective setup, and constraint declaration
    for the real-time Virtual Power Plant (VPP) system over a planning horizon.

    Level 2 relaxed-assumption modelling is opt-in via ``relaxation`` and the
    reserve (day-ahead reservation) strategy via ``reserve_floor``; both default to
    no-ops so the Level 1 day-ahead formulation is reproduced exactly. None of the
    relaxed constraints introduce a new decision variable -- they are expressed with
    the Level 1 variables ``P``, ``k``, ``SOC``, ``Sell`` and ``x``.
    """

    _DELTA_T: int = 1

    def __init__(
        self,
        assets: ProcessorSettingsSystem,
        prices: PriceSystem,
        all_jobs: list[ExpandedJob],
        horizon: int,
        *,
        relaxation: RelaxationConfig | None = None,
        reserve_floor: dict[int, float] | None = None,
    ) -> None:
        """Initializes the formulator with assets, prices, and jobs.

        Args:
            assets: The physical device settings (generators, storages, renewables).
            prices: The day-ahead hourly price list.
            all_jobs: The combined list of expanded regular and charging jobs.
            horizon: The scheduling horizon duration (in ticks).
            relaxation: Optional Level 2 relaxed-assumption parameters. ``None``
                (the default) reproduces the Level 1 formulation.
            reserve_floor: Optional per-tick lower bound on ``Sell[t]`` (MWh). The
                day-ahead reservation strategy keeps this much redirectable surplus
                available for sporadic/aperiodic acceptance. ``None`` disables it.
        """
        self._assets = assets
        self._prices = prices
        self._all_jobs = all_jobs
        self._horizon = horizon
        self._relax = relaxation or RelaxationConfig()
        self._reserve_floor = reserve_floor or {}

        self._regular_jobs = [j for j in all_jobs if not j.is_charging]
        self._charging_jobs = [j for j in all_jobs if j.is_charging]

        self._time_steps = list(range(1, horizon + 1))
        self._prob = pulp.LpProblem("VPP_DayAhead", pulp.LpMinimize)

        # Build maps for formulation lookup
        self._price_map = {p.hour: p.market_price for p in self._prices.price}
        self._forecast_map: dict[str, dict[int, float]] = {}
        for rf in self._assets.renewable_forecasts:
            self._forecast_map[rf.renewable_id] = {
                f.hour: f.pv_forecast for f in rf.forecasts
            }
        self._capacity_map = {
            rc.renewable_id: rc.capacity
            for rc in self._assets.renewable_capacities
        }
        self._gen_map = {
            g.generator_id: g for g in self._assets.generators
        }
        self._sto_map = {
            s.storage_id: s for s in self._assets.storages
        }

        self._gen_ids = [g.generator_id for g in self._assets.generators]
        self._ren_ids = [
            rc.renewable_id for rc in self._assets.renewable_capacities
        ]
        self._sto_ids = [s.storage_id for s in self._assets.storages]
        self._all_device_ids = self._gen_ids + self._ren_ids + self._sto_ids
        self._gen_ren_ids = self._gen_ids + self._ren_ids

        self._storage_charging_job = {
            cj.target_storage: cj.job_id for cj in self._charging_jobs
        }

        # Decision variables dict placeholders
        self._P: dict[str, dict[int, pulp.LpVariable]] = {}
        self._k: dict[str, dict[str, dict[int, pulp.LpVariable]]] = {}
        self._u: dict[str, dict[int, pulp.LpVariable]] = {}
        self._start: dict[str, dict[int, pulp.LpVariable]] = {}
        self._stop: dict[str, dict[int, pulp.LpVariable]] = {}
        self._charge_b: dict[str, dict[int, pulp.LpVariable]] = {}
        self._discharge_b: dict[str, dict[int, pulp.LpVariable]] = {}
        self._SOC: dict[str, dict[int, pulp.LpVariable]] = {}
        self._Sell: dict[int, pulp.LpVariable] = {}
        self._x: dict[str, dict[int, pulp.LpVariable]] = {}

    @property
    def prob(self) -> pulp.LpProblem:
        """Returns the formulated PuLP LpProblem."""
        return self._prob

    @property
    def P(self) -> dict[str, dict[int, pulp.LpVariable]]:
        """Returns the output power variables P[device_id][tick]."""
        return self._P

    @property
    def k(self) -> dict[str, dict[str, dict[int, pulp.LpVariable]]]:
        """Returns the routing variables k[job_id][device_id][tick]."""
        return self._k

    @property
    def SOC(self) -> dict[str, dict[int, pulp.LpVariable]]:
        """Returns the storage State of Charge variables SOC[storage_id][tick]."""
        return self._SOC

    @property
    def Sell(self) -> dict[int, pulp.LpVariable]:
        """Returns the market sales variables Sell[tick]."""
        return self._Sell

    @property
    def time_steps(self) -> list[int]:
        """Returns the horizon time steps list."""
        return self._time_steps

    @property
    def all_device_ids(self) -> list[str]:
        """Returns all device IDs (generators, renewables, storage)."""
        return self._all_device_ids

    @property
    def gen_ren_ids(self) -> list[str]:
        """Returns generator and renewable IDs."""
        return self._gen_ren_ids

    @property
    def sto_ids(self) -> list[str]:
        """Returns storage device IDs."""
        return self._sto_ids

    @property
    def all_jobs(self) -> list[ExpandedJob]:
        """Returns all expanded jobs list."""
        return self._all_jobs

    @property
    def regular_jobs(self) -> list[ExpandedJob]:
        """Returns only expanded regular jobs list."""
        return self._regular_jobs

    def formulate(self) -> None:
        """Formulates the variables, objective, and all constraints.

        Beyond the 23 Level 1 constraints this also adds, when the corresponding
        relaxation/reserve options are enabled, the Level 2 relaxed-assumption
        constraints (R-prefixed) and the reservation-strategy floor.
        """
        self._create_variables()
        self._add_objective()
        self._add_job_constraints()
        self._add_generator_constraints()
        self._add_renewable_constraints()
        self._add_storage_constraints()
        self._add_power_balance_constraints()
        self._add_precedence_constraints()
        self._add_reserve_floor_constraints()

    def _create_variables(self) -> None:
        """Declares all decision variables for the MILP solver."""
        T = self._time_steps

        # ── 連續變數 ────────────────────────────────────────────────────────────

        # P[i][t]：裝置 i 在 t 時刻的總輸出功率（MWh），≥ 0
        # 包含發電機、再生能源、儲能（放電為正）
        for i in self._all_device_ids:
            self._P[i] = {
                t: pulp.LpVariable(f"P_{i}_{t}", lowBound=0) for t in T
            }

        # k[job][device][t]：job j 在 t 時刻從裝置 i 取用的電量（MWh）
        # 只在 job 的 [release, deadline] 區間內定義
        # 充電 job 只能從 gen/renewable 取電（不含儲能放電）
        for job in self._all_jobs:
            self._k[job.job_id] = {}
            allowed_devices = (
                self._gen_ren_ids if job.is_charging else self._all_device_ids
            )
            for i in allowed_devices:
                self._k[job.job_id][i] = {}
                for t in range(job.release, job.deadline + 1):
                    self._k[job.job_id][i][t] = pulp.LpVariable(
                        f"k_{job.job_id}_{i}_{t}", lowBound=0
                    )

        # ── 整數變數（Binary）───────────────────────────────────────────────────

        # u[i][t]：發電機 i 在 t 時刻是否開機（1=開, 0=關）
        # start[i][t]：t 時刻是否啟動（0→1 的轉變）
        # stop[i][t]：t 時刻是否關機（1→0 的轉變）
        for i in self._gen_ids:
            self._u[i] = {
                t: pulp.LpVariable(f"u_{i}_{t}", cat="Binary") for t in T
            }
            self._start[i] = {
                t: pulp.LpVariable(f"start_{i}_{t}", cat="Binary") for t in T
            }
            self._stop[i] = {
                t: pulp.LpVariable(f"stop_{i}_{t}", cat="Binary") for t in T
            }

        # charge_b[i][t]：儲能 i 在 t 時刻是否正在充電
        # discharge_b[i][t]：儲能 i 在 t 時刻是否正在放電
        # SOC[i][t]：儲能 i 在 t 時刻的剩餘電量（State of Charge），連續變數
        #   上下界 = [soc_min, soc_max]（由 processor_settings.json 設定）
        for i in self._sto_ids:
            sto = self._sto_map[i]
            self._charge_b[i] = {
                t: pulp.LpVariable(f"chgb_{i}_{t}", cat="Binary") for t in T
            }
            self._discharge_b[i] = {
                t: pulp.LpVariable(f"disb_{i}_{t}", cat="Binary") for t in T
            }
            self._SOC[i] = {
                t: pulp.LpVariable(
                    f"SOC_{i}_{t}", lowBound=sto.soc_min, upBound=sto.soc_max
                )
                for t in T
            }

        # Sell[t]：t 時刻賣給市場的電量（MWh），≥ 0
        # 同時也是 Phase 3 可重導向給 sporadic/aperiodic 的 reserve 上限
        self._Sell = {t: pulp.LpVariable(f"Sell_{t}", lowBound=0) for t in T}

        # x[job][t]：job j 在 t 時刻是否正在執行（1=執行, 0=未執行）
        # 只對普通 job 定義（充電 job 不需要 x，由 k 直接控制）
        # 只在 job 的 [release, deadline] 區間內定義
        for job in self._regular_jobs:
            self._x[job.job_id] = {
                t: pulp.LpVariable(f"x_{job.job_id}_{t}", cat="Binary")
                for t in range(job.release, job.deadline + 1)
            }

    def _add_objective(self) -> None:
        """Defines the objective function (minimize gen cost - maximize sell revenue).

        With the Level 2 storage relaxation a battery aging term is added: each MWh
        discharged carries an ``aging_cost`` so the optimizer trades cycling the
        battery against running generators. The term is zero (and absent) under the
        Level 1 default.
        """
        # 目標函數：min F = f2 + f3（Level 1 不含 f1，因為 periodic job 必須全完成）
        #
        # f2 = Σ_{i∈generators} Σ_t (cost_fixed * u[i][t] + cost_variable * P[i][t])
        #      發電機的固定開機成本（每拍開著就計費）+ 可變發電成本（和輸出量成正比）
        f2 = pulp.lpSum(
            self._gen_map[i].cost_fixed * self._u[i][t]
            + self._gen_map[i].cost_variable * self._P[i][t]
            for i in self._gen_ids
            for t in self._time_steps
        )
        # f3 = -Σ_t (price[t] * Sell[t])
        #      市場售電收益取負值（因為是最小化問題，賣越多目標值越小，等同最大化收益）
        f3 = -pulp.lpSum(
            self._price_map[t] * self._Sell[t] for t in self._time_steps
        )
        objective = f2 + f3

        # Level 2 選項：加入電池老化成本
        # aging_cost * Σ P[storage][t]（放電量越多，老化成本越高）
        # 預設 aging_cost=0，不影響 Level 1 結果
        if self._relax.aging_cost > 0.0:
            # R-aging: battery throughput (discharge) aging cost, reusing P[storage].
            objective += pulp.lpSum(
                self._relax.aging_cost * self._P[i][t]
                for i in self._sto_ids
                for t in self._time_steps
            )
        self._prob += objective, "objective"

    def _add_job_constraints(self) -> None:
        """Declares task execution, demand, and preemption constraints."""
        for job in self._regular_jobs:
            jid = job.job_id
            window = range(job.release, job.deadline + 1)

            for t in window:
                # 每個 job 在 t 時刻從所有裝置取的電量總和
                k_sum = pulp.lpSum(
                    self._k[jid][i][t]
                    for i in self._all_device_ids
                    if t in self._k[jid].get(i, {})
                )
                # C1：執行時需要 demand（w）MWh，不執行時為 0
                # k_sum = demand * x[j][t]
                # → x=1（執行中）時 k_sum = demand；x=0 時 k_sum = 0
                self._prob += (
                    k_sum == job.demand * self._x[jid][t],
                    f"C1_demand_{jid}_{t}",
                )

            # C3：job 必須剛好執行 e 個時槽（不多不少）
            self._prob += (
                pulp.lpSum(self._x[jid][t] for t in window) == job.execution,
                f"C3_exec_{jid}",
            )

            # C5：非可搶佔（preempt=0）的 job 必須連續執行
            # 做法：統計 x 從 0 變 1 的次數（「起跑」次數），限制 ≤ 1
            # → 最多只能有一段連續區塊
            # 技術細節：release 時刻本身也要算一次可能的起跑點，
            # 否則 solver 可能在 release 開始一段、稍後再插入第二段而不被偵測到
            if not job.preemptive:
                rises: list[pulp.LpVariable] = []
                for t in window:
                    rise = pulp.LpVariable(f"rise_{jid}_{t}", cat="Binary")
                    prev = self._x[jid][t - 1] if t > job.release else 0
                    self._prob += (
                        rise >= self._x[jid][t] - prev,
                        f"C5_rise_{jid}_{t}",
                    )
                    rises.append(rise)
                self._prob += (
                    pulp.lpSum(rises) <= 1,
                    f"C5_contiguous_{jid}",
                )

    def _add_generator_constraints(self) -> None:
        """Declares output limits, ramp rates, and min up/down time constraints."""
        for i in self._gen_ids:
            gen = self._gen_map[i]
            # 從 processor_settings.json 讀取排程前的初始狀態
            u_initial = 1 if gen.initial_on_time > 0 else 0  # 初始是否開機
            p_initial = float(gen.initial_energy)             # 初始輸出功率

            for t in self._time_steps:
                # C6：輸出上下界（開機時 output_min ≤ P ≤ output_max，關機時 P = 0）
                # u[i][t] 乘以上下界，確保關機時輸出為零
                self._prob += (
                    self._P[i][t] >= gen.output_min * self._u[i][t],
                    f"C6_lo_{i}_{t}",
                )
                self._prob += (
                    self._P[i][t] <= gen.output_max * self._u[i][t],
                    f"C6_hi_{i}_{t}",
                )

                # C7：爬坡率限制（每個 tick 輸出變化量不能超過 ramp rate）
                # 防止發電機輸出突然大幅跳變（物理限制）
                p_prev = self._P[i][t - 1] if t > 1 else p_initial
                self._prob += (
                    self._P[i][t] - p_prev <= gen.ramp_up_rate * self._DELTA_T,
                    f"C7_up_{i}_{t}",
                )
                self._prob += (
                    p_prev - self._P[i][t]
                    <= gen.ramp_down_rate * self._DELTA_T,
                    f"C7_dn_{i}_{t}",
                )

                # start/stop 與 u 的關聯：
                # start[t] - stop[t] = u[t] - u[t-1]
                # → 開機那拍 start=1；關機那拍 stop=1；其他拍兩者都 0
                # 同一拍不能同時啟動和關機（start + stop ≤ 1）
                u_prev = self._u[i][t - 1] if t > 1 else u_initial
                self._prob += (
                    self._start[i][t] - self._stop[i][t]
                    == self._u[i][t] - u_prev,
                    f"startstop_link_{i}_{t}",
                )
                self._prob += (
                    self._start[i][t] + self._stop[i][t] <= 1,
                    f"startstop_excl_{i}_{t}",
                )

            # C9：最短開機時間（啟動後至少維持 min_up_time 個 tick）
            # 啟動後的接下來 ut 個 tick，u 都必須是 1
            ut = gen.min_up_time
            for t in self._time_steps:
                end = min(t + ut - 1, self._horizon)
                self._prob += (
                    pulp.lpSum(
                        self._u[i][s] for s in range(t, end + 1)
                    )
                    >= (end - t + 1) * self._start[i][t],
                    f"C9_minup_{i}_{t}",
                )

            # C10：最短關機時間（關機後至少維持 min_down_time 個 tick）
            dt = gen.min_down_time
            for t in self._time_steps:
                end = min(t + dt - 1, self._horizon)
                self._prob += (
                    pulp.lpSum(
                        1 - self._u[i][s] for s in range(t, end + 1)
                    )
                    >= (end - t + 1) * self._stop[i][t],
                    f"C10_mindn_{i}_{t}",
                )

            # C11：排程前已開機但未達最短開機時間 → 強制繼續開機
            # 例如排程前已開 2 小時，min_up_time=4，則還需強制開 2 小時
            if gen.initial_on_time > 0:
                remaining_up = max(0, gen.min_up_time - gen.initial_on_time)
                for t in range(1, min(remaining_up, self._horizon) + 1):
                    self._prob += (
                        self._u[i][t] == 1,
                        f"C11_initup_{i}_{t}",
                    )

            # C12：排程前已關機但未達最短關機時間 → 強制繼續關機
            if gen.initial_off_time > 0:
                remaining_dn = max(0, gen.min_down_time - gen.initial_off_time)
                for t in range(1, min(remaining_dn, self._horizon) + 1):
                    self._prob += (
                        self._u[i][t] == 0,
                        f"C12_initdn_{i}_{t}",
                    )

    def _add_renewable_constraints(self) -> None:
        """Declares renewable capacity upper bounds based on hourly solar forecasts.

        C13 (Level 1): ``P[i][t] <= capacity * forecast * dt``.

        R-uncertainty (Level 2, Assumption 11): the forecast is derated by
        ``renewable_uncertainty_margin`` (beta) so the plan keeps headroom against
        an over-forecast, giving ``P[i][t] <= capacity * forecast * (1 - beta) * dt``.
        beta = 0 reproduces C13 exactly.
        """
        # keep = (1 - β)：beta=0 時 keep=1.0，等同 Level 1 不打折
        # beta=0.1 → 只用預測值的 90%，保留 10% headroom 給實際輸出偏低的情況
        keep = 1.0 - self._relax.renewable_uncertainty_margin
        for i in self._ren_ids:
            cap = self._capacity_map[i]          # 再生能源裝置容量（最大 MWh）
            forecasts = self._forecast_map[i]    # 每個 tick 的發電比例（0~1）
            for t in self._time_steps:
                forecast = forecasts.get(t, 0.0)
                # C13：P[i][t] ≤ capacity × forecast × (1-β)
                # → 再生能源輸出不能超過當前預測的（打折後）上限
                self._prob += (
                    self._P[i][t] <= cap * forecast * keep * self._DELTA_T,
                    f"C13_rencap_{i}_{t}",
                )

    def _add_storage_constraints(self) -> None:
        """Declares storage battery discharge, charge limits, and SOC balance constraints.

        Level 1: C14 (discharge cap), C15 (charge cap), C16 (SOC balance),
        C18 (discharge limited by usable SOC), C19 (no simultaneous charge/discharge).

        Level 2 (Assumption 12 -- realistic storage) refines these *in place* using
        the relaxation parameters, all reproducing the Level 1 equation when the
        parameters are at their defaults:

        * R-eff-c / R-eff-d / R-self (in C16): SOC balance becomes
          ``SOC[t] = (1 - sigma) * SOC[t-1] + eta_c * charge_in - P[t] / eta_d``.
        * R-eff-d (in C18): energy drawn from the cell to deliver ``P[t]`` is
          ``P[t] / eta_d``, bounded by usable SOC ``(1 - sigma) * SOC[t-1] - soc_min``.
        * R-soc-dis / R-soc-chg: SOC-dependent power tapers the rated discharge/charge
          power linearly down to ``soc_power_floor`` at the unfavourable SOC extreme.
        * R-cycle: total discharged energy over the horizon is capped by
          ``cycle_limit * (soc_max - soc_min)``.
        """
        relax = self._relax
        eta_c = relax.charge_efficiency    # 充電效率 η_c（預設 1.0=無損耗）
        eta_d = relax.discharge_efficiency # 放電效率 η_d（預設 1.0=無損耗）
        sigma = relax.self_discharge_rate  # 自耗損率 σ（預設 0.0=無靜置損耗）
        floor = relax.soc_power_floor      # SOC 依存功率下限（預設 1.0=不縮減）

        for i in self._sto_ids:
            sto = self._sto_map[i]
            chg_jid = self._storage_charging_job[i]  # 對應此儲能的充電 job ID
            usable = float(sto.soc_max - sto.soc_min)  # 可用 SOC 範圍（MWh）

            for t in self._time_steps:
                # C14：放電上限（開放電時 P ≤ discharge_max；關放電時 P = 0）
                # discharge_b[i][t]=1 表示本時槽正在放電
                self._prob += (
                    self._P[i][t]
                    <= sto.discharge_max * self._DELTA_T * self._discharge_b[i][t],
                    f"C14_discap_{i}_{t}",
                )

                # C15：充電上限（charge_in ≤ charge_max，charge_b=1 表示正在充電）
                # charge_in = 從所有發電機/再生能源流入儲能的電量總和
                charge_in = pulp.lpSum(
                    self._k[chg_jid][dev][t]
                    for dev in self._gen_ren_ids
                    if t in self._k[chg_jid].get(dev, {})
                )
                self._prob += (
                    charge_in
                    <= sto.charge_max * self._DELTA_T * self._charge_b[i][t],
                    f"C15_chgcap_{i}_{t}",
                )

                # t=1 時使用初始 SOC；之後用上一拍的 SOC 變數
                soc_prev = (
                    self._SOC[i][t - 1] if t > 1 else float(sto.soc_init)
                )

                # C16：SOC 能量平衡（Level 2 加入效率與自耗損）
                # SOC[t] = (1-σ)·SOC[t-1] + η_c·charge_in - (1/η_d)·P
                # Level 1 預設：σ=0, η_c=1, η_d=1 → SOC[t] = SOC[t-1] + charge_in - P
                self._prob += (
                    self._SOC[i][t]
                    == (1.0 - sigma) * soc_prev
                    + eta_c * charge_in
                    - (1.0 / eta_d) * self._P[i][t],
                    f"C16_socbal_{i}_{t}",
                )

                # C18：放電量不能超過「當前可用 SOC」
                # 放電實際消耗電池量 = P / η_d（效率損耗）
                # 可用量 = (1-σ)·SOC_prev - soc_min（扣掉下限後才能用）
                self._prob += (
                    (1.0 / eta_d) * self._P[i][t]
                    <= (1.0 - sigma) * soc_prev - sto.soc_min,
                    f"C18_dislim_{i}_{t}",
                )

                # C19：同一拍不能同時充電和放電（物理限制）
                # charge_b + discharge_b ≤ 1 確保兩者互斥
                self._prob += (
                    self._charge_b[i][t] + self._discharge_b[i][t] <= 1,
                    f"C19_nosim_{i}_{t}",
                )

                # Level 2 選項：SOC 依存功率縮減（soc_power_floor < 1.0 時才生效）
                # 電量接近極限時，可用充放電功率線性縮減到 floor 倍
                # 放電：電量越低（SOC 接近 soc_min），放電功率越受限
                # 充電：電量越高（SOC 接近 soc_max），充電功率越受限
                if floor < 1.0 and usable > 0:
                    dis_factor = floor + (1.0 - floor) * (
                        (soc_prev - sto.soc_min) / usable
                    )
                    self._prob += (
                        self._P[i][t]
                        <= sto.discharge_max * self._DELTA_T * dis_factor,
                        f"Rsocdis_{i}_{t}",
                    )
                    chg_factor = floor + (1.0 - floor) * (
                        (sto.soc_max - soc_prev) / usable
                    )
                    self._prob += (
                        charge_in <= sto.charge_max * self._DELTA_T * chg_factor,
                        f"Rsocchg_{i}_{t}",
                    )

            # Level 2 選項：循環壽命限制（cycle_limit 非 None 時才生效）
            # 整個排程期間，儲能的總放電量不能超過 cycle_limit × usable
            # 例如 cycle_limit=100, usable=10 → 最多放電 1000 MWh
            if relax.cycle_limit is not None and usable > 0:
                self._prob += (
                    pulp.lpSum(self._P[i][t] for t in self._time_steps)
                    <= relax.cycle_limit * usable,
                    f"Rcycle_{i}",
                )

    def _add_power_balance_constraints(self) -> None:
        """Declares grid-level power balance and device routing limitations."""
        for t in self._time_steps:
            # total_supply：所有裝置（發電機 + 再生能源 + 儲能放電）在 t 時刻的總輸出
            total_supply = pulp.lpSum(
                self._P[i][t] for i in self._all_device_ids
            )

            # total_k：所有 job 在 t 時刻從所有裝置取用的電量總和
            # 充電 job 只能從 gen/renewable 取電（is_charging → gen_ren_ids only）
            total_k = pulp.lpSum(
                self._k[job.job_id][i][t]
                for job in self._all_jobs
                for i in (
                    self._gen_ren_ids if job.is_charging else self._all_device_ids
                )
                if t in self._k[job.job_id].get(i, {})
            )

            # C23：全系統電力平衡（每個 tick 供需必須完全平衡）
            # total_supply = total_k（所有 job 用電）+ Sell[t]（賣給市場）
            # 不能有電力盈餘或不足，Sell 是「剩餘電量賣市場」的緩衝
            self._prob += (
                total_supply == total_k + self._Sell[t],
                f"C23_balance_{t}",
            )

            # C20：每台裝置的輸出 P[i][t] 必須 ≥ 從它取走的電量總和
            # 防止 solver 讓某台裝置被多個 job 超額使用
            for i in self._all_device_ids:
                alloc = pulp.lpSum(
                    self._k[job.job_id][i][t]
                    for job in self._all_jobs
                    if i in self._k[job.job_id]
                    and t in self._k[job.job_id][i]
                )
                self._prob += (
                    alloc <= self._P[i][t],
                    f"C20_devalloc_{i}_{t}",
                )

    def _add_precedence_constraints(self) -> None:
        """Declares Level 2 job precedence constraints (Assumption 5 relaxation).

        For an ordered pair ``(a, b)``, job ``b`` may only be active at tick ``t``
        once job ``a`` has accumulated all ``e_a`` of its active ticks before ``t``:

            ``e_a * x[b][t] <= sum_{s < t} x[a][s]``     for all t in b's window.

        This reuses the Level 1 activity variable ``x`` and adds no new variable.
        Pairs referencing a non-regular or unknown job ID are skipped.
        """
        # 無優先順序設定 → 直接返回，不加任何約束（Level 1 行為）
        if not self._relax.precedence:
            return
        # 建立 job_id → 執行時間的對照表，用來確認 job a 的 e_a
        exec_of = {job.job_id: job.execution for job in self._regular_jobs}
        for a, b in self._relax.precedence:
            # 跳過不存在的 job（充電 job 沒有 x 變數，也不受 precedence 限制）
            if a not in self._x or b not in self._x or a not in exec_of:
                continue
            e_a = exec_of[a]
            # 對 job b 的每個執行時槽 t：job a 必須在 t 之前已經完整執行 e_a 個 tick
            # e_a × x[b][t] ≤ Σ_{s<t} x[a][s]
            # 若 x[b][t]=1（b 正在執行），右側必須 ≥ e_a（a 已完整完成）
            for t in self._x[b]:
                self._prob += (
                    e_a * self._x[b][t]
                    <= pulp.lpSum(
                        self._x[a][s] for s in self._x[a] if s < t
                    ),
                    f"Rprec_{a}_before_{b}_{t}",
                )

    def _add_reserve_floor_constraints(self) -> None:
        """Declares the day-ahead reservation floor ``Sell[t] >= reserve_floor[t]``.

        The reservation strategy forces the plan to keep at least ``reserve_floor[t]``
        MWh of redirectable surplus (sold, by C23) at tick ``t``, so that surplus is
        available to accept sporadic/aperiodic jobs at acceptance time. It reuses the
        Level 1 ``Sell`` variable and adds no new variable.
        """
        # reserve_floor[t]：Phase 3 AcceptanceTester 要求「日前排程必須在 t 時刻
        # 保留至少這麼多 MWh 的可重導向剩餘（以 Sell 為緩衝）」
        # 這樣 Phase 3 才能把 Sell 的電量「轉給」sporadic/aperiodic job 使用
        # reserve_floor 為空（預設）→ 此函數不加任何約束，Sell 完全由 MILP 決定
        for t, floor in self._reserve_floor.items():
            if floor > 0 and t in self._Sell:
                # Sell[t] ≥ floor：強制留出至少 floor MWh 給 Phase 3 接受測試用
                self._prob += (
                    self._Sell[t] >= floor,
                    f"Rreserve_{t}",
                )

    def pin_prefix(
        self,
        committed_by_tick: dict[int, dict[str, Any]],
        upto_tick: int,
        tol: float = 1e-6,
    ) -> None:
        """Freezes already-executed ticks for receding-horizon re-optimization.

        Pins every continuous decision variable (``P``, ``k``, ``SOC``, ``Sell``) at
        ticks ``t < upto_tick`` to the committed value, and fixes the prefix binaries
        (``u``, ``charge_b``, ``discharge_b``, ``x``) derived from that committed
        state. Pinning the continuous state alone carries generator on/off duration,
        ramp position and SOC across the boundary; fixing the binaries as well lets
        the solver's presolve collapse the frozen prefix, which keeps each re-solve
        cheap as the committed window grows.

        Args:
            committed_by_tick: Map ``tick -> {"P", "k", "soc", "sell"}`` of committed
                (raw, unrounded) values from the previous solve.
            upto_tick: Exclusive upper bound; ticks strictly below it are frozen.
            tol: Tolerance band applied around each pinned value to absorb float noise.
        """

        def _pin(var: pulp.LpVariable, value: float) -> None:
            """將連續變數夾緊在 [value-tol, value+tol]，並保持原本的上下界。"""
            low, up = value - tol, value + tol
            if var.lowBound is not None:
                low = max(low, var.lowBound)
            if var.upBound is not None:
                up = min(up, var.upBound)
            var.bounds(low, up)

        def _fix_bin(var: pulp.LpVariable, on: bool) -> None:
            """將二元變數固定為 1（開）或 0（關）。"""
            var.bounds(1, 1) if on else var.bounds(0, 0)

        # 只凍結 t < upto_tick 的時槽（已執行的部分）
        # t >= upto_tick 的時槽仍讓 solver 自由優化（未來的部分）
        for t in self._time_steps:
            if t >= upto_tick:
                continue
            rec = committed_by_tick.get(t, {})
            p_vals = rec.get("P", {})

            # 凍結所有裝置的 P[i][t]（連續輸出變數）
            for i in self._all_device_ids:
                _pin(self._P[i][t], float(p_vals.get(i, 0.0)))

            # 凍結所有 job 在各裝置的電量分配 k[job][device][t]
            k_vals = rec.get("k", {})
            for job in self._all_jobs:
                alloc = k_vals.get(job.job_id, {})
                for i, var_by_t in self._k[job.job_id].items():
                    if t in var_by_t:
                        _pin(var_by_t[t], float(alloc.get(i, 0.0)))

            # 凍結儲能的 SOC[i][t]（確保下一個 window 的初始 SOC 正確接續）
            soc_vals = rec.get("soc", {})
            for i in self._sto_ids:
                if i in soc_vals:
                    _pin(self._SOC[i][t], float(soc_vals[i]))

            # 凍結售電量 Sell[t]
            if "sell" in rec:
                _pin(self._Sell[t], float(rec["sell"]))

            # 從已凍結的連續變數推導二元變數並固定
            # 讓 CBC presolve 能夠完整消除（collapse）已凍結的前綴，加速重解
            for i in self._gen_ids:
                # 發電機：P > 0 → u=1（開機），否則 u=0（關機）
                _fix_bin(self._u[i][t], float(p_vals.get(i, 0.0)) > tol)
            for i in self._sto_ids:
                # 儲能：P > 0 → 正在放電
                _fix_bin(self._discharge_b[i][t], float(p_vals.get(i, 0.0)) > tol)
                # 儲能：充電 job 的 k 總量 > 0 → 正在充電
                chg_jid = self._storage_charging_job.get(i)
                charge_in = sum(
                    k_vals.get(chg_jid, {}).values()
                ) if chg_jid else 0.0
                _fix_bin(self._charge_b[i][t], charge_in > tol)
            for job in self._regular_jobs:
                if t in self._x.get(job.job_id, {}):
                    # 普通 job：該 job 在 t 有分到電量 → x=1（正在執行）
                    k_sum = sum(k_vals.get(job.job_id, {}).values())
                    _fix_bin(self._x[job.job_id][t], k_sum > tol)
