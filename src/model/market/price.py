from typing import Dict, List

from pydantic import BaseModel, Field

from src.model.base.base_model import AppBaseModel


class PriceRecord(BaseModel):
    """Represents a single price entry for a specific hour."""
    hour: int           # 時刻（tick）：1 ~ 72，對應排程 horizon 的每個小時
    market_price: int   # 市場電價（$/MWh）：MILP 目標函數的 f3 使用此值計算售電收益


class PriceSystem(AppBaseModel):
    """Aggregate root for market price configurations.

    對應 input/price_72hr.json，儲存整個 72 小時排程期間的逐時市場電價。
    評估器（Evaluator）和 MILP（Formulator）都會用到：
    - Formulator：f3 = -Σ_t (price[t] × Sell[t])，最大化售電收益
    - Evaluator：market_revenue = Σ_t (price[t] × Sell_t)
    """
    price: List[PriceRecord]  # 每個 tick 的市場電價列表（共 72 筆）
