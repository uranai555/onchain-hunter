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
RATE_LIMIT_DELAY = 3.5    # seconds between wallet API calls
MAX_RETRIES = 3


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
        time.sleep(0.2)  # delay between pagination calls

    return all_fills


def _fetch_with_retry(address: str, lookback_days: int) -> list[dict[str, Any]]:
    """Fetch fills for one wallet with retry on 429."""
    for attempt in range(MAX_RETRIES):
        try:
            return fetch_user_fills(address, lookback_days=lookback_days)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 429 and attempt < MAX_RETRIES - 1:
                wait = RATE_LIMIT_DELAY * (attempt + 2)
                print(f"[hyperliquid] 429 on {address[:14]}.., retrying in {wait:.0f}s ...")
                time.sleep(wait)
            else:
                raise
    return []  # unreachable but keeps type checker happy


def fetch_all_wallets(config: dict[str, Any]) -> pd.DataFrame:
    """Fetch fills for all candidate wallets with rate-limit resilience.

    Checks existing parquet cache first; only fetches wallets not in cache.
    After fetching, merges with existing cache and saves back.
    """
    hyper_cfg = config.get("hyperliquid", {})
    lookback_days = int(hyper_cfg.get("lookback_days", config.get("run", {}).get("lookback_days", 90)))
    wallets_path = hyper_cfg.get("candidate_wallets_file", "data/candidate_hyperliquid_wallets.csv")
    output_path = Path(hyper_cfg.get("fills_output_file", "data/hyperliquid_fills.parquet"))

    addresses = load_candidate_wallets(wallets_path)
    if not addresses:
        print("[hyperliquid] No candidate wallets found.")
        return pd.DataFrame()

    # Load existing cache
    existing_fills = pd.DataFrame()
    if output_path.exists():
        try:
            existing_fills = pd.read_parquet(output_path)
            print(f"[hyperliquid] Loaded {len(existing_fills)} existing fills from cache")
        except Exception:
            print("[hyperliquid] Could not read existing fills cache, starting fresh")

    # Determine which addresses are already in cache
    cached_addresses: set[str] = set()
    if not existing_fills.empty and "wallet_address" in existing_fills.columns:
        cached_addresses = set(existing_fills["wallet_address"].dropna().astype(str).str.strip().str.lower())

    new_rows: list[dict[str, Any]] = []
    for address in addresses:
        norm = address.strip().lower()
        if norm in cached_addresses:
            print(f"[hyperliquid] Skipping {address[:14]}.. (already in cache)")
            continue
        print(f"[hyperliquid] Fetching fills for {address[:14]}.. ...")
        try:
            fills = _fetch_with_retry(address, lookback_days=lookback_days)
            new_rows.extend(fills)
            print(f"[hyperliquid]  {address[:14]}..: {len(fills)} fills")
            time.sleep(RATE_LIMIT_DELAY)
        except Exception as exc:
            print(f"[hyperliquid]  Failed {address[:14]}..: {exc}")
            time.sleep(RATE_LIMIT_DELAY)

    # Merge new data with existing
    all_rows = []
    if not existing_fills.empty:
        all_rows.append(existing_fills)
    if new_rows:
        new_df = pd.DataFrame(new_rows)
        if not new_df.empty:
            all_rows.append(new_df)

    if not all_rows:
        print("[hyperliquid] No fills data at all.")
        return pd.DataFrame()

    df = pd.concat(all_rows, ignore_index=True)

    # Normalise columns
    if "time" in df.columns:
        df["time"] = pd.to_numeric(df["time"], errors="coerce")
    if "datetime" not in df.columns and "time" in df.columns:
        df["datetime"] = pd.to_datetime(df["time"], unit="ms", utc=True, errors="coerce")
    for column in ("px", "sz", "fee", "closedPnl"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    # Deduplicate: keep latest fill per (hash, tid, oid)
    dedup_cols = [c for c in ("hash", "tid", "oid") if c in df.columns]
    if dedup_cols and not df.empty:
        df = df.drop_duplicates(subset=dedup_cols + ["wallet_address"], keep="last").reset_index(drop=True)

    ensure_directory(output_path.parent)
    df.to_parquet(output_path, index=False)
    print(f"[hyperliquid] Saved {len(df)} fills ({df['wallet_address'].nunique()} wallets) -> {output_path}")
    return df