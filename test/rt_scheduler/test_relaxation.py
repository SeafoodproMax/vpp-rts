"""Tests for the Level 2 relaxed-assumption constraints in VppMilpFormulator.

Each test builds a small instance, solves it, and checks the relaxed constraint
behaves as specified -- and that the defaults reproduce the Level 1 model.
"""

import pulp

from src.model import (
    ChargingJob,
    ExpandedJob,
    Generator,
    HourlyForecast,
    PriceRecord,
    PriceSystem,
    ProcessorSettingsSystem,
    RenewableCapacity,
    RenewableForecast,
    Storage,
)
from src.rt_scheduler.formulator import VppMilpFormulator
from src.rt_scheduler.relaxation import RelaxationConfig

_SOLVER = pulp.PULP_CBC_CMD(msg=0)


def _assets(
    *,
    gen_max: int = 100,
    ren_cap: int = 100,
    forecast: float = 0.5,
    soc_init: int = 50,
    ticks: int = 2,
) -> ProcessorSettingsSystem:
    """Builds a 1-generator / 1-renewable / 1-battery asset system."""
    return ProcessorSettingsSystem(
        generators=[
            Generator(
                generator_id="g1",
                output_min=0,
                output_max=gen_max,
                ramp_up_rate=100,
                ramp_down_rate=100,
                min_up_time=1,
                min_down_time=1,
                cost_fixed=1,
                cost_variable=5,
                initial_on_time=0,
                initial_off_time=5,
                initial_energy=0,
            )
        ],
        renewable_capacities=[RenewableCapacity(renewable_id="r1", capacity=ren_cap)],
        renewable_forecasts=[
            RenewableForecast(
                renewable_id="r1",
                forecasts=[
                    HourlyForecast(hour=t, pv_forecast=forecast)
                    for t in range(1, ticks + 1)
                ],
            )
        ],
        storages=[
            Storage(
                storage_id="b1",
                soc_min=0,
                soc_max=100,
                discharge_max=50,
                charge_max=50,
                soc_init=soc_init,
            )
        ],
        charging_jobs=[ChargingJob(job_id="b1_chg", target_storage="b1")],
    )


def _prices(ticks: int = 2, price: int = 10) -> PriceSystem:
    return PriceSystem(
        price=[PriceRecord(hour=t, market_price=price) for t in range(1, ticks + 1)]
    )


def _job(jid: str, release: int, deadline: int, execution: int, demand: int,
         preempt: bool = True) -> ExpandedJob:
    return ExpandedJob(
        job_id=jid,
        source_task_id=jid.split("_")[0],
        release=release,
        deadline=deadline,
        execution=execution,
        demand=demand,
        preemptive=preempt,
    )


def _charging_job(ticks: int = 2) -> ExpandedJob:
    return ExpandedJob(
        job_id="b1_chg",
        source_task_id="b1_chg",
        release=1,
        deadline=ticks,
        execution=ticks,
        demand=0,
        preemptive=True,
        is_charging=True,
        target_storage="b1",
    )


def _solve(assets, prices, jobs, horizon, **kwargs) -> VppMilpFormulator:
    f = VppMilpFormulator(assets, prices, jobs, horizon, **kwargs)
    f.formulate()
    f.prob.solve(_SOLVER)
    return f


def test_default_relaxation_matches_level1() -> None:
    """An explicit default RelaxationConfig reproduces the no-relaxation objective."""
    assets, prices = _assets(), _prices()
    jobs = [_job("p1_0", 1, 2, 1, 30), _charging_job()]

    base = _solve(assets, prices, jobs, 2)
    relaxed = _solve(assets, prices, jobs, 2, relaxation=RelaxationConfig())

    assert pulp.LpStatus[base.prob.status] == "Optimal"
    assert pulp.LpStatus[relaxed.prob.status] == "Optimal"
    assert pulp.value(base.prob.objective) == pulp.value(relaxed.prob.objective)


def test_renewable_uncertainty_margin_derates_cap() -> None:
    """C13' caps renewable output at forecast * (1 - beta)."""
    assets = _assets(ren_cap=100, forecast=0.5)  # raw cap = 50/tick
    prices = _prices()
    jobs = [_job("p1_0", 1, 1, 1, 45), _charging_job()]  # demand 45 at t=1

    relax = RelaxationConfig(renewable_uncertainty_margin=0.2)  # derated cap = 40
    f = _solve(assets, prices, jobs, 2, relaxation=relax)

    assert pulp.LpStatus[f.prob.status] == "Optimal"
    assert pulp.value(f.P["r1"][1]) <= 40 + 1e-4
    # Demand 45 cannot be met by 40 of renewable alone -> generator must help.
    assert pulp.value(f.P["g1"][1]) >= 5 - 1e-4


