from typing import Dict, List

from pydantic import BaseModel, Field

from src.model.base.base_model import AppBaseModel


class PriceRecord(BaseModel):
    """Represents a single price entry for a specific hour."""
    hour: int
    market_price: int


class PriceSystem(AppBaseModel):
    """Aggregate root for market price configurations."""
    price: List[PriceRecord]
