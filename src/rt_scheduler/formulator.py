"""PuLP MILP formulation for the Virtual Power Plant scheduling problem."""

import pulp
from src.model import (
    ExpandedJob,
    Generator,
    ProcessorSettingsSystem,
    PriceSystem,
    Storage,
)


class VppMilpFormulator:
    """Formulates the mixed-integer linear programming (MILP) model.

    This class handles variable creation, objective setup, and constraint declaration
    for the real-time Virtual Power Plant (VPP) system over a planning horizon.
    """

    _DELTA_T: int = 1

    def __init__(
        self,
        assets: ProcessorSettingsSystem,
        prices: PriceSystem,
        all_jobs: list[ExpandedJob],
        horizon: int,
    ) -> None:
        """Initializes the formulator with assets, prices, and jobs.

        Args:
            assets: The physical device settings (generators, storages, renewables).
            prices: The day-ahead hourly price list.
            all_jobs: The combined list of expanded regular and charging jobs.
            horizon: The scheduling horizon duration (in ticks).
        """
        self._assets = assets
        self._prices = prices
        self._all_jobs = all_jobs
        self._horizon = horizon

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
        """Formulates the variables, objective, and all 23 constraints."""
        self._create_variables()
        self._add_objective()
        self._add_job_constraints()
        self._add_generator_constraints()
        self._add_renewable_constraints()
        self._add_storage_constraints()
        self._add_power_balance_constraints()

    def _create_variables(self) -> None:
        """Declares all decision variables for the MILP solver."""
        T = self._time_steps

        # P[i][t]: Device output
        for i in self._all_device_ids:
            self._P[i] = {
                t: pulp.LpVariable(f"P_{i}_{t}", lowBound=0) for t in T
            }

        # k[job][device][t]: Energy routing
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

        # Generator on/off/start/stop binaries
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

        # Storage charge/discharge binaries & SOC continuous variables
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

        # Sell[t]: Grid sales power
        self._Sell = {t: pulp.LpVariable(f"Sell_{t}", lowBound=0) for t in T}

        # x[job][t]: Job active state binaries (for regular jobs only)
        for job in self._regular_jobs:
            self._x[job.job_id] = {
                t: pulp.LpVariable(f"x_{job.job_id}_{t}", cat="Binary")
                for t in range(job.release, job.deadline + 1)
            }

    def _add_objective(self) -> None:
        """Defines the objective function (minimize gen cost - maximize sell revenue)."""
        f2 = pulp.lpSum(
            self._gen_map[i].cost_fixed * self._u[i][t]
            + self._gen_map[i].cost_variable * self._P[i][t]
            for i in self._gen_ids
            for t in self._time_steps
        )
        f3 = -pulp.lpSum(
            self._price_map[t] * self._Sell[t] for t in self._time_steps
        )
        self._prob += f2 + f3, "objective"

    def _add_job_constraints(self) -> None:
        """Declares task execution, demand, and preemption constraints."""
        for job in self._regular_jobs:
            jid = job.job_id
            window = range(job.release, job.deadline + 1)

            for t in window:
                k_sum = pulp.lpSum(
                    self._k[jid][i][t]
                    for i in self._all_device_ids
                    if t in self._k[jid].get(i, {})
                )
                # C1: energy demand when active
                self._prob += (
                    k_sum == job.demand * self._x[jid][t],
                    f"C1_demand_{jid}_{t}",
                )

            # C3: must execute exactly e time steps
            self._prob += (
                pulp.lpSum(self._x[jid][t] for t in window) == job.execution,
                f"C3_exec_{jid}",
            )

            # C5: non-preemptive jobs must run as a single contiguous block.
            # Treating x as 0 before release, every 0->1 transition (a "rise")
            # marks the start of a block; bounding the rises by 1 forces a
            # single block, given that Sum(x) == e is fixed by C3. The release
            # tick must be counted as a potential rise -- skipping it lets a
            # block start at release and a second block slip in later (one rise
            # + one fall) without being detected.
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
            u_initial = 1 if gen.initial_on_time > 0 else 0
            p_initial = float(gen.initial_energy)

            for t in self._time_steps:
                # C6: output bounds
                self._prob += (
                    self._P[i][t] >= gen.output_min * self._u[i][t],
                    f"C6_lo_{i}_{t}",
                )
                self._prob += (
                    self._P[i][t] <= gen.output_max * self._u[i][t],
                    f"C6_hi_{i}_{t}",
                )

                # C7: ramp rate
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

                # Start/stop binaries linking
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

            # C9: min up time
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

            # C10: min down time
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

            # C11: initial up time carry-over
            if gen.initial_on_time > 0:
                remaining_up = max(0, gen.min_up_time - gen.initial_on_time)
                for t in range(1, min(remaining_up, self._horizon) + 1):
                    self._prob += (
                        self._u[i][t] == 1,
                        f"C11_initup_{i}_{t}",
                    )

            # C12: initial down time carry-over
            if gen.initial_off_time > 0:
                remaining_dn = max(0, gen.min_down_time - gen.initial_off_time)
                for t in range(1, min(remaining_dn, self._horizon) + 1):
                    self._prob += (
                        self._u[i][t] == 0,
                        f"C12_initdn_{i}_{t}",
                    )

    def _add_renewable_constraints(self) -> None:
        """Declares renewable capacity upper bounds based on hourly solar forecasts."""
        for i in self._ren_ids:
            cap = self._capacity_map[i]
            forecasts = self._forecast_map[i]
            for t in self._time_steps:
                forecast = forecasts.get(t, 0.0)
                self._prob += (
                    self._P[i][t] <= cap * forecast * self._DELTA_T,
                    f"C13_rencap_{i}_{t}",
                )

    def _add_storage_constraints(self) -> None:
        """Declares storage battery discharge, charge limits, and SOC balance constraints."""
        for i in self._sto_ids:
            sto = self._sto_map[i]
            chg_jid = self._storage_charging_job[i]

            for t in self._time_steps:
                # C14: discharge cap
                self._prob += (
                    self._P[i][t]
                    <= sto.discharge_max * self._DELTA_T * self._discharge_b[i][t],
                    f"C14_discap_{i}_{t}",
                )

                # C15: charge cap
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

                # C16: SOC balance
                soc_prev = (
                    self._SOC[i][t - 1] if t > 1 else float(sto.soc_init)
                )
                self._prob += (
                    self._SOC[i][t] == soc_prev + charge_in - self._P[i][t],
                    f"C16_socbal_{i}_{t}",
                )

                # C18: discharge limit vs SOC
                soc_prev_val = (
                    self._SOC[i][t - 1] if t > 1 else float(sto.soc_init)
                )
                self._prob += (
                    self._P[i][t] <= soc_prev_val - sto.soc_min,
                    f"C18_dislim_{i}_{t}",
                )

                # C19: no simultaneous charge/discharge
                self._prob += (
                    self._charge_b[i][t] + self._discharge_b[i][t] <= 1,
                    f"C19_nosim_{i}_{t}",
                )

    def _add_power_balance_constraints(self) -> None:
        """Declares grid-level power balance and device routing limitations."""
        for t in self._time_steps:
            total_supply = pulp.lpSum(
                self._P[i][t] for i in self._all_device_ids
            )

            total_k = pulp.lpSum(
                self._k[job.job_id][i][t]
                for job in self._all_jobs
                for i in (
                    self._gen_ren_ids if job.is_charging else self._all_device_ids
                )
                if t in self._k[job.job_id].get(i, {})
            )

            # C23: power balance
            self._prob += (
                total_supply == total_k + self._Sell[t],
                f"C23_balance_{t}",
            )

            # C20: device output covers its allocations
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
