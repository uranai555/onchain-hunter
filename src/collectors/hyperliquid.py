from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from src.utils.io import ensure_directory, load_candidate_wallets


INFO_ENDPOINT = "https://api.hyperliquid.xyz/info"
PAGE_LIMIT = 500


def _to_millis(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _request_user_fills(address: str, start_time: int) -> list[dict[str, Any]]:
    payload = {
        "type": "userFills",
        "user": address,
        "startTime": start_time,
    }
    response = requests.post(INFO_ENDPOINT, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected Hyperliquid response for {address}: {data!r}")
    return data


def fetch_user_fills(address: str, lookback_days: int = 90) -> list[dict[str, Any]]:
    """Fetch recent Hyperliquid fills for one wallet.

    Hyperliquid returns up to roughly 500 fills per call. Pagination advances by
    the largest fill timestamp seen so repeated timestamps do not loop forever.
    """
    if not address:
        return []

    start_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    start_time = _to_millis(start_dt)
    cutoff = start_time
    all_fills: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, ...]] = set()

    while True:
        page = _request_user_fills(address, start_time)
        if not page:
            break

        new_rows = 0
        max_time = start_time
        for fill in page:
            fill_time = int(fill.get("time", 0) or 0)
            if fill_time < cutoff:
                continue
            key = (
                fill.get("hash"),
                fill.get("tid"),
                fill.get("oid"),
                fill_time,
                fill.get("coin"),
                fill.get("px"),
                fill.get("sz"),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            row = dict(fill)
            row["wallet_address"] = address
            all_fills.append(row)
            new_rows += 1
            max_time = max(max_time, fill_time)

        if len(page) < PAGE_LIMIT or new_rows == 0 or max_time <= start_time:
            break

        start_time = max_time + 1
        time.sleep(0.1)

    return all_fills


def fetch_all_wallets(config: dict[str, Any]) -> pd.DataFrame:
    hyper_cfg = config.get("hyperliquid", {})
    lookback_days = int(hyper_cfg.get("lookback_days", config.get("run", {}).get("lookback_days", 90)))
    wallets_path = hyper_cfg.get("candidate_wallets_file", "data/candidate_hyperliquid_wallets.csv")
    output_path = Path(hyper_cfg.get("fills_output_file", "data/hyperliquid_fills.parquet"))

    addresses = load_candidate_wallets(wallets_path)
    rows: list[dict[str, Any]] = []
    for address in addresses:
        rows.extend(fetch_user_fills(address, lookback_days=lookback_days))

    df = pd.DataFrame(rows)
    if not df.empty:
        df["time"] = pd.to_numeric(df["time"], errors="coerce")
        df["datetime"] = pd.to_datetime(df["time"], unit="ms", utc=True, errors="coerce")
        for column in ("px", "sz", "fee", "closedPnl"):
            if column in df.columns:
                df[column] = pd.to_numeric(df[column], errors="coerce")

    ensure_directory(output_path.parent)
    df.to_parquet(output_path, index=False)
    return df
