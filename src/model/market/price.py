from dataclasses import dataclass
from typing import Any, Dict, List

from src.model.base.base_model import AppBaseModel


@dataclass
class PriceRecord:
    """Represents a single price entry for a specific hour."""
    hour: int
    market_price: int


@dataclass
class PriceSystem(AppBaseModel):
    """Aggregate root for market price configurations."""
    price: List[PriceRecord]

    @classmethod
    def _parse(cls, data: Dict[str, Any]) -> "PriceSystem":
        """Parses raw dictionary data into PriceSystem structure."""
        price_records = [
            PriceRecord(hour=item["hour"], market_price=item["market_price"])
            for item in data.get("price", [])
        ]
        return cls(price=price_records)
