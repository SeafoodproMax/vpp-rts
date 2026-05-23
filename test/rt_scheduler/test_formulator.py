"""Tests for the VppMilpFormulator class."""

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


def test_formulator_initialization_and_vars() -> None:
    """Tests variable registration inside the MILP formulator."""
    # Setup standard VPP physical data
    assets = ProcessorSettingsSystem(
        generators=[
            Generator(
                generator_id="g1",
                output_min=10,
                output_max=50,
                ramp_up_rate=10,
                ramp_down_rate=10,
                min_up_time=2,
                min_down_time=2,
                cost_fixed=100,
                cost_variable=5,
                initial_on_time=0,
                initial_off_time=5,
                initial_energy=0,
            )
        ],
        renewable_capacities=[RenewableCapacity(renewable_id="r1", capacity=100)],
        renewable_forecasts=[
            RenewableForecast(
                renewable_id="r1",
                forecasts=[
                    HourlyForecast(hour=1, pv_forecast=0.5),
                    HourlyForecast(hour=2, pv_forecast=0.8),
                ],
            )
        ],
        storages=[
            Storage(
                storage_id="b1",
                soc_min=10,
                soc_max=90,
                discharge_max=20,
                charge_max=20,
                soc_init=50,
            )
        ],
        charging_jobs=[
            ChargingJob(job_id="battery_1_chg", target_storage="b1")
        ],
    )

    prices = PriceSystem(
        price=[
            PriceRecord(hour=1, market_price=10),
            PriceRecord(hour=2, market_price=15),
        ]
    )

    jobs = [
        ExpandedJob(
            job_id="p1_0",
            source_task_id="p1",
            release=1,
            deadline=2,
            execution=1,
            demand=10,
            preemptive=True,
        ),
        ExpandedJob(
            job_id="battery_1_chg",
            source_task_id="battery_1_chg",
            release=1,
            deadline=2,
            execution=2,
            demand=0,
            preemptive=True,
            is_charging=True,
            target_storage="b1",
        ),
    ]

    formulator = VppMilpFormulator(assets, prices, jobs, horizon=2)
    formulator.formulate()

    # Verify basic lists
    assert len(formulator.all_device_ids) == 3  # g1, r1, b1
    assert "g1" in formulator.all_device_ids
    assert "r1" in formulator.all_device_ids
    assert "b1" in formulator.all_device_ids

    # Verify decision variable declarations
    # P variables for 3 devices over 2 ticks = 6 variables
    assert len(formulator.P) == 3
    assert len(formulator.P["g1"]) == 2
    assert len(formulator.P["r1"]) == 2
    assert len(formulator.P["b1"]) == 2

    # Sell variables for 2 ticks
    assert len(formulator.Sell) == 2

    # SOC variables for battery over 2 ticks
    assert len(formulator.SOC) == 1
    assert len(formulator.SOC["b1"]) == 2

    # Check that objective function exists
    assert formulator.prob.objective is not None
