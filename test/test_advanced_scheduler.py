"""Tests for the Level 2 AdvancedScheduler (rolling-horizon re-optimization).

Uses a small injected instance so the rolling solve runs quickly, and checks the
physical invariants of the produced schedule (energy balance, SOC bounds, periodic
completion) plus the dynamic-run bookkeeping.
"""

from src.advanced_scheduler import AdvancedScheduler
from src.model import (
    AperiodicTask,
    ChargingJob,
    Generator,
    HourlyForecast,
    PeriodicTask,
    PriceRecord,
    PriceSystem,
    ProcessorSettingsSystem,
    RenewableCapacity,
    RenewableForecast,
    SporadicTask,
    Storage,
    TaskSystem,
)
from src.rt_scheduler.relaxation import RelaxationConfig

_H = 6


def _assets() -> ProcessorSettingsSystem:
    return ProcessorSettingsSystem(
        generators=[
            Generator(
                generator_id="g1",
                output_min=0,
                output_max=60,
                ramp_up_rate=60,
                ramp_down_rate=60,
                min_up_time=1,
                min_down_time=1,
                cost_fixed=10,
                cost_variable=5,
                initial_on_time=0,
                initial_off_time=5,
                initial_energy=0,
            )
        ],
        renewable_capacities=[RenewableCapacity(renewable_id="pv1", capacity=20)],
        renewable_forecasts=[
            RenewableForecast(
                renewable_id="pv1",
                forecasts=[
                    HourlyForecast(hour=t, pv_forecast=(0.5 if t in (3, 4) else 0.0))
                    for t in range(1, _H + 1)
                ],
            )
        ],
        storages=[
            Storage(
                storage_id="b1",
                soc_min=10,
                soc_max=60,
                discharge_max=20,
                charge_max=20,
                soc_init=40,
            )
        ],
        charging_jobs=[ChargingJob(job_id="b1_chg", target_storage="b1")],
    )


def _prices() -> PriceSystem:
    return PriceSystem(
        price=[PriceRecord(hour=t, market_price=8 + t) for t in range(1, _H + 1)]
    )


def _tasks() -> TaskSystem:
    return TaskSystem(
        periodic_tasks=[
            PeriodicTask(task_id="p1", r=1, e=1, d=2, w=10, preempt=1, p=3),
        ],
        sporadic_tasks=[
            SporadicTask(task_id="s1", r=2, e=1, d=3, w=8, preempt=1),
        ],
        aperiodic_tasks=[
            AperiodicTask(task_id="a1", r=1, e=1, d=4, w=6, preempt=1),
        ],
    )


def _run() -> dict:
    scheduler = AdvancedScheduler(
        processor_settings_path="",
        task_set_path="",
        price_path="",
        horizon=_H,
        epsilon=1e-6,
        relaxation=RelaxationConfig(
            charge_efficiency=0.95,
            discharge_efficiency=0.95,
            self_discharge_rate=0.002,
            renewable_uncertainty_margin=0.1,
        ),
        reopt_interval=3,
        time_limit=15,
        gap_rel=0.0,
        assets=_assets(),
        tasks=_tasks(),
        prices=_prices(),
    )
    return scheduler.run()


def test_schedule_shape_and_keys() -> None:
    """The dynamic schedule spans the horizon with the Level 1 record schema."""
    result = _run()
    schedule = result["schedule_result"]
    assert [r["t"] for r in schedule] == list(range(1, _H + 1))
    for rec in schedule:
        assert set(["t", "P", "k", "sell", "soc"]).issubset(rec)
        assert {
            "accepted_sporadic",
            "scheduled_aperiodic",
            "missed_aperiodic",
            "rejected_sporadic",
        }.issubset(rec)
    assert result["run_log"], "expected at least one re-optimization round"
    assert "summary" in result["log"]


def test_energy_balance_per_tick() -> None:
    """Every tick satisfies C23: total generation == routed demand + sales."""
    schedule = _run()["schedule_result"]
    for rec in schedule:
        gen = sum(rec["P"].values())
        routed = sum(sum(alloc.values()) for alloc in rec["k"].values())
        assert abs(gen - (routed + rec["sell"])) < 1e-2, rec["t"]


def test_soc_within_bounds() -> None:
    """Storage SOC stays within [soc_min, soc_max] at every tick."""
    schedule = _run()["schedule_result"]
    for rec in schedule:
        assert 10 - 1e-2 <= rec["soc"]["b1"] <= 60 + 1e-2


def test_periodic_jobs_complete_before_deadline() -> None:
    """Each periodic job runs its execution count within its absolute window."""
    schedule = _run()["schedule_result"]
    # p1: period 3, e=1 -> instances p1_1 (release 1, dl 2) and p1_2 (release 4, dl 5).
    active = {}
    for rec in schedule:
        for jid, alloc in rec["k"].items():
            if jid.startswith("p1_") and any(v > 0 for v in alloc.values()):
                active.setdefault(jid, []).append(rec["t"])
    for jid, windows in {"p1_1": (1, 2), "p1_2": (4, 5)}.items():
        ticks = active.get(jid, [])
        assert len(ticks) == 1, f"{jid} ran {ticks}, expected 1 tick"
        assert windows[0] <= ticks[0] <= windows[1]


def test_accepted_sporadic_meets_hard_deadline() -> None:
    """An accepted sporadic job is annotated and finishes by its hard deadline."""
    result = _run()
    schedule = result["schedule_result"]
    sporadic_log = {e["task_id"]: e for e in result["log"]["sporadic"]}
    for jid, entry in sporadic_log.items():
        if entry["decision"] == "accepted":
            assert entry["completion_tick"] is not None
            assert entry["completion_tick"] <= entry["absolute_deadline"]
