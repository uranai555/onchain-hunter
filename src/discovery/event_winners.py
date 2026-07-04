"""MVP 1: Event winners mining.

Detect significant price events (BTC/ETH/SOL) and score existing candidate
wallets on their positioning around those events.

Principles from the 0→1 strategy:
  - event-first: find the event, then find who profited
  - leaderboard-independent: uses REST API only
  - source_surface != validation_score: event participation is one signal

API sources (no API key required):
  - CoinGecko free API for price data
  - Hyperliquid info endpoint for user fills
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import pandas as pd
import requests

from src.collectors.hyperliquid import fetch_user_fills

COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}

EVENT_THRESHOLDS = {
    "sharp_drop": -5.0,   # % drop in 24h
    "sharp_rise": 5.0,    # % rise in 24h
    "major_drop": -10.0,  # % drop in 48h
    "major_rise": 10.0,   # % rise in 48h
}

EVENT_WINDOW_BEFORE_H = 6   # lookback before event
EVENT_WINDOW_AFTER_H  = 12  # lookahead after event
EVENT_MERGE_WINDOW_H  = 6   # merge events of same symbol+type within this window
HYPERLIQUID_RATE_LIMIT_DELAY = 3.5  # seconds between API calls (Hyperliquid is strict)
API_RETRIES = 3
COINGECKO_DELAY = 1.0  # seconds between CoinGecko API calls (free tier: 1 req/s)

# In-memory cache for CoinGecko price data (avoid re-fetching same coin+days within a run)
_coingecko_cache: dict[str, pd.DataFrame] = {}


def _fetch_price_history(
    coin_id: str,
    days: int = 7,
) -> pd.DataFrame:
    """Fetch hourly OHLC price data from CoinGecko with retry and in-memory cache.

    Returns DataFrame with columns: timestamp, price.
    CoinGecko free tier returns hourly data for ≤90 days.
    """
    cache_key = f"{coin_id}_{days}"
    if cache_key in _coingecko_cache:
        return _coingecko_cache[cache_key].copy()

    url = (
        f"https://api.coingecko.com/api/v3/coins/{coin_id}"
        f"/market_chart?vs_currency=usd&days={days}"
    )
    last_exc: Exception | None = None
    for attempt in range(API_RETRIES):
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            prices = data.get("prices", [])
            if not prices:
                return pd.DataFrame(columns=["timestamp", "price"])

            df = pd.DataFrame(prices, columns=["timestamp_ms", "price"])
            df["timestamp"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
            df = df.drop(columns=["timestamp_ms"]).sort_values("timestamp").reset_index(drop=True)
            _coingecko_cache[cache_key] = df.copy()
            return df
        except (requests.RequestException, ValueError, KeyError) as exc:
            last_exc = exc
            status = getattr(exc, "response", None)
            status_code = getattr(status, "status_code", 0) if hasattr(exc, "response") and status else 0
            if status_code == 429 and attempt < API_RETRIES - 1:
                wait = COINGECKO_DELAY * (attempt + 2)
                print(f"[coingecko] 429 on {coin_id}, retrying in {wait:.0f}s ...")
                time.sleep(wait)
            elif status_code in (502, 503, 504) and attempt < API_RETRIES - 1:
                wait = COINGECKO_DELAY * (attempt + 2)
                print(f"[coingecko] {status_code} on {coin_id}, retrying in {wait:.0f}s ...")
                time.sleep(wait)
            elif attempt < API_RETRIES - 1:
                time.sleep(COINGECKO_DELAY)
    raise RuntimeError(f"Failed to fetch CoinGecko data for {coin_id} after {API_RETRIES} attempts") from last_exc


def _merge_events(events_df: pd.DataFrame) -> pd.DataFrame:
    """Merge consecutive events of the same symbol+type within EVENT_MERGE_WINDOW_H.

    Keeps the one with the largest absolute price change as the cluster representative.
    """
    if events_df.empty:
        return events_df

    df = events_df.copy().sort_values("event_time").reset_index(drop=True)
    df["event_time_dt"] = pd.to_datetime(df["event_time"])
    df["time_since_prev"] = df.groupby(["symbol", "event_type"])["event_time_dt"].diff().dt.total_seconds() / 3600

    # Start a new cluster when gap > merge window or first event of group
    df["new_cluster"] = df["time_since_prev"].isna() | (df["time_since_prev"] > EVENT_MERGE_WINDOW_H)
    df["cluster_id"] = df["new_cluster"].cumsum()

    # Pick the event with max |price_change_pct| as the cluster representative
    df["abs_change"] = df["price_change_pct"].abs()
    merged = (
        df.loc[df.groupby(["symbol", "event_type", "cluster_id"])["abs_change"].idxmax()]
        .drop(columns=["event_time_dt", "time_since_prev", "new_cluster", "cluster_id", "abs_change"])
        .reset_index(drop=True)
    )
    return merged


def detect_events(
    symbol: str = "BTC",
    days: int = 7,
    thresholds: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Detect significant price events for a given symbol.

    Returns DataFrame with deduplicated columns:
      event_type, event_time, symbol, price_before, price_after,
      price_change_pct, description
    """
    thresholds = thresholds or EVENT_THRESHOLDS
    coin_id = COINGECKO_IDS.get(symbol.upper())
    if not coin_id:
        raise ValueError(f"Unsupported symbol: {symbol}. Supported: {list(COINGECKO_IDS.keys())}")

    df = _fetch_price_history(coin_id, days=days)
    if df.empty:
        return pd.DataFrame(columns=[
            "event_type", "event_time", "symbol", "price_before",
            "price_after", "price_change_pct", "description",
        ])

    events: list[dict[str, Any]] = []

    # 24-hour windows (hourly data, so 24 rows = 24h) — vectorized
    window_24h = 24
    if len(df) > window_24h:
        df["price_24h_ago"] = df["price"].shift(window_24h)
        df["change_24h_pct"] = (df["price"] - df["price_24h_ago"]) / df["price_24h_ago"] * 100

        valid_24 = df.dropna(subset=["change_24h_pct"])
        drops_24 = valid_24[valid_24["change_24h_pct"] <= thresholds["sharp_drop"]]
        rises_24 = valid_24[valid_24["change_24h_pct"] >= thresholds["sharp_rise"]]

        for _, row in drops_24.iterrows():
            change = row["change_24h_pct"]
            events.append({
                "event_type": "sharp_drop",
                "event_time": row["timestamp"].isoformat(),
                "symbol": symbol.upper(),
                "price_before": float(row["price_24h_ago"]),
                "price_after": float(row["price"]),
                "price_change_pct": round(change, 2),
                "description": (
                    f"{symbol} {change:+.2f}% in 24h "
                    f"(${float(row['price_24h_ago']):,.0f} → ${float(row['price']):,.0f})"
                ),
            })
        for _, row in rises_24.iterrows():
            change = row["change_24h_pct"]
            events.append({
                "event_type": "sharp_rise",
                "event_time": row["timestamp"].isoformat(),
                "symbol": symbol.upper(),
                "price_before": float(row["price_24h_ago"]),
                "price_after": float(row["price"]),
                "price_change_pct": round(change, 2),
                "description": (
                    f"{symbol} {change:+.2f}% in 24h "
                    f"(${float(row['price_24h_ago']):,.0f} → ${float(row['price']):,.0f})"
                ),
            })

    # 48-hour windows for major events — vectorized
    window_48h = 48
    if len(df) > window_48h:
        df["price_48h_ago"] = df["price"].shift(window_48h)
        df["change_48h_pct"] = (df["price"] - df["price_48h_ago"]) / df["price_48h_ago"] * 100

        valid_48 = df.dropna(subset=["change_48h_pct"])
        drops_48 = valid_48[valid_48["change_48h_pct"] <= thresholds["major_drop"]]
        rises_48 = valid_48[valid_48["change_48h_pct"] >= thresholds["major_rise"]]

        for _, row in drops_48.iterrows():
            change = row["change_48h_pct"]
            events.append({
                "event_type": "major_drop",
                "event_time": row["timestamp"].isoformat(),
                "symbol": symbol.upper(),
                "price_before": float(row["price_48h_ago"]),
                "price_after": float(row["price"]),
                "price_change_pct": round(change, 2),
                "description": (
                    f"{symbol} {change:+.2f}% in 48h "
                    f"(${float(row['price_48h_ago']):,.0f} → ${float(row['price']):,.0f})"
                ),
            })
        for _, row in rises_48.iterrows():
            change = row["change_48h_pct"]
            events.append({
                "event_type": "major_rise",
                "event_time": row["timestamp"].isoformat(),
                "symbol": symbol.upper(),
                "price_before": float(row["price_48h_ago"]),
                "price_after": float(row["price"]),
                "price_change_pct": round(change, 2),
                "description": (
                    f"{symbol} {change:+.2f}% in 48h "
                    f"(${float(row['price_48h_ago']):,.0f} → ${float(row['price']):,.0f})"
                ),
            })

    # Deduplicate raw events by (event_type, event_time, symbol)
    events_df = pd.DataFrame(events)
    if events_df.empty:
        return events_df

    events_df = events_df.drop_duplicates(subset=["event_type", "event_time", "symbol"]).sort_values("event_time").reset_index(drop=True)

    # Merge consecutive events of same type/symbol within the merge window
    events_df = _merge_events(events_df)

    # Dedup: if same (symbol, event_time) has both sharp_* and major_*, keep major_*
    # (major events use a wider window and subsume sharp events at the same candle)
    if not events_df.empty and len(events_df) > 1:
        for direction_types in [("sharp_drop", "major_drop"), ("sharp_rise", "major_rise")]:
            sharp_t, major_t = direction_types
            mask_sharp = events_df["event_type"] == sharp_t
            mask_major = events_df["event_type"] == major_t
            if not mask_sharp.any() or not mask_major.any():
                continue
            # Find sharp events that share symbol+event_time with a major event
            dup_mask = events_df.duplicated(subset=["symbol", "event_time"], keep=False)
            dup_df: pd.DataFrame = events_df[dup_mask]
            sharp_dup: pd.DataFrame = dup_df[dup_df["event_type"] == sharp_t]
            if not sharp_dup.empty:
                events_df = events_df.drop(sharp_dup.index).reset_index(drop=True)

    return events_df


