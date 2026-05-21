"""Module for generating task sets."""

import math
import random
from typing import Dict, List, Optional, Set, Tuple

from src.generator.frame_size_calculator import FrameSizeCalculator
from src.generator.task_set_validator import TaskSetValidator


class TaskSetGenerator:
    """Generates sets of periodic tasks satisfying specific constraints."""

    def __init__(self, validator: Optional[TaskSetValidator] = None) -> None:
        """Initializes the TaskSetGenerator with a calculator and validator."""
        self._calculator = FrameSizeCalculator()
        self._validator = validator or TaskSetValidator()

    def generate(self) -> Tuple[Dict[str, dict], int]:
        """Generates a valid dictionary of tasks and the corresponding frame size.

        Returns:
            A tuple containing the dictionary of periodic tasks and the frame size.
        """
        while True:
            tasks_list, periods = self._generate_candidate()
            if len(periods) < 3:
                continue

            random.shuffle(tasks_list)
            tasks_dict = {f"p{i+1}": t for i, t in enumerate(tasks_list)}

            frame_size = self._calculator.find_frame_size(tasks_dict)

            if self._validator.is_valid(tasks_dict, frame_size):
                assert frame_size is not None
                return tasks_dict, frame_size

    def _generate_candidate(self) -> Tuple[List[dict], Set[int]]:
        """Generates a candidate list of tasks and their periods."""
        num_tasks = random.randint(6, 10)
        num_d_eq_e = math.ceil(num_tasks * 0.2)
        
        tasks: List[dict] = []
        periods: Set[int] = set()

        tasks.extend(self._generate_deadline_eq_exec_tasks(num_d_eq_e, periods))
        tasks.extend(self._generate_non_preemptive_tasks(periods))
        
        remaining_count = num_tasks - len(tasks)
        tasks.extend(self._generate_remaining_tasks(remaining_count, periods))

        return tasks, periods

    def _generate_deadline_eq_exec_tasks(
        self, count: int, periods: Set[int]
    ) -> List[dict]:
        """Generates tasks where deadline equals execution time."""
        tasks = []
        for _ in range(count):
            p = random.choice([8, 12, 16, 20, 24])
            tasks.append({
                "r": random.randint(1, p),
                "p": p,
                "e": 4,
                "d": 4,
                "w": random.randint(6, 18),
                "preempt": random.choice([0, 1]),
            })
            periods.add(p)
        return tasks

    def _generate_non_preemptive_tasks(self, periods: Set[int]) -> List[dict]:
        """Generates two specific non-preemptive tasks to satisfy constraints."""
        tasks = []
        for _ in range(2):
            p = random.randint(6, 24)
            min_d = max(2, 8 - math.gcd(4, p))
            d = random.randint(min_d, p)
            tasks.append({
                "r": random.randint(1, p),
                "p": p,
                "e": 2,
                "d": d,
                "w": random.randint(14, 18),
                "preempt": 0,
            })
            periods.add(p)
        return tasks

    def _generate_remaining_tasks(
        self, count: int, periods: Set[int]
    ) -> List[dict]:
        """Generates the remaining required tasks with loose constraints."""
        tasks = []
        for _ in range(count):
            e = random.randint(1, 3)
            p = random.randint(6, 24)
            min_d = max(e, 8 - math.gcd(4, p))
            d = random.randint(min_d, p)
            tasks.append({
                "r": random.randint(1, p),
                "p": p,
                "e": e,
                "d": d,
                "w": random.randint(6, 18),
                "preempt": random.choice([0, 1]),
            })
            periods.add(p)
        return tasks
