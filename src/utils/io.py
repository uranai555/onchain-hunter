from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml


def ensure_directory(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def load_config(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return data


def load_candidate_wallets(path: str) -> list[str]:
    csv_path = Path(path)
    if not csv_path.exists():
        return []
    df = pd.read_csv(csv_path)
    if df.empty:
        return []
    for column in ("wallet_address", "address", "wallet"):
        if column in df.columns:
            return [str(value).strip() for value in df[column].dropna() if str(value).strip()]
    return [str(value).strip() for value in df.iloc[:, 0].dropna() if str(value).strip()]


def write_text(path: str | Path, content: str) -> None:
    output_path = Path(path)
    ensure_directory(output_path.parent)
    output_path.write_text(content, encoding="utf-8")
