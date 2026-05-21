"""Module for generating task sets and calculating valid frame sizes."""

import math
import random
from typing import Dict, List, Optional, Set, Tuple


class FrameSizeCalculator:
    """Calculates valid frame sizes for a given set of periodic tasks."""

    def find_frame_size(self, tasks_dict: Dict[str, dict]) -> Optional[int]:
        """Finds the minimum valid frame size for the given task set.

        The frame size (f) must satisfy:
            1. f >= max(e)
            2. 72 % f == 0
            3. 2f - gcd(f, period) <= deadline for all tasks

        Args:
            tasks_dict: A dictionary of tasks where values contain 'e', 'p', and 'd'.

        Returns:
            The minimum valid frame size integer, or None if no valid size exists.
        """
        if not tasks_dict:
            return None
        
        max_e = max(t["e"] for t in tasks_dict.values())
        candidates = [i for i in range(1, 73) if 72 % i == 0 and i >= max_e]
        
        for f in candidates:
            if self._is_frame_size_valid(f, tasks_dict):
                return f
        return None

    def _is_frame_size_valid(self, f: int, tasks_dict: Dict[str, dict]) -> bool:
        """Checks if a specific frame size is valid for all tasks."""
        for t in tasks_dict.values():
            if 2 * f - math.gcd(f, t["p"]) > t["d"]:
                return False
        return True


class TaskSetGenerator:
    """Generates sets of periodic tasks satisfying specific constraints."""

    def __init__(self) -> None:
        """Initializes the TaskSetGenerator with a frame size calculator."""
        self._calculator = FrameSizeCalculator()

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
            if frame_size is None:
                continue

            if not self._validate_density(tasks_dict):
                continue

            if not self._validate_jobs_count(tasks_dict):
                continue

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

    def _validate_density(self, tasks_dict: Dict[str, dict]) -> bool:
        """Validates that the workload density is at least 0.7."""
        density = sum(t["e"] / t["p"] for t in tasks_dict.values())
        return density >= 0.7

    def _validate_jobs_count(self, tasks_dict: Dict[str, dict]) -> bool:
        """Validates that the total job count within 72 units exceeds 30."""
        jobs_count = 0
        for t in tasks_dict.values():
            r = t["r"]
            p = t["p"]
            d = t["d"]
            k = 0
            while r + k * p + d <= 72:
                jobs_count += 1
                k += 1
        return jobs_count > 30
