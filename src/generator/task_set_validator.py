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
        # 四個條件全部通過才算合格，任一失敗立即回傳 False（短路求值）
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
        """Validates that the workload density falls within the configured range.

        The lower bound satisfies rubric item 1-5 (density >= 0.7 by default).
        An optional upper bound can be set to leave reserve for sporadic /
        aperiodic acceptance tests — without it, the generator tends to produce
        densities of 0.7-0.9 even if the lower bound is relaxed.

        ┌─────────────────────────────────────────────────────────┐
        │  想調整密度範圍，改這兩個數字即可：                          │
        │                                                         │
        │  DENSITY_MIN = 0.7   ← 作業規格下限（1-5 項）             │
        │  DENSITY_MAX = None  ← None 表示不設上限                 │
        │                                                         │
        │  若想讓系統「比較閒」以觀察 sporadic/aperiodic 插入效果：    │
        │    DENSITY_MIN = 0.45                                   │
        │    DENSITY_MAX = 0.60  （約 50% 使用率）                  │
        └─────────────────────────────────────────────────────────┘
        """
        # ── 在這裡調整密度範圍 ──────────────────────────────────────
        DENSITY_MIN = 0.7   # 作業規格要求下限（不可低於此值）
        DENSITY_MAX = None  # 上限：None = 不限制；填數字 = 強制低密度
        # ────────────────────────────────────────────────────────────

        density = sum(t["e"] / t["p"] for t in tasks_dict.values())

        if density < DENSITY_MIN:
            return False
        if DENSITY_MAX is not None and density > DENSITY_MAX:
            return False
        return True

    def _validate_jobs_count(self, tasks_dict: Dict[str, dict]) -> bool:
        """Validates that the total job count within the horizon exceeds 30."""
        # 作業規格 1-3：72 小時內展開後的 job 總數必須 > 30
        # 展開規則：job 的 release = r + k*p，完成時間 = release + d，必須 ≤ horizon
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
        # 作業規格 1-8：至少 2 個任務的耗電量 w ≥ 14 MWh
        count = sum(1 for t in tasks_dict.values() if t["w"] >= 14)
        return count >= 2

    def _validate_frame_size(self, frame_size: Optional[int]) -> bool:
        """Validates that a valid frame size was found."""
        # frame_size 為 None 表示找不到任何合法的 f，這組任務集直接淘汰
        return frame_size is not None
