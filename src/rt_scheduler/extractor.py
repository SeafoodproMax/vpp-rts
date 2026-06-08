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

        # 從 formulator 取出 solver 解完後的所有變數
        time_steps = self._formulator.time_steps
        all_device_ids = self._formulator.all_device_ids
        all_jobs = self._formulator.all_jobs
        gen_ren_ids = self._formulator.gen_ren_ids
        sto_ids = self._formulator.sto_ids

        P = self._formulator.P       # P[device][t]：裝置輸出（MWh）
        k = self._formulator.k       # k[job][device][t]：job 從哪個裝置取多少電
        SOC = self._formulator.SOC   # SOC[storage][t]：儲能的剩餘電量
        Sell = self._formulator.Sell # Sell[t]：賣給市場的電量（也是 Phase 3 的 reserve）

        for t in time_steps:
            # P：每個裝置在 t 時刻的實際輸出，只記非零值
            p_dict: dict[str, float] = {}
            for i in all_device_ids:
                v = self._clean(pulp.value(P[i][t]))
                if v > 0:
                    p_dict[i] = v

            # k：每個 job 在 t 時刻從哪些裝置取了多少電
            # 充電 job 只能從 gen/renewable 取電（不能從儲能放電後再充）
            # 普通 job 可以從所有裝置取電
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

            # SOC：每個儲能設備在 t 時刻的電量狀態
            soc_dict: dict[str, float] = {}
            for i in sto_ids:
                soc_dict[i] = self._clean(pulp.value(SOC[i][t]))

            sell_val = self._clean(pulp.value(Sell[t]))

            # missed_aperiodic / rejected_sporadic 欄位留空，
            # 等 Phase 3 AcceptanceTester 執行後填入
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
