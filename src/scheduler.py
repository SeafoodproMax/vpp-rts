from __future__ import annotations

from typing import Any, Optional

import pulp
from pydantic import BaseModel

from src.model.asset.processor_settings import ProcessorSettingsSystem
from src.model.asset.generator import Generator
from src.model.asset.storage import Storage
from src.model.market.price import PriceSystem
from src.model.task.task_system import TaskSystem
from src.utils.file_io import JsonIO


class ExpandedJob(BaseModel):
    """A concrete job instance expanded from a periodic task or charging job."""

    job_id: str
    source_task_id: str
    release: int
    deadline: int
    execution: int
    demand: int
    preemptive: bool
    is_charging: bool = False
    target_storage: Optional[str] = None


class Scheduler:
    """PuLP-based MILP day-ahead static scheduler for periodic jobs.

    Reads task set, processor settings, and market prices, formulates
    a mixed-integer linear program, solves it, and outputs schedule results.
    """

    _HORIZON: int = 72
    _DELTA_T: int = 1
    _EPS: float = 1e-6

    def __init__(
        self,
        processor_settings_path: str = "input/processor_settings.json",
        task_set_path: str = "output/task_set.json",
        price_path: str = "input/price_72hr.json",
        output_path: str = "output/schedule_result.json",
    ) -> None:
        self._processor_settings_path = processor_settings_path
        self._task_set_path = task_set_path
        self._price_path = price_path
        self._output_path = output_path

    def run(self) -> dict[str, Any]:
        """Executes the full scheduling pipeline.

        Returns:
            Dict with 'schedule_result' list and 'reserve' dict.
        """
        self._load_data()
        self._expand_jobs()
        self._build_index_maps()
        self._create_model()
        self._create_variables()
        self._add_objective()
        self._add_constraints()
        self._solve()
        result = self._extract_results()
        reserve = self._compute_reserve()
        return {"schedule_result": result, "reserve": reserve}

    # ------------------------------------------------------------------ data
    def _load_data(self) -> None:
        self._assets = ProcessorSettingsSystem.load_from_json(
            self._processor_settings_path
        )
        self._tasks = TaskSystem.load_from_json(self._task_set_path)
        self._prices = PriceSystem.load_from_json(self._price_path)

        self._price_map: dict[int, int] = {
            p.hour: p.market_price for p in self._prices.price
        }
        self._forecast_map: dict[str, dict[int, float]] = {}
        for rf in self._assets.renewable_forecasts:
            self._forecast_map[rf.renewable_id] = {
                f.hour: f.pv_forecast for f in rf.forecasts
            }
        self._capacity_map: dict[str, int] = {
            rc.renewable_id: rc.capacity
            for rc in self._assets.renewable_capacities
        }
        self._charging_target: dict[str, str] = {
            cj.job_id: cj.target_storage
            for cj in self._assets.charging_jobs
        }
        self._gen_map: dict[str, Generator] = {
            g.generator_id: g for g in self._assets.generators
        }
        self._sto_map: dict[str, Storage] = {
            s.storage_id: s for s in self._assets.storages
        }

    # -------------------------------------------------------------- expansion
    def _expand_jobs(self) -> None:
        self._regular_jobs: list[ExpandedJob] = []
        self._charging_jobs_list: list[ExpandedJob] = []

        for task in self._tasks.periodic_tasks:
            k = 0
            while True:
                abs_release = task.r + k * task.p
                abs_deadline = abs_release + task.d - 1
                if abs_release > self._HORIZON or abs_deadline > self._HORIZON:
                    break
                self._regular_jobs.append(
                    ExpandedJob(
                        job_id=f"{task.task_id}_{k}",
                        source_task_id=task.task_id,
                        release=abs_release,
                        deadline=abs_deadline,
                        execution=task.e,
                        demand=task.w,
                        preemptive=task.preempt == 1,
                    )
                )
                k += 1

        for cj in self._assets.charging_jobs:
            self._charging_jobs_list.append(
                ExpandedJob(
                    job_id=cj.job_id,
                    source_task_id=cj.job_id,
                    release=1,
                    deadline=self._HORIZON,
                    execution=self._HORIZON,
                    demand=0,
                    preemptive=True,
                    is_charging=True,
                    target_storage=cj.target_storage,
                )
            )

        self._all_jobs = self._regular_jobs + self._charging_jobs_list

    # --------------------------------------------------------------- indices
    def _build_index_maps(self) -> None:
        self._time_steps = list(range(1, self._HORIZON + 1))
        self._gen_ids = [g.generator_id for g in self._assets.generators]
        self._ren_ids = [
            rc.renewable_id for rc in self._assets.renewable_capacities
        ]
        self._sto_ids = [s.storage_id for s in self._assets.storages]
        self._all_device_ids = self._gen_ids + self._ren_ids + self._sto_ids
        self._gen_ren_ids = self._gen_ids + self._ren_ids

        self._job_map: dict[str, ExpandedJob] = {
            j.job_id: j for j in self._all_jobs
        }
        self._regular_job_ids = [j.job_id for j in self._regular_jobs]
        self._charging_job_ids = [j.job_id for j in self._charging_jobs_list]

        self._big_m = float(
            sum(g.output_max for g in self._assets.generators)
            + sum(rc.capacity for rc in self._assets.renewable_capacities)
            + sum(s.discharge_max for s in self._assets.storages)
        )

        self._storage_charging_job: dict[str, str] = {}
        for cj in self._charging_jobs_list:
            self._storage_charging_job[cj.target_storage] = cj.job_id

    # ----------------------------------------------------------------- model
    def _create_model(self) -> None:
        self._prob = pulp.LpProblem("VPP_DayAhead", pulp.LpMinimize)

    # --------------------------------------------------------------- variables
    def _create_variables(self) -> None:
        T = self._time_steps

        self._P: dict[str, dict[int, pulp.LpVariable]] = {}
        for i in self._all_device_ids:
            self._P[i] = {
                t: pulp.LpVariable(f"P_{i}_{t}", lowBound=0) for t in T
            }

        self._k: dict[str, dict[str, dict[int, pulp.LpVariable]]] = {}
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

        self._u: dict[str, dict[int, pulp.LpVariable]] = {}
        self._start: dict[str, dict[int, pulp.LpVariable]] = {}
        self._stop: dict[str, dict[int, pulp.LpVariable]] = {}
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

        self._charge_b: dict[str, dict[int, pulp.LpVariable]] = {}
        self._discharge_b: dict[str, dict[int, pulp.LpVariable]] = {}
        self._SOC: dict[str, dict[int, pulp.LpVariable]] = {}
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

        self._Sell: dict[int, pulp.LpVariable] = {
            t: pulp.LpVariable(f"Sell_{t}", lowBound=0) for t in T
        }

        self._x: dict[str, dict[int, pulp.LpVariable]] = {}
        for job in self._regular_jobs:
            self._x[job.job_id] = {
                t: pulp.LpVariable(f"x_{job.job_id}_{t}", cat="Binary")
                for t in range(job.release, job.deadline + 1)
            }

    # ------------------------------------------------------------- objective
    def _add_objective(self) -> None:
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

    # ----------------------------------------------------------- constraints
    def _add_constraints(self) -> None:
        self._add_job_constraints()
        self._add_generator_constraints()
        self._add_renewable_constraints()
        self._add_storage_constraints()
        self._add_power_balance_constraints()

    def _add_job_constraints(self) -> None:
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

            # C5: non-preemptive continuity (at most one start + one stop)
            if not job.preemptive:
                y_rise: dict[int, pulp.LpVariable] = {}
                z_fall: dict[int, pulp.LpVariable] = {}
                for t in window:
                    if t == job.release:
                        continue
                    y_rise[t] = pulp.LpVariable(
                        f"y_{jid}_{t}", cat="Binary"
                    )
                    z_fall[t] = pulp.LpVariable(
                        f"z_{jid}_{t}", cat="Binary"
                    )
                    self._prob += (
                        y_rise[t] >= self._x[jid][t] - self._x[jid][t - 1],
                        f"C5_rise_{jid}_{t}",
                    )
                    self._prob += (
                        z_fall[t] >= self._x[jid][t - 1] - self._x[jid][t],
                        f"C5_fall_{jid}_{t}",
                    )
                if y_rise:
                    self._prob += (
                        pulp.lpSum(y_rise.values()) <= 1,
                        f"C5_once_start_{jid}",
                    )
                    self._prob += (
                        pulp.lpSum(z_fall.values()) <= 1,
                        f"C5_once_stop_{jid}",
                    )

    def _add_generator_constraints(self) -> None:
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

                # Start/stop linking
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
                end = min(t + ut - 1, self._HORIZON)
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
                end = min(t + dt - 1, self._HORIZON)
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
                for t in range(1, min(remaining_up, self._HORIZON) + 1):
                    self._prob += (
                        self._u[i][t] == 1,
                        f"C11_initup_{i}_{t}",
                    )

            # C12: initial down time carry-over
            if gen.initial_off_time > 0:
                remaining_dn = max(0, gen.min_down_time - gen.initial_off_time)
                for t in range(1, min(remaining_dn, self._HORIZON) + 1):
                    self._prob += (
                        self._u[i][t] == 0,
                        f"C12_initdn_{i}_{t}",
                    )

    def _add_renewable_constraints(self) -> None:
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

    # ----------------------------------------------------------------- solve
    def _solve(self) -> None:
        self._prob.solve(pulp.PULP_CBC_CMD(msg=1))
        status = pulp.LpStatus[self._prob.status]
        if status != "Optimal":
            raise RuntimeError(f"Solver did not find optimal solution: {status}")
        print(f"Objective value: {pulp.value(self._prob.objective):.2f}")

    # -------------------------------------------------------------- results
    def _clean(self, val: float) -> float:
        return 0.0 if abs(val) < self._EPS else round(val, 4)

    def _extract_results(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        for t in self._time_steps:
            p_dict: dict[str, float] = {}
            for i in self._all_device_ids:
                v = self._clean(pulp.value(self._P[i][t]))
                if v > 0:
                    p_dict[i] = v

            k_dict: dict[str, dict[str, float]] = {}
            for job in self._all_jobs:
                allowed = (
                    self._gen_ren_ids
                    if job.is_charging
                    else self._all_device_ids
                )
                job_alloc: dict[str, float] = {}
                for i in allowed:
                    if t in self._k[job.job_id].get(i, {}):
                        v = self._clean(
                            pulp.value(self._k[job.job_id][i][t])
                        )
                        if v > 0:
                            job_alloc[i] = v
                if job_alloc:
                    k_dict[job.job_id] = job_alloc

            soc_dict: dict[str, float] = {}
            for i in self._sto_ids:
                soc_dict[i] = self._clean(pulp.value(self._SOC[i][t]))

            sell_val = self._clean(pulp.value(self._Sell[t]))

            results.append(
                {
                    "t": t,
                    "P": p_dict,
                    "k": k_dict,
                    "sell": sell_val,
                    "soc": soc_dict,
                    "missed_aperiodic": [],
                    "rejected_sporadic": [],
                }
            )

        return results

    def _compute_reserve(self) -> dict[int, float]:
        reserve: dict[int, float] = {}
        for t in self._time_steps:
            total_supply = sum(
                self._clean(pulp.value(self._P[i][t]))
                for i in self._all_device_ids
            )
            total_demand = sum(
                self._clean(pulp.value(self._k[job.job_id][i][t]))
                for job in self._regular_jobs
                for i in self._all_device_ids
                if t in self._k[job.job_id].get(i, {})
            )
            reserve[t] = self._clean(total_supply - total_demand)
        return reserve


def main() -> None:
    """Runs the MILP scheduler and outputs schedule_result.json."""
    scheduler = Scheduler()
    output = scheduler.run()
    JsonIO.save(
        {"schedule_result": output["schedule_result"]}, "output/schedule_result.json"
    )
    print(f"Schedule saved to output/schedule_result.json")
    print(f"Time steps: {len(output['schedule_result'])}")


if __name__ == "__main__":
    main()
