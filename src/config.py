"""Đọc và giữ cấu hình từ config.yaml."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


class Config:
    def __init__(self, data: dict[str, Any]):
        self._d = data

    def __getitem__(self, key: str) -> Any:
        return self._d[key]

    def get(self, path: str, default: Any = None) -> Any:
        """Lấy giá trị lồng nhau bằng đường dẫn 'a.b.c'."""
        cur: Any = self._d
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    @property
    def raw(self) -> dict[str, Any]:
        return self._d


def load_config(path: str | None = None) -> Config:
    """Đọc config.yaml (mặc định ở gốc dự án). Nếu chưa có, gợi ý copy từ example."""
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    if not cfg_path.exists():
        example = ROOT / "config.example.yaml"
        raise FileNotFoundError(
            f"Chưa có {cfg_path.name}. Hãy copy '{example.name}' thành 'config.yaml' rồi chỉnh."
        )
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"{cfg_path.name} không đúng định dạng (cần các mục dạng 'khoá: giá trị').")
    return Config(data)
