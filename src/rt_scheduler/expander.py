"""Job expansion logic for the real-time VPP scheduler."""

from src.model import ExpandedJob, ProcessorSettingsSystem, TaskSystem


class JobExpander:
    """Expands abstract periodic tasks and charging configs into concrete jobs.

    This class isolates the temporal expansion logic over a planning horizon.
    """

    def __init__(self, horizon: int) -> None:
        """Initializes the job expander.

        Args:
            horizon: The planning horizon duration (in ticks).
        """
        self._horizon = horizon

    def expand_periodic_tasks(self, tasks: TaskSystem) -> list[ExpandedJob]:
        """Expands periodic tasks into concrete timeline job instances.

        Args:
            tasks: The task system loaded from output/task_set.json.

        Returns:
            A list of expanded job instances within the scheduling horizon.
        """
        expanded_jobs: list[ExpandedJob] = []

        for task in tasks.periodic_tasks:
            k = 0  # 第幾個週期（第 0 次、第 1 次、...）
            while True:
                # 絕對釋放時間 = 初始 release time + 第 k 個週期的偏移
                abs_release = task.r + k * task.p
                # 絕對 deadline = 釋放時間 + 相對 deadline - 1
                # （-1 是因為 release 當拍本身也算在 deadline 視窗內）
                abs_deadline = abs_release + task.d - 1

                # 超出 horizon 就不再展開（只有 deadline ≤ 72 的 job 才納入 MILP）
                if abs_release > self._horizon or abs_deadline > self._horizon:
                    break

                expanded_jobs.append(
                    ExpandedJob(
                        job_id=f"{task.task_id}_{k}",   # 例如 "p1_0", "p1_1"
                        source_task_id=task.task_id,
                        release=abs_release,
                        deadline=abs_deadline,
                        execution=task.e,
                        demand=task.w,
                        preemptive=(task.preempt == 1),
                    )
                )
                k += 1

        return expanded_jobs

    def expand_charging_jobs(
        self, assets: ProcessorSettingsSystem
    ) -> list[ExpandedJob]:
        """Expands asset charging job configurations into timeline jobs.

        Args:
            assets: The processor settings loaded from input.

        Returns:
            A list of charging job instances spanning the entire horizon.
        """
        # 充電 job 是特殊的：它代表「把電充進儲能設備」的動作。
        # 與普通 job 不同，它：
        #   - 跨整個 horizon（release=1, deadline=H，可以任何拍充電）
        #   - 只能由發電機或再生能源供電（不能從儲能放電再充電）
        #   - demand=0（充多少由 MILP 自行決定，不是固定需求）
        charging_jobs: list[ExpandedJob] = []

        for cj in assets.charging_jobs:
            charging_jobs.append(
                ExpandedJob(
                    job_id=cj.job_id,
                    source_task_id=cj.job_id,
                    release=1,
                    deadline=self._horizon,
                    execution=self._horizon,
                    demand=0,
                    preemptive=True,
                    is_charging=True,          # 標記為充電 job，routing 限制不同
                    target_storage=cj.target_storage,  # 充進哪個儲能設備
                )
            )

        return charging_jobs
