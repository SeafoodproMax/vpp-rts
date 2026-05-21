import json
from abc import ABC, abstractmethod
from typing import Any, Dict


class AppBaseModel(ABC):
    """
    Base model for all data structures in the VPP-RTS project.
    Provides generic JSON loading capability.
    """

    @classmethod
    def load_from_json(cls, filepath: str) -> "AppBaseModel":
        """Reads a JSON file and parses it into the model object."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls._parse(data)

    @classmethod
    @abstractmethod
    def _parse(cls, data: Dict[str, Any]) -> "AppBaseModel":
        """
        Abstract method to be implemented by child classes
        to parse the raw dictionary into an object instance.
        """
        pass
