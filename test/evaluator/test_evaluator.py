"""Tests for the Evaluator class."""

import json
import os
import tempfile

import pytest

from src.evaluator.evaluator import Evaluator


# ---------------------------------------------------------------------------
# Fixtures: minimal JSON content for each input file
# ---------------------------------------------------------------------------

PROCESSOR_SETTINGS = {
    "generator": [
        {
            "generator_id": "gen_1",
            "output_min": 10,
            "output_max": 50,
            "ramp_up_rate": 20,
            "ramp_down_rate": 20,
            "min_up_time": 1,
            "min_down_time": 1,
            "cost_fixed": 100,
            "cost_variable": 5,
            "initial_on_time": 0,
            "initial_off_time": 1,
            "initial_energy": 0,
        }
    ],
    "storage": [],
    "renewable_capacity": [],
    "renewable_forecast": [],
    "charging_jobs": [],
}

PRICE_DATA = {
    "price": [{"hour": t, "market_price": 50} for t in range(1, 73)]
}


def _make_task_set(periodic=None, sporadic=None, aperiodic=None) -> dict:
    return {
        "frame_size": 6,
        "periodic": periodic or {},
        "sporadic": sporadic or {},
        "aperiodic": aperiodic or {},
    }


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


def _make_evaluator(tmp: str, task_set: dict, schedule: list) -> Evaluator:
    """Helper that writes all required JSON files and returns an Evaluator."""
    proc = os.path.join(tmp, "processor_settings.json")
    tasks = os.path.join(tmp, "task_set.json")
    price = os.path.join(tmp, "price_72hr.json")
    sched = os.path.join(tmp, "schedule_result.json")

    _write_json(proc, PROCESSOR_SETTINGS)
    _write_json(tasks, task_set)
    _write_json(price, PRICE_DATA)
    _write_json(sched, {"schedule_result": schedule})

    return Evaluator(
        processor_settings_path=proc,
        task_set_path=tasks,
        price_path=price,
        schedule_result_path=sched,
        horizon=72,
    )


# ---------------------------------------------------------------------------
# Unit tests for private helpers
# ---------------------------------------------------------------------------