# ── Per-wallet event scoring (accepts pre-fetched fills) ────────────

def score_wallet_for_event(
    fills: list[dict[str, Any]],
    event_time: pd.Timestamp,
    event_direction: str,  # "rise" or "drop"
) -> dict[str, float]:
    """Score a single wallet's performance around a price event.

    Args:
        fills: Pre-fetched user fills for this wallet (list of dicts with 'datetime' or 'time' key).
        event_time: When the event occurred.
        event_direction: 'rise' or 'drop'.

    Returns:
      pre_positioning_score: 0-100 — was the wallet positioned before the event?
      execution_score: 0-100       — did they get good entry prices?
      exit_quality: 0-100           — did they take profits?
      estimated_pnl: float          — net PnL in event window
      trade_count_in_window: int    — trades during event window
    """
    window_start = event_time - timedelta(hours=EVENT_WINDOW_BEFORE_H)
    window_end   = event_time + timedelta(hours=EVENT_WINDOW_AFTER_H)

    # Normalise fill timestamps once
    normalised: list[dict[str, Any]] = []
    for fill in fills:
        raw_ts = fill.get("datetime") or fill.get("time")
        if raw_ts is None:
            continue
        if isinstance(raw_ts, (int, float)):
            dt = pd.Timestamp(raw_ts, unit="ms", tz="UTC")
        else:
            dt = pd.Timestamp(raw_ts, tz="UTC")
        if window_start <= dt <= window_end:
            f = dict(fill)
            f["_dt"] = dt
            normalised.append(f)

    trade_count = len(normalised)
    if trade_count == 0:
        return {
            "pre_positioning_score": 0.0,
            "execution_score": 0.0,
            "exit_quality": 0.0,
            "estimated_pnl": 0.0,
            "trade_count_in_window": 0,
        }

    pnl_values = [float(f.get("closedPnl", 0) or 0) for f in normalised]
    pnl_series = pd.Series(pnl_values)

    # Pre-positioning: trades before the event
    pre_fills = [f for f in normalised if f["_dt"] < event_time]
    pre_pnl = sum(float(f.get("closedPnl", 0) or 0) for f in pre_fills)

    buy_count = sum(1 for f in pre_fills if str(f.get("side", "")).lower() == "buy")
    sell_count = sum(1 for f in pre_fills if str(f.get("side", "")).lower() == "sell")

    if event_direction == "rise":
        positioning_ok = pre_pnl > 0 if abs(pre_pnl) > 0 else (buy_count > sell_count)
    else:
        positioning_ok = pre_pnl > 0 if abs(pre_pnl) > 0 else (sell_count > buy_count)

    if trade_count >= 5:
        pre_positioning_score = 80.0 if positioning_ok else (40.0 if pre_pnl > 0 else 10.0)
    elif trade_count >= 2:
        pre_positioning_score = 60.0 if positioning_ok else (30.0 if pre_pnl > 0 else 5.0)
    else:
        pre_positioning_score = 30.0 if positioning_ok else 0.0

    # Execution quality
    profitable_fills = int((pnl_series > 0).sum())
    win_rate = profitable_fills / max(trade_count, 1) * 100
    execution_score = min(win_rate * 1.2, 100.0)

    # Exit quality
    total_pnl = float(pnl_series.sum())
    peak_pnl = float(pnl_series.cumsum().max())
    exit_quality = max(0.0, min(100.0, (total_pnl / peak_pnl) * 100)) if peak_pnl > 0 else (50.0 if total_pnl > 0 else 10.0)

    return {
        "pre_positioning_score": round(pre_positioning_score, 1),
        "execution_score": round(execution_score, 1),
        "exit_quality": round(exit_quality, 1),
        "estimated_pnl": round(total_pnl, 2),
        "trade_count_in_window": trade_count,
    }


