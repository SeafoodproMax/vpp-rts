"""Tests for the Level 2 self-validator (relaxed-constraint checks).

These exercise the relaxed-assumption verification in isolation by feeding the
validator small hand-built schedules whose state exactly follows (or deliberately
breaks) the relaxed constraint equations. Standard library only, like the validator.
"""

from src.validator import Level2Validator

_RELAX = {
    "charge_efficiency": 0.9,
    "discharge_efficiency": 0.9,
    "self_discharge_rate": 0.01,
    "cycle_limit": 5.0,
    "soc_power_floor": 0.5,
    "aging_cost": 2.0,
}

_STORAGE_SETTINGS = {
    "generator": [],
    "storage": [
        {
            "storage_id": "b1",
            "soc_min": 10,
            "soc_max": 100,
            "discharge_max": 20,
            "charge_max": 20,
            "soc_init": 50,
        }
    ],
    "renewable_capacity": [],
    "renewable_forecast": [],
    "charging_jobs": [{"job_id": "b1_chg", "target_storage": "b1"}],
}

_EMPTY_TASKS = {"frame_size": 4, "periodic": {}, "sporadic": {}, "aperiodic": {}}


def _storage_schedule(soc1: float, soc2: float) -> dict:
    """A 2-tick schedule: b1 discharges 5 MWh at t1 (sold), idle at t2."""
    return {
        "schedule_result": [
            {"t": 1, "P": {"b1": 5.0}, "k": {}, "sell": 5.0,
             "soc": {"b1": soc1}, "missed_aperiodic": [], "rejected_sporadic": []},
            {"t": 2, "P": {}, "k": {}, "sell": 0.0,
             "soc": {"b1": soc2}, "missed_aperiodic": [], "rejected_sporadic": []},
        ]
    }


def _validator(schedule: dict, settings=_STORAGE_SETTINGS, tasks=_EMPTY_TASKS,
               precedence=None) -> Level2Validator:
    return Level2Validator(
        task_set=tasks,
        schedule=schedule,
        settings=settings,
        relaxation=_RELAX,
        horizon=2,
        precedence=precedence,
    )


def test_active_storage_relaxations_pass() -> None:
    """A schedule following the relaxed SOC dynamics earns the storage points."""
    # SOC[1] = 0.99*50 + 0.9*0 - (1/0.9)*5 = 43.9444 ; SOC[2] = 0.99*43.9444 = 43.505
    v = _validator(_storage_schedule(43.9444, 43.505))
    results = {r.item: r for r in v.check_item3_relaxations()}

    for item in ("R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9"):
        assert results[item].status == "PASS", (item, results[item].violations)
    # No realized series and no precedence configured -> those relaxations SKIP.
    assert results["R1"].status == "SKIP"
    assert results["R10"].status == "SKIP"
    awarded = sum(r.score for r in results.values())
    assert awarded == 8.0  # R2..R9


def test_corrupted_soc_fails_efficiency_checks() -> None:
    """A SOC value violating the relaxed balance fails the C16′-derived checks."""
    v = _validator(_storage_schedule(40.0, 43.505))  # SOC[1] should be 43.9444
    results = {r.item: r for r in v.check_item3_relaxations()}
    assert results["R2"].status == "FAIL"  # charge efficiency (C16′)
    assert results["R4"].status == "FAIL"  # self-discharge (C16′)


def test_disabled_relaxations_skip() -> None:
    """When all relaxation params are at defaults, every relaxation is SKIP."""
    v = Level2Validator(
        task_set=_EMPTY_TASKS,
        schedule=_storage_schedule(40.0, 40.0),  # any SOC: nothing is checked
        settings=_STORAGE_SETTINGS,
        relaxation={},  # defaults -> no relaxation active
        horizon=2,
    )
    results = v.check_item3_relaxations()
    assert all(r.status == "SKIP" for r in results)
    assert sum(r.score for r in results) == 0.0


def test_precedence_pass_and_fail() -> None:
    """Precedence (R10) passes when a precedes b, fails when b runs first."""
    settings = {
        "generator": [{
            "generator_id": "g1", "output_min": 0, "output_max": 50,
            "ramp_up_rate": 50, "ramp_down_rate": 50, "min_up_time": 1,
            "min_down_time": 1, "cost_fixed": 1, "cost_variable": 1,
            "initial_on_time": 0, "initial_off_time": 5, "initial_energy": 0,
        }],
        "storage": [], "renewable_capacity": [], "renewable_forecast": [],
        "charging_jobs": [],
    }
    tasks = {
        "frame_size": 4,
        "periodic": {
            "pA": {"r": 1, "p": 10, "e": 1, "d": 3, "w": 5, "preempt": 1},
            "pB": {"r": 1, "p": 10, "e": 1, "d": 3, "w": 5, "preempt": 1},
        },
        "sporadic": {}, "aperiodic": {},
    }

    def sched(a_tick: int, b_tick: int) -> dict:
        recs = []
        for t in (1, 2):
            k = {}
            if t == a_tick:
                k["pA_0"] = {"g1": 5.0}
            if t == b_tick:
                k["pB_0"] = {"g1": 5.0}
            recs.append({"t": t, "P": {"g1": 5.0}, "k": k, "sell": 0.0,
                         "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []})
        return {"schedule_result": recs}

    ok = Level2Validator(task_set=tasks, schedule=sched(1, 2), settings=settings,
                         relaxation={}, horizon=2, precedence=[["pA_0", "pB_0"]])
    r_ok = {r.item: r for r in ok.check_item3_relaxations()}["R10"]
    assert r_ok.status == "PASS", r_ok.violations

    bad = Level2Validator(task_set=tasks, schedule=sched(2, 1), settings=settings,
                          relaxation={}, horizon=2, precedence=[["pA_0", "pB_0"]])
    r_bad = {r.item: r for r in bad.check_item3_relaxations()}["R10"]
    assert r_bad.status == "FAIL"
