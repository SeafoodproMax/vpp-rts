"""Module for calculating valid frame sizes."""

import math
from typing import Dict, Optional


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