# ── Orchestration ───────────────────────────────────────────────────

def run_event_discovery(
    wallet_addresses: list[str],
    symbols: list[str] | None = None,
    days: int = 7,
    thresholds: dict[str, float] | None = None,
    prefetched_fills: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Run full event detection + winner scoring pipeline.

    Fetches userFills ONCE per wallet, then scores against all events.
    This avoids 429 rate limiting from per-event×per-wallet API calls.

    If *prefetched_fills* is provided (wallet_address -> list of fill dicts),
    those are used instead of calling the Hyperliquid API, eliminating
    duplicate API calls when the pipeline already fetched fills in an
    earlier phase.

    Returns a dict with:
      events_found: int
      events: list of event dicts (deduplicated)
      winners: dict mapping event_key -> list of wallet results
      errors: list of error messages
    """
    symbols = symbols or ["BTC", "ETH", "SOL"]
    all_events: list[dict[str, Any]] = []
    errors: list[str] = []

    # Phase 1: Detect events for each symbol
    print("[discovery:events] Fetching price data and detecting events ...")
    for symbol in symbols:
        try:
            events_df = detect_events(symbol, days=days, thresholds=thresholds)
            if not events_df.empty:
                for _, row in events_df.iterrows():
                    all_events.append(row.to_dict())
                print(f"[discovery:events] {symbol}: {len(events_df)} events detected")
            else:
                print(f"[discovery:events] {symbol}: no significant events")
            time.sleep(1.0)  # CoinGecko rate limit
        except Exception as exc:
            msg = f"Failed to detect events for {symbol}: {exc}"
            errors.append(msg)
            print(f"[discovery:events] {msg}")

    if not all_events:
        return {"events_found": 0, "events": [], "winners": {}, "errors": errors}

    # Phase 2: Use prefetched fills when available; otherwise fetch per wallet.
    wallet_fills: dict[str, list[dict[str, Any]]] = {}
    if prefetched_fills is not None:
        print(f"[discovery:events] Using prefetched fills for {len(wallet_addresses)} wallets (skipping API calls)")
        for address in wallet_addresses:
            wallet_fills[address] = prefetched_fills.get(address, [])
    else:
        print(f"[discovery:events] Fetching fills for {len(wallet_addresses)} wallets (one call each, {HYPERLIQUID_RATE_LIMIT_DELAY}s delay) ...")
        for address in wallet_addresses:
            for attempt in range(API_RETRIES):
                try:
                    fills = fetch_user_fills(address, lookback_days=days)
                    wallet_fills[address] = fills
                    print(f"[discovery:events]  {address[:14]}..: {len(fills)} fills")
                    break
                except Exception as exc:
                    status = getattr(exc, "response", None)
                    status_code = getattr(status, "status_code", 0) if status else 0
                    if status_code == 429 and attempt < API_RETRIES - 1:
                        wait = HYPERLIQUID_RATE_LIMIT_DELAY * (attempt + 2)
                        print(f"[discovery:events]  429 on {address[:14]}.., retrying in {wait:.0f}s ...")
                        time.sleep(wait)
                    else:
                        msg = f"Failed to fetch fills for {address[:14]}..: {exc}"
                        errors.append(msg)
                        print(f"[discovery:events]  {msg}")
                        wallet_fills[address] = []
                        break
            time.sleep(HYPERLIQUID_RATE_LIMIT_DELAY)

    # Phase 3: Score each wallet against every event using pre-fetched fills
    print(f"[discovery:events] Scoring {len(wallet_addresses)} wallets across {len(all_events)} deduplicated events ...")
    winners: dict[str, list[dict[str, Any]]] = {}

    for event in all_events:
        event_time_str = event["event_time"]
        event_time = pd.Timestamp(event_time_str, tz="UTC")
        event_type = event["event_type"]
        event_direction = "rise" if "rise" in event_type else "drop"
        event_key = f"{event_time_str[:19]}Z_{event['symbol']}_{event_type}"

        wallet_results: list[dict[str, Any]] = []
        for address in wallet_addresses:
            fills = wallet_fills.get(address, [])
            if not fills:
                continue
            score = score_wallet_for_event(fills, event_time, event_direction)
            if score["trade_count_in_window"] > 0:
                score["wallet_address"] = address
                wallet_results.append(score)

        if wallet_results:
            wallet_results.sort(key=lambda w: w["estimated_pnl"], reverse=True)
            winners[event_key] = wallet_results

    print(f"[discovery:events] {len(winners)} events with matched winners")
    return {
        "events_found": len(all_events),
        "events": all_events,
        "winners": winners,
        "errors": errors,
    }
