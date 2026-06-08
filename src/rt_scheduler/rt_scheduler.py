"""Real-time Virtual Power Plant scheduling orchestrator."""

from typing import Any
import pulp

from src.model import ProcessorSettingsSystem, PriceSystem, TaskSystem
from src.rt_scheduler.acceptance_tester import AcceptanceTester
from src.rt_scheduler.expander import JobExpander
from src.rt_scheduler.formulator import VppMilpFormulator
from src.rt_scheduler.extractor import SchedulerResultExtractor


class RTScheduler:
    """PuLP-based MILP day-ahead static scheduler for periodic real-time jobs.

    This class orchestrates loading data, expanding jobs, formulating constraints,
    solving the mathematical program, and extracting the optimal schedule results.
    """

    def __init__(
        self,
        processor_settings_path: str,
        task_set_path: str,
        price_path: str,
        horizon: int,
        epsilon: float,
        assets: ProcessorSettingsSystem | None = None,
        tasks: TaskSystem | None = None,
        prices: PriceSystem | None = None,
    ) -> None:
        """Initializes the scheduler with paths or pre-loaded dependency models.

        Args:
            processor_settings_path: File path to the processor settings JSON.
            task_set_path: File path to the task set JSON.
            price_path: File path to the pricing forecast JSON.
            horizon: The planning horizon duration (in ticks).
            epsilon: Epsilon threshold for result extractor.
            assets: Optional pre-loaded ProcessorSettingsSystem aggregate (DI).
            tasks: Optional pre-loaded TaskSystem aggregate (DI).
            prices: Optional pre-loaded PriceSystem aggregate (DI).
        """
        self._processor_settings_path = processor_settings_path
        self._task_set_path = task_set_path
        self._price_path = price_path
        self._horizon = horizon
        self._epsilon = epsilon

        # Dependency Injection support
        self._assets = assets
        self._tasks = tasks
        self._prices = prices

    def _load_data_if_needed(self) -> None:
        """Loads data from file system if models were not injected during init."""
        if self._assets is None:
            self._assets = ProcessorSettingsSystem.load_from_json(
                self._processor_settings_path
            )
        if self._tasks is None:
            self._tasks = TaskSystem.load_from_json(self._task_set_path)
        if self._prices is None:
            self._prices = PriceSystem.load_from_json(self._price_path)

    def run(self) -> dict[str, Any]:
        """Executes the full scheduling pipeline.

        Returns:
            A dictionary containing 'schedule_result', the per-tick 'reserve'
            mapping, and the acceptance-test 'log' of per-job decisions.
        """
        # Step 1：從 JSON 讀取電廠設備、任務集、電價資料
        self._load_data_if_needed()
        assert self._assets is not None
        assert self._tasks is not None
        assert self._prices is not None

        # Step 2：把抽象的週期任務展開成具體的 job 實例
        # 例如 p1（period=12）在 72 小時內會展開成 p1_0, p1_1, ... 等多個 job
        # 充電 job（儲能充電）也在此展開，橫跨整個 horizon
        expander = JobExpander(horizon=self._horizon)
        regular_jobs = expander.expand_periodic_tasks(self._tasks)
        charging_jobs = expander.expand_charging_jobs(self._assets)
        all_jobs = regular_jobs + charging_jobs

        # Step 3：建立 PuLP MILP 模型
        # formulate() 會依序建立決策變數、目標函數、23 條限制式
        formulator = VppMilpFormulator(
            assets=self._assets,
            prices=self._prices,
            all_jobs=all_jobs,
            horizon=self._horizon,
        )
        formulator.formulate()

        # Step 4：呼叫 CBC solver 求解
        # PULP_CBC_CMD 是 PuLP 內建的開源 MILP solver（CBC）
        # msg=1 → 印出 solver 的求解過程 log
        # 求解成功 → status = "Optimal"；否則拋出例外
        formulator.prob.solve(pulp.PULP_CBC_CMD(msg=1))
        status = pulp.LpStatus[formulator.prob.status]
        if status != "Optimal":
            raise RuntimeError(f"Solver did not find optimal solution: {status}")

        print(f"Objective value: {pulp.value(formulator.prob.objective):.2f}")

        # Step 5：把 solver 的原始變數值解析成人看得懂的 JSON 格式
        # 每個 tick 輸出 P（各裝置輸出）、k（能量分配）、sell、soc
        extractor = SchedulerResultExtractor(formulator=formulator, eps=self._epsilon)
        result = extractor.extract_results()

        # Step 6：Phase 3 — 在 MILP 解出的 reserve 上執行 acceptance test
        # AcceptanceTester 利用 sell（可重導向的剩餘電量）作為 reserve
        # 依序處理 sporadic（硬 deadline）和 aperiodic（軟 deadline）任務
        acceptance_tester = AcceptanceTester(schedule_result=result)
        return acceptance_tester.run(self._tasks)
