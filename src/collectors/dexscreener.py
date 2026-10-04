"""DexScreener collector — token discovery for DEX wallet hunting.

Two data sources:
1. /token-profiles/latest/v1 — latest token profiles created on Solana/Base
2. /latest/dex/search?q=... — search pairs by chain or token address

This is the first stage of Phase 3: find tokens that are pumping or newly
created, then score them and extract early-buyer wallets.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

DEXSCREENER_BASE = "https://api.dexscreener.com"
LATEST_PROFILES_URL = f"{DEXSCREENER_BASE}/token-profiles/latest/v1"
SEARCH_URL = f"{DEXSCREENER_BASE}/latest/dex/search"

# Supported chains for DEX hunting
SUPPORTED_CHAINS = {"solana", "base"}


def fetch_latest_profiles(
    max_retries: int = 3,
    chain_filter: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch latest token profiles from DexScreener.

    Returns token profiles (metadata: url, chainId, tokenAddress, links, etc.)
    Optionally filtered by chain.
    """
    for attempt in range(max_retries):
        try:
            resp = requests.get(LATEST_PROFILES_URL, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                raise ValueError(f"Unexpected response shape: {type(data).__name__}")
            if chain_filter:
                data = [p for p in data if p.get("chainId", "").lower() in chain_filter]
            return data
        except (requests.RequestException, ValueError) as exc:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            print(f"[dexscreener] Failed profiles: {exc}")
            return []
    return []


def search_pairs(
    query: str,
    max_retries: int = 3,
) -> list[dict[str, Any]]:
    """Search for trading pairs by token symbol, address, or chain.

    Returns list of pair dicts with price, liquidity, volume info.
    """
    for attempt in range(max_retries):
        try:
            resp = requests.get(
                f"{SEARCH_URL}",
                params={"q": query},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            pairs = data.get("pairs", [])
            if not isinstance(pairs, list):
                raise ValueError(f"Unexpected pairs shape: {type(pairs).__name__}")
            return pairs
        except (requests.RequestException, ValueError) as exc:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            print(f"[dexscreener] Search failed for '{query}': {exc}")
            return []
    return []


def fetch_trending_on_chain(
    chain: str = "solana",
    min_liquidity_usd: float = 1000.0,
    min_volume_usd: float = 500.0,
    max_age_hours: float = 72.0,
) -> pd.DataFrame:
    """Fetch trending tokens on a specific chain.

    Uses search to find pairs that are active on the given chain,
    then filters by liquidity, volume, and age.

    Returns a DataFrame with token-level metrics.
    """
    # Search for pairs on this chain — DexScreener search by chain name
    pairs = search_pairs(chain)
    if not pairs:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for pair in pairs:
        chain_id = str(pair.get("chainId", "")).lower()
        if chain_id != chain.lower():
            continue

        base_token = pair.get("baseToken", {}) or {}
        quote_token = pair.get("quoteToken", {}) or {}

        # Parse price/liquidity
        try:
            price_usd = float(pair.get("priceUsd", 0) or 0)
        except (TypeError, ValueError):
            price_usd = 0.0

        try:
            liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        except (TypeError, ValueError):
            liquidity_usd = 0.0

        try:
            volume_24h = float(pair.get("volume", {}).get("h24", 0) or 0)
        except (TypeError, ValueError):
            volume_24h = 0.0

        try:
            fdv = float(pair.get("fdv", 0) or 0)
        except (TypeError, ValueError):
            fdv = 0.0

        # Pair age — DexScreener provides creation timestamp
        pair_created_at = pair.get("pairCreationTimestamp")
        age_hours = None
        if pair_created_at:
            try:
                created = datetime.fromtimestamp(
                    int(pair_created_at) / 1000, tz=timezone.utc
                )
                age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600.0
            except (TypeError, ValueError, OSError):
                pass

        # Apply filters
        if liquidity_usd < min_liquidity_usd:
            continue
        if volume_24h < min_volume_usd:
            continue
        if age_hours is not None and age_hours > max_age_hours:
            continue

        rows.append({
            "chain": chain_id,
            "dex": str(pair.get("dexId", "")),
            "pair_address": str(pair.get("pairAddress", "")),
            "url": str(pair.get("url", "")),
            "base_token_address": str(base_token.get("address", "")),
            "base_token_name": str(base_token.get("name", "")),
            "base_token_symbol": str(base_token.get("symbol", "")),
            "quote_token_symbol": str(quote_token.get("symbol", "")),
            "price_usd": price_usd,
            "liquidity_usd": liquidity_usd,
            "volume_24h_usd": volume_24h,
            "fdv_usd": fdv,
            "age_hours": age_hours,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.sort_values("liquidity_usd", ascending=False)
    return df


def fetch_latest_token_profiles_df(
    chain_filter: set[str] | None = None,
) -> pd.DataFrame:
    """Fetch latest token profiles as a DataFrame."""
    profiles = fetch_latest_profiles(chain_filter=chain_filter)
    if not profiles:
        return pd.DataFrame()

    rows = []
    for p in profiles:
        links = p.get("links", [])
        socials = {lnk.get("label", "").lower(): lnk.get("url", "")
                   for lnk in links if isinstance(lnk, dict)}

        rows.append({
            "token_address": str(p.get("tokenAddress", "")),
            "chain": str(p.get("chainId", "")),
            "url": str(p.get("url", "")),
            "has_icon": bool(p.get("icon")),
            "has_header": bool(p.get("header")),
            "description": str(p.get("description", "")),
            "has_website": bool(socials.get("website")),
            "has_twitter": bool(socials.get("twitter")),
            "has_telegram": bool(socials.get("telegram")),
            "cto": bool(p.get("cto", False)),
            "detected_at": datetime.now(timezone.utc).isoformat(),
        })

    return pd.DataFrame(rows)


def save_tokens(df: pd.DataFrame, output_dir: str | Path = "data") -> str:
    """Save token discovery data to parquet."""
    path = Path(output_dir) / "dexscreener_tokens.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return str(path)