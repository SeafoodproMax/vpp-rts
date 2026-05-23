"""Result extraction and post-processing for the Virtual Power Plant scheduler."""

from typing import Any
import pulp

from src.rt_scheduler.formulator import VppMilpFormulator


class SchedulerResultExtractor:
    """Extracts and formats solved variable outcomes from the MILP model.

    This class handles rounding, result structuring, and computing power grid reserves.
    """

    def __init__(
        self, formulator: VppMilpFormulator, eps: float
    ) -> None:
        """Initializes the result extractor.

        Args:
            formulator: The formulated and solved VppMilpFormulator instance.
            eps: Epsilon threshold below which values are treated as zero.
        """
        self._formulator = formulator
        self._eps = eps

    def _clean(self, val: float | None) -> float:
        """Cleans and rounds floating point variables to 4 decimal places.

        Args:
            val: The raw floating point value from the solver.

        Returns:
            The rounded value, or 0.0 if the value is below the epsilon threshold.
        """
        if val is None:
            return 0.0
        return 0.0 if abs(val) < self._eps else round(val, 4)

    def extract_results(self) -> list[dict[str, Any]]:
        """Parses raw optimization decision variables into a formatted list.

        Returns:
            A list of dictionary records containing power generation, routing,
            battery SOC, and sales details for each time tick.
        """
        results: list[dict[str, Any]] = []

        time_steps = self._formulator.time_steps
        all_device_ids = self._formulator.all_device_ids
        all_jobs = self._formulator.all_jobs
        gen_ren_ids = self._formulator.gen_ren_ids
        sto_ids = self._formulator.sto_ids

        P = self._formulator.P
        k = self._formulator.k
        SOC = self._formulator.SOC
        Sell = self._formulator.Sell

        for t in time_steps:
            p_dict: dict[str, float] = {}
            for i in all_device_ids:
                v = self._clean(pulp.value(P[i][t]))
                if v > 0:
                    p_dict[i] = v

            k_dict: dict[str, dict[str, float]] = {}
            for job in all_jobs:
                allowed = gen_ren_ids if job.is_charging else all_device_ids
                job_alloc: dict[str, float] = {}
                for i in allowed:
                    if t in k[job.job_id].get(i, {}):
                        v = self._clean(pulp.value(k[job.job_id][i][t]))
                        if v > 0:
                            job_alloc[i] = v
                if job_alloc:
                    k_dict[job.job_id] = job_alloc

            soc_dict: dict[str, float] = {}
            for i in sto_ids:
                soc_dict[i] = self._clean(pulp.value(SOC[i][t]))

            sell_val = self._clean(pulp.value(Sell[t]))

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

    def compute_reserve(self) -> dict[int, float]:
        """Computes power grid reserve capacities per time tick.

        Returns:
            A mapping from hour tick to clean calculated reserve power.
        """
        reserve: dict[int, float] = {}

        time_steps = self._formulator.time_steps
        all_device_ids = self._formulator.all_device_ids
        regular_jobs = self._formulator.regular_jobs

        P = self._formulator.P
        k = self._formulator.k

        for t in time_steps:
            total_supply = sum(
                self._clean(pulp.value(P[i][t])) for i in all_device_ids
            )
            total_demand = sum(
                self._clean(pulp.value(k[job.job_id][i][t]))
                for job in regular_jobs
                for i in all_device_ids
                if t in k[job.job_id].get(i, {})
            )
            reserve[t] = self._clean(total_supply - total_demand)

        return reserve
