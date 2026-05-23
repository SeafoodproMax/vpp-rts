"""Module for validating generated task sets."""

from typing import Dict, Optional


class TaskSetValidator:
    """Validates the generated task sets against required constraints."""

    def __init__(self, horizon: int) -> None:
        """Initializes the validator.
        
        Args:
            horizon: The planning horizon duration.
        """
        self._horizon = horizon

    def is_valid(self, tasks_dict: Dict[str, dict], frame_size: Optional[int]) -> bool:
        """Validates all constraints for a given task set and frame size.

        Args:
            tasks_dict: The dictionary of generated tasks.
            frame_size: The frame size computed by the calculator.

        Returns:
            True if all validation checks pass, False otherwise.
        """
        if not self._validate_frame_size(frame_size):
            return False
        if not self._validate_density(tasks_dict):
            return False
        if not self._validate_jobs_count(tasks_dict):
            return False
        if not self._validate_w(tasks_dict):
            return False
        return True

    def _validate_density(self, tasks_dict: Dict[str, dict]) -> bool:
        """Validates that the workload density is at least 0.7."""
        density = sum(t["e"] / t["p"] for t in tasks_dict.values())
        return density >= 0.7

    def _validate_jobs_count(self, tasks_dict: Dict[str, dict]) -> bool:
        """Validates that the total job count within the horizon exceeds 30."""
        jobs_count = 0
        for t in tasks_dict.values():
            r = t["r"]
            p = t["p"]
            d = t["d"]
            k = 0
            while r + k * p + d <= self._horizon:
                jobs_count += 1
                k += 1
        return jobs_count > 30

    def _validate_w(self, tasks_dict: Dict[str, dict]) -> bool:
        """Validates that at least 2 tasks have an energy demand (w) >= 14."""
        count = sum(1 for t in tasks_dict.values() if t["w"] >= 14)
        return count >= 2

    def _validate_frame_size(self, frame_size: Optional[int]) -> bool:
        """Validates that a valid frame size was found."""
        return frame_size is not None