def test_cycle_limit_caps_total_discharge() -> None:
    """R-cycle bounds total discharged energy over the horizon."""
    # No generator output and no sun -> only the battery can serve the demand.
    assets = _assets(soc_init=80, gen_max=0, forecast=0.0)
    prices = _prices(price=0)
    # Two ticks each needing 10 MWh; only the battery can supply it.
    jobs = [_job("p1_0", 1, 1, 1, 10), _job("p2_0", 2, 2, 1, 10), _charging_job()]

    # usable = soc_max - soc_min = 100; cycle_limit 0.15 -> max discharge 15 MWh < 20 needed.
    relax = RelaxationConfig(cycle_limit=0.15)
    f = _solve(assets, prices, jobs, 2, relaxation=relax)
    assert pulp.LpStatus[f.prob.status] != "Optimal"  # infeasible: cannot serve 20

    relax_ok = RelaxationConfig(cycle_limit=0.30)  # max discharge 30 MWh >= 20
    f2 = _solve(assets, prices, jobs, 2, relaxation=relax_ok)
    assert pulp.LpStatus[f2.prob.status] == "Optimal"
    total_dis = pulp.value(f2.P["b1"][1]) + pulp.value(f2.P["b1"][2])
    assert total_dis <= 30 + 1e-4


def test_discharge_efficiency_increases_soc_draw() -> None:
    """With eta_d < 1, delivering P[t] draws P[t]/eta_d from SOC (C16')."""
    assets = _assets(soc_init=50, gen_max=0, forecast=0.0)
    prices = _prices(price=0)
    jobs = [_job("p1_0", 1, 1, 1, 10), _charging_job()]

    # eta_d 0.5: 10 delivered draws 20 from SOC. A small aging cost makes minimal
    # discharge optimal so the battery delivers exactly the demand (no free dumping).
    relax = RelaxationConfig(discharge_efficiency=0.5, aging_cost=1.0)
    f = _solve(assets, prices, jobs, 2, relaxation=relax)
    assert pulp.LpStatus[f.prob.status] == "Optimal"
    assert abs(pulp.value(f.P["b1"][1]) - 10) < 1e-3   # delivered = demand
    assert abs(pulp.value(f.SOC["b1"][1]) - 30) < 1e-3  # drawn = 10 / 0.5 = 20


def test_precedence_orders_jobs() -> None:
    """R-prec keeps job b inactive until job a has completed."""
    assets, prices = _assets(), _prices(ticks=3)
    # Both jobs run 1 tick within [1,3]; precedence a before b.
    jobs = [_job("a_0", 1, 3, 1, 10), _job("b_0", 1, 3, 1, 10), _charging_job(ticks=3)]
    relax = RelaxationConfig(precedence=[("a_0", "b_0")])
    f = _solve(assets, prices, jobs, 3, relaxation=relax)

    assert pulp.LpStatus[f.prob.status] == "Optimal"
    a_active = {t: round(pulp.value(f.k["a_0"]["g1"][t]) or 0, 3) for t in (1, 2, 3)}
    b_active = {t: round(pulp.value(f.k["b_0"]["g1"][t]) or 0, 3) for t in (1, 2, 3)}
    a_done = next(t for t in (1, 2, 3) if a_active[t] > 0)
    b_done = next(t for t in (1, 2, 3) if b_active[t] > 0)
    assert b_done > a_done


def test_reserve_floor_enforced() -> None:
    """The reservation floor forces Sell[t] >= floor[t]."""
    assets, prices = _assets(), _prices()
    jobs = [_job("p1_0", 1, 2, 1, 10), _charging_job()]
    f = _solve(assets, prices, jobs, 2, reserve_floor={1: 25.0})
    assert pulp.LpStatus[f.prob.status] == "Optimal"
    assert pulp.value(f.Sell[1]) >= 25 - 1e-4


def test_pin_prefix_freezes_committed_tick() -> None:
    """pin_prefix forces an earlier tick's state to the committed values."""
    assets, prices = _assets(), _prices()
    jobs = [_job("p1_0", 1, 2, 1, 30), _charging_job()]

    base = _solve(assets, prices, jobs, 2)
    committed = {
        1: {
            "P": {i: pulp.value(base.P[i][1]) for i in base.all_device_ids},
            "k": {
                j.job_id: {
                    i: pulp.value(base.k[j.job_id][i][1])
                    for i in base.k[j.job_id]
                    if 1 in base.k[j.job_id][i]
                }
                for j in base.all_jobs
            },
            "soc": {i: pulp.value(base.SOC[i][1]) for i in base.sto_ids},
            "sell": pulp.value(base.Sell[1]),
        }
    }

    pinned = VppMilpFormulator(assets, prices, jobs, 2)
    pinned.formulate()
    pinned.pin_prefix(committed, upto_tick=2)
    pinned.prob.solve(_SOLVER)

    assert pulp.LpStatus[pinned.prob.status] == "Optimal"
    for i in pinned.all_device_ids:
        assert abs(pulp.value(pinned.P[i][1]) - committed[1]["P"][i]) < 1e-3
    assert abs(pulp.value(pinned.Sell[1]) - committed[1]["sell"]) < 1e-3
