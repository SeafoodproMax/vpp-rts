"""Module for generating task sets."""

import math
import random
from typing import Dict, List, Optional, Set, Tuple

from src.generator.frame_size_calculator import FrameSizeCalculator
from src.generator.task_set_validator import TaskSetValidator


class TaskSetGenerator:
    """Generates sets of periodic tasks satisfying specific constraints."""

    def __init__(
        self,
        horizon: int,
        validator: Optional[TaskSetValidator] = None,
        seed: Optional[int] = None,
    ) -> None:
        """Initializes the TaskSetGenerator with a calculator and validator.

        Args:
            horizon: The planning horizon duration.
            validator: Task-set validator (defaults to a fresh ``TaskSetValidator``).
            seed: Optional RNG seed. When given, generation is fully deterministic,
                so the same task set is produced on every run (and across machines /
                Python versions). ``None`` draws a fresh random task set each run.
        """
        self._calculator = FrameSizeCalculator(horizon=horizon)
        self._validator = validator or TaskSetValidator(horizon=horizon)
        # Own RNG instance so seeding does not perturb global random state used
        # elsewhere (e.g. AdvancedScheduler's renewable realization).
        self._rng = random.Random(seed)

    def generate(self) -> Tuple[Dict[str, dict], int]:
        """Generates a valid dictionary of tasks and the corresponding frame size.

        Returns:
            A tuple containing the dictionary of periodic tasks and the frame size.
        """
        # 不斷重試，直到生成一組通過所有驗證的合法任務集
        while True:
            tasks_list, periods = self._generate_candidate()
            # 至少要有 3 種不同週期，避免任務集過於單調
            if len(periods) < 3:
                continue

            # 洗牌後重新編號（p1, p2, ...），避免特殊任務永遠排在最前面
            self._rng.shuffle(tasks_list)
            tasks_dict = {f"p{i+1}": t for i, t in enumerate(tasks_list)}

            # 計算這組任務集對應的最小合法 frame size
            frame_size = self._calculator.find_frame_size(tasks_dict)

            # 通過密度、job 數量、耗電量等驗證才回傳
            if self._validator.is_valid(tasks_dict, frame_size):
                assert frame_size is not None
                return tasks_dict, frame_size

    def _generate_candidate(self) -> Tuple[List[dict], Set[int]]:
        """Generates a candidate list of tasks and their periods."""
        # 隨機決定總任務數（作業規格：6 ≤ 任務數 ≤ 10）
        num_tasks = self._rng.randint(6, 10)
        # 作業規格要求至少 20% 的任務 deadline = 執行時間，無條件進位
        # 例：6 個任務 → ceil(1.2) = 2；10 個任務 → ceil(2.0) = 2
        num_d_eq_e = math.ceil(num_tasks * 0.2)

        tasks: List[dict] = []
        periods: Set[int] = set()

        # 第一批：先保證 d=e 的任務數量達標（規格 1-6）
        tasks.extend(self._generate_deadline_eq_exec_tasks(num_d_eq_e, periods))
        # 第二批：先保證至少 2 個 non-preemptive 高耗電任務（規格 1-7）
        tasks.extend(self._generate_non_preemptive_tasks(periods))

        # 第三批：補齊剩餘任務數量，參數限制較鬆
        remaining_count = num_tasks - len(tasks)
        tasks.extend(self._generate_remaining_tasks(remaining_count, periods))

        return tasks, periods

    def _generate_deadline_eq_exec_tasks(
        self, count: int, periods: Set[int]
    ) -> List[dict]:
        """Generates tasks where deadline equals execution time."""
        tasks = []
        for _ in range(count):
            # period 只從整除性較好的值中選，方便 frame size 計算
            p = self._rng.choice([8, 12, 16, 20, 24])
            tasks.append({
                "r": self._rng.randint(1, p),
                "p": p,
                "e": 4,
                "d": 4,          # d == e：deadline 剛好等於執行時間，最緊的情況
                "w": self._rng.randint(6, 18),
                "preempt": self._rng.choice([0, 1]),
            })
            periods.add(p)
        return tasks

    def _generate_non_preemptive_tasks(self, periods: Set[int]) -> List[dict]:
        """Generates two specific non-preemptive tasks to satisfy constraints."""
        tasks = []
        for _ in range(2):
            p = self._rng.randint(6, 24)
            # 用 f=4（最小可能 frame size）代入公式 2f - gcd(f,p) ≤ d
            # 反推 d 的下界：min_d = 2*4 - gcd(4,p) = 8 - gcd(4,p)
            # 同時保底 d ≥ e=2，確保 deadline 不會比執行時間還短
            min_d = max(2, 8 - math.gcd(4, p))
            d = self._rng.randint(min_d, p)
            tasks.append({
                "r": self._rng.randint(1, p),
                "p": p,
                "e": 2,          # e≠1 且 preempt=0，滿足規格 1-7 的非可搶佔條件
                "d": d,
                "w": self._rng.randint(14, 18),   # 高耗電（w ≥ 14），滿足規格 1-8
                "preempt": 0,    # 非可搶佔：執行時必須連續佔用時槽，不可中斷
            })
            periods.add(p)
        return tasks

    def _generate_remaining_tasks(
        self, count: int, periods: Set[int]
    ) -> List[dict]:
        """Generates the remaining required tasks with loose constraints."""
        tasks = []
        for _ in range(count):
            e = self._rng.randint(1, 3)
            p = self._rng.randint(6, 24)
            # 同樣用 f=4 反推 d 下界，並保底 d ≥ e（deadline 不得短於執行時間）
            min_d = max(e, 8 - math.gcd(4, p))
            d = self._rng.randint(min_d, p)
            tasks.append({
                "r": self._rng.randint(1, p),
                "p": p,
                "e": e,
                "d": d,
                "w": self._rng.randint(6, 18),
                "preempt": self._rng.choice([0, 1]),
            })
            periods.add(p)
        return tasks
