"""Module for handling file input and output operations."""

import json
import os
from typing import Any


class JsonIO:
    """Utility class for JSON file operations.

    靜態工具類別（Stateless utility）：所有方法都是 @staticmethod，
    不需要建立實例，也不持有任何狀態。
    使用方式：JsonIO.load(path)、JsonIO.save(data, path)
    """

    @staticmethod
    def load(filepath: str) -> Any:
        """Loads and returns parsed JSON data from a file.

        Args:
            filepath: The source file path.

        Returns:
            The parsed JSON object.
        """
        # UTF-8 編碼讀取，確保中文路徑與內容不亂碼
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def save(data: Any, filepath: str) -> None:
        """Saves data to a JSON file, creating parent directories if needed.

        Args:
            data: The data to serialize to JSON.
            filepath: The target file path.
        """
        # 自動建立不存在的目錄（例如 output/ 資料夾）
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            # indent=4：輸出 pretty-print JSON，方便人工閱讀
            json.dump(data, f, indent=4)
