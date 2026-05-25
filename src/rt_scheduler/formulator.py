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
        """Defines the objective function (minimize gen cost - maximize sell revenue).

        With the Level 2 storage relaxation a battery aging term is added: each MWh
        discharged carries an ``aging_cost`` so the optimizer trades cycling the
        battery against running generators. The term is zero (and absent) under the
        Level 1 default.
        """
        f2 = pulp.lpSum(
            self._gen_map[i].cost_fixed * self._u[i][t]
            + self._gen_map[i].cost_variable * self._P[i][t]
            for i in self._gen_ids
            for t in self._time_steps
        )
        f3 = -pulp.lpSum(
            self._price_map[t] * self._Sell[t] for t in self._time_steps
        )
        objective = f2 + f3
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
        """Declares renewable capacity upper bounds based on hourly solar forecasts.

        C13 (Level 1): ``P[i][t] <= capacity * forecast * dt``.

        R-uncertainty (Level 2, Assumption 11): the forecast is derated by
        ``renewable_uncertainty_margin`` (beta) so the plan keeps headroom against
        an over-forecast, giving ``P[i][t] <= capacity * forecast * (1 - beta) * dt``.
        beta = 0 reproduces C13 exactly.
        """
        keep = 1.0 - self._relax.renewable_uncertainty_margin
        for i in self._ren_ids:
            cap = self._capacity_map[i]
            forecasts = self._forecast_map[i]
            for t in self._time_steps:
                forecast = forecasts.get(t, 0.0)
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
        eta_c = relax.charge_efficiency
        eta_d = relax.discharge_efficiency
        sigma = relax.self_discharge_rate
        floor = relax.soc_power_floor

        for i in self._sto_ids:
            sto = self._sto_map[i]
            chg_jid = self._storage_charging_job[i]
            usable = float(sto.soc_max - sto.soc_min)

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

                soc_prev = (
                    self._SOC[i][t - 1] if t > 1 else float(sto.soc_init)
                )

                # C16: SOC balance (with efficiency + self-discharge when relaxed)
                self._prob += (
                    self._SOC[i][t]
                    == (1.0 - sigma) * soc_prev
                    + eta_c * charge_in
                    - (1.0 / eta_d) * self._P[i][t],
                    f"C16_socbal_{i}_{t}",
                )

                # C18: discharge limited by usable stored energy
                self._prob += (
                    (1.0 / eta_d) * self._P[i][t]
                    <= (1.0 - sigma) * soc_prev - sto.soc_min,
                    f"C18_dislim_{i}_{t}",
                )

                # C19: no simultaneous charge/discharge
                self._prob += (
                    self._charge_b[i][t] + self._discharge_b[i][t] <= 1,
                    f"C19_nosim_{i}_{t}",
                )

                # R-soc-dis / R-soc-chg: SOC-dependent power limit (taper to floor).
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

            # R-cycle: throughput limit over the whole horizon (reuses P[storage]).
            if relax.cycle_limit is not None and usable > 0:
                self._prob += (
                    pulp.lpSum(self._P[i][t] for t in self._time_steps)
                    <= relax.cycle_limit * usable,
                    f"Rcycle_{i}",
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

    def _add_precedence_constraints(self) -> None:
        """Declares Level 2 job precedence constraints (Assumption 5 relaxation).

        For an ordered pair ``(a, b)``, job ``b`` may only be active at tick ``t``
        once job ``a`` has accumulated all ``e_a`` of its active ticks before ``t``:

            ``e_a * x[b][t] <= sum_{s < t} x[a][s]``     for all t in b's window.

        This reuses the Level 1 activity variable ``x`` and adds no new variable.
        Pairs referencing a non-regular or unknown job ID are skipped.
        """
        if not self._relax.precedence:
            return
        exec_of = {job.job_id: job.execution for job in self._regular_jobs}
        for a, b in self._relax.precedence:
            if a not in self._x or b not in self._x or a not in exec_of:
                continue
            e_a = exec_of[a]
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
        for t, floor in self._reserve_floor.items():
            if floor > 0 and t in self._Sell:
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
            low, up = value - tol, value + tol
            if var.lowBound is not None:
                low = max(low, var.lowBound)
            if var.upBound is not None:
                up = min(up, var.upBound)
            var.bounds(low, up)

        def _fix_bin(var: pulp.LpVariable, on: bool) -> None:
            var.bounds(1, 1) if on else var.bounds(0, 0)

        for t in self._time_steps:
            if t >= upto_tick:
                continue
            rec = committed_by_tick.get(t, {})
            p_vals = rec.get("P", {})
            for i in self._all_device_ids:
                _pin(self._P[i][t], float(p_vals.get(i, 0.0)))

            k_vals = rec.get("k", {})
            for job in self._all_jobs:
                alloc = k_vals.get(job.job_id, {})
                for i, var_by_t in self._k[job.job_id].items():
                    if t in var_by_t:
                        _pin(var_by_t[t], float(alloc.get(i, 0.0)))

            soc_vals = rec.get("soc", {})
            for i in self._sto_ids:
                if i in soc_vals:
                    _pin(self._SOC[i][t], float(soc_vals[i]))

            if "sell" in rec:
                _pin(self._Sell[t], float(rec["sell"]))

            # Fix prefix binaries from the committed continuous state so presolve
            # can eliminate the frozen window.
            for i in self._gen_ids:
                _fix_bin(self._u[i][t], float(p_vals.get(i, 0.0)) > tol)
            for i in self._sto_ids:
                _fix_bin(self._discharge_b[i][t], float(p_vals.get(i, 0.0)) > tol)
                chg_jid = self._storage_charging_job.get(i)
                charge_in = sum(
                    k_vals.get(chg_jid, {}).values()
                ) if chg_jid else 0.0
                _fix_bin(self._charge_b[i][t], charge_in > tol)
            for job in self._regular_jobs:
                if t in self._x.get(job.job_id, {}):
                    k_sum = sum(k_vals.get(job.job_id, {}).values())
                    _fix_bin(self._x[job.job_id][t], k_sum > tol)
