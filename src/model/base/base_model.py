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
        """Reads a JSON file and parses it into the model object."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        # If the subclass has a custom _parse method, use it; otherwise unpack directly
        if hasattr(cls, "_parse") and callable(getattr(cls, "_parse")):
            return cls._parse(data)
        return cls(**data)
