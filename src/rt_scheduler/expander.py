"""Job expansion logic for the real-time VPP scheduler."""

from src.model import ExpandedJob, ProcessorSettingsSystem, TaskSystem


class JobExpander:
    """Expands abstract periodic tasks and charging configs into concrete jobs.

    This class isolates the temporal expansion logic over a planning horizon.
    """

    def __init__(self, horizon: int = 72) -> None:
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
            k = 0
            while True:
                abs_release = task.r + k * task.p
                abs_deadline = abs_release + task.d - 1
                if abs_release > self._horizon or abs_deadline > self._horizon:
                    break
                expanded_jobs.append(
                    ExpandedJob(
                        job_id=f"{task.task_id}_{k}",
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
                    is_charging=True,
                    target_storage=cj.target_storage,
                )
            )

        return charging_jobs
