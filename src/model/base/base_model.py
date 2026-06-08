import json
from typing import Any, Dict

from pydantic import BaseModel


class AppBaseModel(BaseModel):
    """
    Base model for all data structures in the VPP-RTS project.
    Provides generic JSON loading capability.
    """

    @classmethod
    def load_from_json(cls, filepath: str) -> "AppBaseModel":
        """Reads a JSON file and parses it into the model object.

        Template Method Pattern：
        - 骨架：讀檔 → 解析 → 回傳物件（固定在這裡）
        - 彈性：子類別覆寫 _parse() 來客製化從 dict 轉成物件的邏輯
        - 若子類別沒有 _parse，預設直接用 dict 解包（適合簡單結構）
        """
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 子類別有 _parse → 呼叫客製化解析（例如 TaskSystem、ProcessorSettingsSystem）
        # 子類別沒有 _parse → 直接用 **data 展開（例如 PriceSystem）
        if hasattr(cls, "_parse") and callable(getattr(cls, "_parse")):
            return cls._parse(data)
        return cls(**data)