class TestComputeCompletionTimes:
    def _make_eval(self) -> Evaluator:
        return Evaluator("", "", "", "", 72)

    def test_returns_last_active_tick(self) -> None:
        schedule = [
            {"t": 1, "k": {"p1_0": {"gen_1": 10.0}}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []},
            {"t": 2, "k": {"p1_0": {"gen_1": 10.0}}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []},
            {"t": 3, "k": {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []},
        ]
        ev = self._make_eval()
        cts = ev._compute_completion_times(schedule)
        assert cts["p1_0"] == 2

    def test_ignores_zero_allocation(self) -> None:
        schedule = [
            {"t": 1, "k": {"p1_0": {"gen_1": 0.0}}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []},
            {"t": 2, "k": {"p1_0": {"gen_1": 5.0}}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []},
        ]
        ev = self._make_eval()
        cts = ev._compute_completion_times(schedule)
        assert cts["p1_0"] == 2

    def test_job_not_in_schedule_is_absent(self) -> None:
        schedule = [{"t": 1, "k": {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}]
        ev = self._make_eval()
        cts = ev._compute_completion_times(schedule)
        assert "p1_0" not in cts

    def test_multiple_jobs(self) -> None:
        schedule = [
            {"t": 1, "k": {"j1": {"gen_1": 10.0}, "j2": {"gen_1": 5.0}}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []},
            {"t": 2, "k": {"j1": {"gen_1": 10.0}}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []},
        ]
        ev = self._make_eval()
        cts = ev._compute_completion_times(schedule)
        assert cts["j1"] == 2
        assert cts["j2"] == 1


class TestCollectRejectedSporadic:
    def test_collects_across_time_steps(self) -> None:
        schedule = [
            {"t": 1, "k": {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": ["s1"]},
            {"t": 2, "k": {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": ["s2"]},
        ]
        ev = Evaluator("", "", "", "", 72)
        assert ev._collect_rejected_sporadic(schedule) == {"s1", "s2"}

    def test_empty_when_none_rejected(self) -> None:
        schedule = [{"t": 1, "k": {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}]
        ev = Evaluator("", "", "", "", 72)
        assert ev._collect_rejected_sporadic(schedule) == set()


# ---------------------------------------------------------------------------
# Integration tests for evaluate()
# ---------------------------------------------------------------------------


class TestEvaluateHardDeadlineMissRate:
    def test_all_periodic_on_time(self) -> None:
        # p=72 so only p1_0 is expanded: release=1, deadline=6
        task_set = _make_task_set(
            periodic={"p1": {"r": 1, "p": 72, "e": 1, "d": 6, "w": 10, "preempt": 1}}
        )
        # p1_0 completes at t=3 ≤ deadline 6 → no miss
        schedule = [
            {"t": t, "k": {"p1_0": {"gen_1": 10.0}} if t == 3 else {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(1, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["hard_deadline_miss_rate"] == 0.0

    def test_periodic_job_misses_deadline(self) -> None:
        # p1_0: deadline=6, but completes at t=8 → miss
        task_set = _make_task_set(
            periodic={"p1": {"r": 1, "p": 72, "e": 1, "d": 6, "w": 10, "preempt": 1}}
        )
        schedule = [
            {"t": t, "k": {"p1_0": {"gen_1": 10.0}} if t == 8 else {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(1, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["hard_deadline_miss_rate"] == 1.0

    def test_rejected_sporadic_counted_as_miss(self) -> None:
        task_set = _make_task_set(
            sporadic={"s1": {"r": 1, "e": 1, "d": 5, "w": 10, "preempt": 1}}
        )
        schedule = [
            {"t": 1, "k": {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": ["s1"]}
        ] + [
            {"t": t, "k": {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(2, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["hard_deadline_miss_rate"] == 1.0


class TestEvaluateSoftDeadlineMissRate:
    def test_aperiodic_completed_before_deadline(self) -> None:
        task_set = _make_task_set(
            aperiodic={"a1": {"r": 1, "e": 1, "d": 10, "w": 10, "preempt": 1}}
        )
        # a1 completes at t=5 ≤ deadline 10 → no miss
        schedule = [
            {"t": t, "k": {"a1": {"gen_1": 10.0}} if t == 5 else {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(1, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["soft_deadline_miss_rate"] == 0.0

    def test_aperiodic_not_scheduled_is_miss(self) -> None:
        task_set = _make_task_set(
            aperiodic={"a1": {"r": 1, "e": 1, "d": 10, "w": 10, "preempt": 1}}
        )
        schedule = [
            {"t": t, "k": {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(1, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["soft_deadline_miss_rate"] == 1.0


class TestEvaluateTardiness:
    def test_no_tardiness_when_on_time(self) -> None:
        # deadline = r + d - 1 = 1 + 6 - 1 = 6; completes at t=3 → tardiness=0
        task_set = _make_task_set(
            periodic={"p1": {"r": 1, "p": 72, "e": 1, "d": 6, "w": 10, "preempt": 1}}
        )
        schedule = [
            {"t": t, "k": {"p1_0": {"gen_1": 10.0}} if t == 3 else {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(1, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["average_tardiness"] == 0.0
        assert metrics["max_tardiness"] == 0.0

    def test_tardiness_computed_correctly(self) -> None:
        # deadline=6, completes at t=9 → tardiness = 9 - 6 = 3
        task_set = _make_task_set(
            periodic={"p1": {"r": 1, "p": 72, "e": 1, "d": 6, "w": 10, "preempt": 1}}
        )
        schedule = [
            {"t": t, "k": {"p1_0": {"gen_1": 10.0}} if t == 9 else {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(1, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["average_tardiness"] == 3.0
        assert metrics["max_tardiness"] == 3.0


class TestEvaluateResponseTime:
    def test_response_time_computed_correctly(self) -> None:
        # r=1, deadline=6, completes at t=4 → R = 4 - 1 = 3
        task_set = _make_task_set(
            periodic={"p1": {"r": 1, "p": 72, "e": 1, "d": 6, "w": 10, "preempt": 1}}
        )
        schedule = [
            {"t": t, "k": {"p1_0": {"gen_1": 10.0}} if t == 4 else {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(1, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["average_response_time"] == 3.0
        assert metrics["max_response_time"] == 3.0


class TestEvaluateCompletionTimeJitter:
    def test_jitter_is_max_minus_min_of_completion_times(self) -> None:
        # p1: r=1, p=6, e=1, d=6, w=10 → instances within horizon 72
        # p1_0 release=1,deadline=6; p1_1 release=7,deadline=12
        # p1_0 completes at t=2; p1_1 completes at t=9 → jitter = 9 - 2 = 7
        task_set = _make_task_set(
            periodic={"p1": {"r": 1, "p": 6, "e": 1, "d": 6, "w": 10, "preempt": 1}}
        )
        k_at = {}
        k_at[2] = "p1_0"
        k_at[9] = "p1_1"
        schedule = []
        for t in range(1, 13):
            job_id = k_at.get(t)
            k_entry = {job_id: {"gen_1": 10.0}} if job_id else {}
            schedule.append({"t": t, "k": k_entry, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []})
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["completion_time_jitter"] == 7.0

    def test_jitter_zero_when_only_one_instance(self) -> None:
        # p=72 so only p1_0 fits; jitter cannot be computed → 0.0
        task_set = _make_task_set(
            periodic={"p1": {"r": 1, "p": 72, "e": 1, "d": 6, "w": 10, "preempt": 1}}
        )
        schedule = [
            {"t": t, "k": {"p1_0": {"gen_1": 10.0}} if t == 3 else {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(1, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["completion_time_jitter"] == 0.0


class TestEvaluateCosts:
    def test_generator_cost_formula(self) -> None:
        # gen_1: cost_fixed=100, cost_variable=5; P_gen_1_t1=20 → cost = 100 + 5*20 = 200
        task_set = _make_task_set()
        schedule = [
            {"t": 1, "k": {}, "P": {"gen_1": 20.0}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
        ] + [
            {"t": t, "k": {}, "P": {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(2, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["generator_cost"] == 200.0

    def test_market_revenue_formula(self) -> None:
        # price=50 at all hours; sell=10 at t=1 → revenue = 50*10 = 500
        task_set = _make_task_set()
        schedule = [
            {"t": 1, "k": {}, "P": {}, "sell": 10.0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
        ] + [
            {"t": t, "k": {}, "P": {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(2, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["market_revenue"] == 500.0

    def test_objective_value_formula(self) -> None:
        # 0 aperiodic misses, gen_cost=200, revenue=500 → obj = 0 + 200 - 500 = -300
        task_set = _make_task_set()
        schedule = [
            {"t": 1, "k": {}, "P": {"gen_1": 20.0}, "sell": 10.0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
        ] + [
            {"t": t, "k": {}, "P": {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(2, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["objective_value"] == pytest.approx(-300.0)

    def test_objective_includes_aperiodic_penalty(self) -> None:
        # 1 aperiodic miss: penalty=10000; gen_cost=0; revenue=0 → obj = 10000
        task_set = _make_task_set(
            aperiodic={"a1": {"r": 1, "e": 1, "d": 5, "w": 10, "preempt": 1}}
        )
        # a1 not scheduled → deadline 5 missed → soft_miss_count = 1
        schedule = [
            {"t": t, "k": {}, "P": {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(1, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["objective_value"] == pytest.approx(10000.0)


class TestEvaluateSporadicValueRate:
    def test_full_rate_when_all_complete_before_deadline(self) -> None:
        # s1: e=2, deadline=r+d-1=1+5-1=5; completes at t=4 (both slots: t=3,t=4) → rate=1.0
        task_set = _make_task_set(
            sporadic={"s1": {"r": 1, "e": 2, "d": 5, "w": 10, "preempt": 1}}
        )
        schedule = [
            {"t": t, "k": {"s1": {"gen_1": 10.0}} if t in (3, 4) else {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(1, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["acceptance_test"]["sporadic_value_rate"] == 1.0

    def test_zero_rate_when_all_rejected(self) -> None:
        task_set = _make_task_set(
            sporadic={"s1": {"r": 1, "e": 2, "d": 5, "w": 10, "preempt": 1}}
        )
        schedule = [
            {"t": 1, "k": {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": ["s1"]}
        ] + [
            {"t": t, "k": {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(2, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["acceptance_test"]["sporadic_value_rate"] == 0.0

    def test_zero_rate_when_no_sporadic_jobs(self) -> None:
        task_set = _make_task_set()
        schedule = [
            {"t": t, "k": {}, "sell": 0, "soc": {}, "missed_aperiodic": [], "rejected_sporadic": []}
            for t in range(1, 73)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics = _make_evaluator(tmp, task_set, schedule).evaluate()
        assert metrics["acceptance_test"]["sporadic_value_rate"] == 0.0
