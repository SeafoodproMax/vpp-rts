"""Module for handling file input and output operations."""

import json
import os
from typing import Any


class JsonIO:
    """Utility class for JSON file operations."""

    @staticmethod
    def save(data: Any, filepath: str) -> None:
        """Saves data to a JSON file, creating parent directories if needed.

        Args:
            data: The data to serialize to JSON.
            filepath: The target file path.
        """
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(data, f, indent=4)
