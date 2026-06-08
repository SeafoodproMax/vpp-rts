from pydantic import BaseModel


class BaseRTTask(BaseModel):
    """Base class for all real-time tasks with common attributes."""
    task_id: str  # 任務識別碼，例如 "p1", "s1", "a1"
    r: int        # release time：任務最早可以開始執行的時刻
    e: int        # execution time（WCET）：需要執行幾個 tick
    d: int        # relative deadline：從 release 起算，必須在 d 個 tick 內完成
    w: int        # energy demand：每個執行 tick 需要 w MWh 的電能
    preempt: int  # 1 = 可搶佔（可分散執行），0 = 非可搶佔（必須連續執行）


class PeriodicTask(BaseRTTask):
    """Represents a periodic hard deadline task."""
    p: int  # period：每隔 p 個 tick 重複釋放一次（PeriodicTask 特有）


class SporadicTask(BaseRTTask):
    """Represents a sporadic hard deadline task that requires acceptance test.

    與 PeriodicTask 不同：不週期重複，只出現一次。
    具有硬 deadline，必須通過 AcceptanceTester 才能被接受執行。
    """
    pass


class AperiodicTask(BaseRTTask):
    """Represents an aperiodic soft deadline task.

    不週期重複，只出現一次。
    具有軟 deadline：盡量在 deadline 前完成，但超過只記為 missed，不算違規。
    """
    pass
