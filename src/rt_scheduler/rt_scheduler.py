"""Real-time Virtual Power Plant scheduling orchestrator."""

from typing import Any
import pulp

from src.model import ProcessorSettingsSystem, PriceSystem, TaskSystem
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
        processor_settings_path: str = "input/processor_settings.json",
        task_set_path: str = "output/task_set.json",
        price_path: str = "input/price_72hr.json",
        assets: ProcessorSettingsSystem | None = None,
        tasks: TaskSystem | None = None,
        prices: PriceSystem | None = None,
        horizon: int = 72,
    ) -> None:
        """Initializes the scheduler with paths or pre-loaded dependency models.

        Args:
            processor_settings_path: File path to the processor settings JSON.
            task_set_path: File path to the task set JSON.
            price_path: File path to the pricing forecast JSON.
            assets: Optional pre-loaded ProcessorSettingsSystem aggregate (DI).
            tasks: Optional pre-loaded TaskSystem aggregate (DI).
            prices: Optional pre-loaded PriceSystem aggregate (DI).
            horizon: The planning horizon duration (in ticks).
        """
        self._processor_settings_path = processor_settings_path
        self._task_set_path = task_set_path
        self._price_path = price_path
        self._horizon = horizon

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
            A dictionary containing 'schedule_result' and 'reserve' mapping records.
        """
        # 1. Load configuration and tasks
        self._load_data_if_needed()
        assert self._assets is not None
        assert self._tasks is not None
        assert self._prices is not None

        # 2. Expand task rules into concrete timeline jobs
        expander = JobExpander(horizon=self._horizon)
        regular_jobs = expander.expand_periodic_tasks(self._tasks)
        charging_jobs = expander.expand_charging_jobs(self._assets)
        all_jobs = regular_jobs + charging_jobs

        # 3. Formulate the MILP Optimization Problem
        formulator = VppMilpFormulator(
            assets=self._assets,
            prices=self._prices,
            all_jobs=all_jobs,
            horizon=self._horizon,
        )
        formulator.formulate()

        # 4. Invoke the Solver
        formulator.prob.solve(pulp.PULP_CBC_CMD(msg=1))
        status = pulp.LpStatus[formulator.prob.status]
        if status != "Optimal":
            raise RuntimeError(f"Solver did not find optimal solution: {status}")

        print(f"Objective value: {pulp.value(formulator.prob.objective):.2f}")

        # 5. Extract and format results
        extractor = SchedulerResultExtractor(formulator=formulator)
        result = extractor.extract_results()
        reserve = extractor.compute_reserve()

        return {"schedule_result": result, "reserve": reserve}
