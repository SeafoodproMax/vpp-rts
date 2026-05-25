"""Tests for the SchedulerResultExtractor class."""

import pulp
from src.model import ExpandedJob
from src.rt_scheduler.extractor import SchedulerResultExtractor


class FakeFormulator:
    """Fake formulator class to avoid running solver in unit tests."""

    def __init__(self) -> None:
        self.time_steps = [1, 2]
        self.all_device_ids = ["gen_1", "bat_1"]
        self.gen_ren_ids = ["gen_1"]
        self.sto_ids = ["bat_1"]
        self.all_jobs = [
            ExpandedJob(
                job_id="p1_0",
                source_task_id="p1",
                release=1,
                deadline=2,
                execution=1,
                demand=10,
                preemptive=True,
            )
        ]
        self.regular_jobs = self.all_jobs

        # Mock continuous variables using simple values
        self.P = {
            "gen_1": {1: pulp.LpVariable("P_gen_1_1"), 2: pulp.LpVariable("P_gen_1_2")},
            "bat_1": {1: pulp.LpVariable("P_bat_1_1"), 2: pulp.LpVariable("P_bat_1_2")},
        }

        self.k = {
            "p1_0": {
                "gen_1": {1: pulp.LpVariable("k_p1_0_gen_1_1"), 2: pulp.LpVariable("k_p1_0_gen_1_2")},
                "bat_1": {1: pulp.LpVariable("k_p1_0_bat_1_1"), 2: pulp.LpVariable("k_p1_0_bat_1_2")},
            }
        }

        self.SOC = {
            "bat_1": {1: pulp.LpVariable("SOC_bat_1_1"), 2: pulp.LpVariable("SOC_bat_1_2")}
        }

        self.Sell = {1: pulp.LpVariable("Sell_1"), 2: pulp.LpVariable("Sell_2")}

        # Assign fake values
        self.P["gen_1"][1].varValue = 15.123456
        self.P["gen_1"][2].varValue = 1e-8  # Below epsilon
        self.P["bat_1"][1].varValue = 5.0
        self.P["bat_1"][2].varValue = 0.0

        self.k["p1_0"]["gen_1"][1].varValue = 10.0
        self.k["p1_0"]["gen_1"][2].varValue = 0.0
        self.k["p1_0"]["bat_1"][1].varValue = 0.0
        self.k["p1_0"]["bat_1"][2].varValue = 0.0


def test_clean_rounding() -> None:
    """Tests epsilon cleaning and rounding behavior."""
    extractor = SchedulerResultExtractor(formulator=None, eps=1e-6)  # type: ignore

    # Zero values and None
    assert extractor._clean(0.0) == 0.0
    assert extractor._clean(None) == 0.0

    # Values below epsilon
    assert extractor._clean(5e-7) == 0.0
    assert extractor._clean(-2e-7) == 0.0

    # Normal values and rounding
    assert extractor._clean(15.123456) == 15.1235
    assert extractor._clean(5.00001) == 5.0
    assert extractor._clean(-10.87654) == -10.8765
