from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from src.utils.io import ensure_directory

YIELDS_URL = "https://yields.llama.fi/pools"


def fetch_yields(max_retries: int = 3) -> list[dict[str, Any]]:
    """Fetch all yield pools from DefiLlama's yields endpoint.

    Returns raw pool dicts. Empty list on failure.
    """
    for attempt in range(max_retries):
        try:
            response = requests.get(YIELDS_URL, timeout=60)
            response.raise_for_status()
            data = response.json()
            pools = data.get("data", [])
            if not isinstance(pools, list):
                raise ValueError(f"Unexpected DefiLlama response shape: {type(pools).__name__}")
            return pools
        except requests.RequestException as exc:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            print(f"[defillama] Failed to fetch yields after {max_retries} retries: {exc}")
            return []


def _parse_numeric(value: object) -> float:
    try:
        v = float(value)
        return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _safe_age_days(pool: dict[str, Any]) -> int:
    """Estimate pool age from APY history length (rough proxy)."""
    history = pool.get("apyHistory", [])
    if isinstance(history, list) and len(history) >= 2:
        try:
            first = datetime.fromtimestamp(history[0], tz=timezone.utc)
            last = datetime.fromtimestamp(history[-1], tz=timezone.utc)
            return (last - first).days
        except (TypeError, ValueError):
            pass
    return 0


def fetch_filtered_pools(config: dict[str, Any]) -> pd.DataFrame:
    """Fetch pools from DefiLlama, apply basic filters, return DataFrame."""
    yield_cfg = config.get("yield", {})
    min_tvl = float(yield_cfg.get("min_tvl_usd", 5_000_000))
    min_apy = float(yield_cfg.get("min_apy", 5))
    max_apy = float(yield_cfg.get("max_apy", 40))
    allowed_chains = set(str(c).lower() for c in yield_cfg.get("chains", []))
    preferred_assets = set(str(a).lower() for a in yield_cfg.get("prefer_assets", []))

    raw = fetch_yields()
    if not raw:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for pool in raw:
        chain = str(pool.get("chain", "")).lower()
        tvl = _parse_numeric(pool.get("tvlUsd"))
        apy = _parse_numeric(pool.get("apy"))
        project = str(pool.get("project", "")).strip()
        symbol = str(pool.get("symbol", "")).strip()

        if allowed_chains and chain not in allowed_chains:
            continue
        if tvl < min_tvl:
            continue
        if apy < min_apy or apy > max_apy:
            continue

        apy_mean_30d = _parse_numeric(pool.get("apyMean30d"))
        apy_std = _parse_numeric(pool.get("apyStd"))
        il_risk = str(pool.get("ilRisk", "")).lower()
        exposure = str(pool.get("exposure", "")).lower()
        age_days = _safe_age_days(pool)
        pool_from = str(pool.get("pool", "")).strip()

        # APY stability — penalise high volatility
        apy_stability = 100.0
        if apy_mean_30d > 0 and apy_std > 0:
            cv = apy_std / apy_mean_30d
            if cv > 2.0:
                apy_stability = max(0.0, 100.0 - (cv - 2.0) * 20)

        is_preferred = any(asset in symbol.lower() for asset in preferred_assets)

        rows.append({
            "pool": pool_from,
            "project": project,
            "chain": chain,
            "symbol": symbol,
            "tvl_usd": tvl,
            "apy": apy,
            "apy_mean_30d": apy_mean_30d,
            "apy_std": apy_std,
            "apy_stability_score": apy_stability,
            "il_risk": il_risk,
            "exposure": exposure,
            "age_days": age_days,
            "is_preferred_asset": is_preferred,
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.sort_values(["is_preferred_asset", "apy"], ascending=[False, False])
    return df


def save_yields(df: pd.DataFrame, output_dir: str | Path = "data") -> str:
    """Save yield pool data to parquet and return the path."""
    path = Path(output_dir) / "defillama_yields.parquet"
    ensure_directory(path.parent)
    df.to_parquet(path, index=False)
    return str(path)