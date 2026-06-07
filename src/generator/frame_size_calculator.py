"""Module for calculating valid frame sizes."""

import math
from typing import Dict, Optional


class FrameSizeCalculator:
    """Calculates valid frame sizes for a given set of periodic tasks."""

    def __init__(self, horizon: int) -> None:
        """Initializes the calculator.
        
        Args:
            horizon: The planning horizon duration.
        """
        self._horizon = horizon

    def find_frame_size(self, tasks_dict: Dict[str, dict]) -> Optional[int]:
        """Finds the minimum valid frame size for the given task set.

        The frame size (f) must satisfy:
            1. f >= max(e)
            2. horizon % f == 0
            3. 2f - gcd(f, period) <= deadline for all tasks

        Args:
            tasks_dict: A dictionary of tasks where values contain 'e', 'p', and 'd'.

        Returns:
            The minimum valid frame size integer, or None if no valid size exists.
        """
        if not tasks_dict:
            return None

        # 條件 1：frame 至少要能裝下最長的任務（f ≥ max(e)）
        max_e = max(t["e"] for t in tasks_dict.values())
        # 條件 2：horizon（72）必須能被 f 整除，從小到大列出所有候選值
        candidates = [i for i in range(1, self._horizon + 1) if self._horizon % i == 0 and i >= max_e]

        # 從最小的候選 f 開始試，第一個通過條件 3 的就是答案
        for f in candidates:
            if self._is_frame_size_valid(f, tasks_dict):
                return f
        return None

    def _is_frame_size_valid(self, f: int, tasks_dict: Dict[str, dict]) -> bool:
        """Checks if a specific frame size is valid for all tasks."""
        # 條件 3：對每個任務檢查 2f - gcd(f, p) ≤ d
        # 意義：最壞情況下，一個 job 在 frame 裡最遲開始的時間 + 執行時間 ≤ deadline
        for t in tasks_dict.values():
            if 2 * f - math.gcd(f, t["p"]) > t["d"]:
                return False
        return True
