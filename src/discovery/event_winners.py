"""Event-first wallet discovery.

Detect significant BTC/ETH/SOL price events, then score candidate wallets by
how well their fills align with the event direction.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import pandas as pd
import requests

from src.collectors.hyperliquid import fetch_user_fills
from src.utils.logger import get_logger
from src.utils.retry import retry_on_http_error
from src.utils.scoring import clip_score

COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}

EVENT_THRESHOLDS = {
    "sharp_drop": -5.0,
    "sharp_rise": 5.0,
    "major_drop": -10.0,
    "major_rise": 10.0,
}

EVENT_WINDOW_BEFORE_H = 6
EVENT_WINDOW_AFTER_H = 12
EVENT_MERGE_WINDOW_H = 6
HYPERLIQUID_RATE_LIMIT_DELAY = 3.5
API_RETRIES = 3
COINGECKO_DELAY = 1.0

logger = get_logger("discovery.events")
_SESSION = requests.Session()
_coingecko_cache: dict[str, pd.DataFrame] = {}


@retry_on_http_error(max_retries=API_RETRIES, base_delay=COINGECKO_DELAY)
def _request_price_history(coin_id: str, days: int) -> dict[str, Any]:
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days={days}"
    resp = _SESSION.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected CoinGecko response shape: {type(data).__name__}")
    return data


def _fetch_price_history(coin_id: str, days: int = 7) -> pd.DataFrame:
    """Fetch price history from CoinGecko with retry and in-memory cache."""
    cache_key = f"{coin_id}_{days}"
    if cache_key in _coingecko_cache:
        return _coingecko_cache[cache_key].copy()

    data = _request_price_history(coin_id, days)
    prices = data.get("prices", [])
    if not prices:
        return pd.DataFrame(columns=["timestamp", "price"])

    df = pd.DataFrame(prices, columns=["timestamp_ms", "price"])
    df["timestamp"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
    df = df.drop(columns=["timestamp_ms"]).sort_values("timestamp").reset_index(drop=True)
    _coingecko_cache[cache_key] = df.copy()
    return df


def _merge_events(events_df: pd.DataFrame) -> pd.DataFrame:
    """Merge consecutive events of the same symbol+type within a short window."""
    if events_df.empty:
        return events_df

    df = events_df.copy().sort_values("event_time").reset_index(drop=True)
    df["event_time_dt"] = pd.to_datetime(df["event_time"])
    df["time_since_prev"] = df.groupby(["symbol", "event_type"])["event_time_dt"].diff().dt.total_seconds() / 3600
    df["new_cluster"] = df["time_since_prev"].isna() | (df["time_since_prev"] > EVENT_MERGE_WINDOW_H)
    df["cluster_id"] = df["new_cluster"].cumsum()
    df["abs_change"] = df["price_change_pct"].abs()

    return (
        df.loc[df.groupby(["symbol", "event_type", "cluster_id"])["abs_change"].idxmax()]
        .drop(columns=["event_time_dt", "time_since_prev", "new_cluster", "cluster_id", "abs_change"])
        .reset_index(drop=True)
    )


def _event_rows_for_window(
    df: pd.DataFrame,
    symbol: str,
    hours: int,
    drop_type: str,
    rise_type: str,
    drop_threshold: float,
    rise_threshold: float,
) -> list[dict[str, Any]]:
    if len(df) <= hours:
        return []

    price_col = f"price_{hours}h_ago"
    change_col = f"change_{hours}h_pct"
    df = df.copy()
    df[price_col] = df["price"].shift(hours)
    df[change_col] = (df["price"] - df[price_col]) / df[price_col] * 100
    valid = df.dropna(subset=[change_col])

    rows: list[dict[str, Any]] = []
    for event_type, subset in (
        (drop_type, valid[valid[change_col] <= drop_threshold]),
        (rise_type, valid[valid[change_col] >= rise_threshold]),
    ):
        for _, row in subset.iterrows():
            change = float(row[change_col])
            rows.append(
                {
                    "event_type": event_type,
                    "event_time": row["timestamp"].isoformat(),
                    "symbol": symbol.upper(),
                    "price_before": float(row[price_col]),
                    "price_after": float(row["price"]),
                    "price_change_pct": round(change, 2),
                    "description": (
                        f"{symbol.upper()} {change:+.2f}% in {hours}h "
                        f"(${float(row[price_col]):,.0f} -> ${float(row['price']):,.0f})"
                    ),
                }
            )
    return rows


def detect_events(
    symbol: str = "BTC",
    days: int = 7,
    thresholds: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Detect significant price events for a given symbol."""
    thresholds = thresholds or EVENT_THRESHOLDS
    coin_id = COINGECKO_IDS.get(symbol.upper())
    if not coin_id:
        raise ValueError(f"Unsupported symbol: {symbol}. Supported: {list(COINGECKO_IDS.keys())}")

    df = _fetch_price_history(coin_id, days=days)
    columns = ["event_type", "event_time", "symbol", "price_before", "price_after", "price_change_pct", "description"]
    if df.empty:
        return pd.DataFrame(columns=columns)

    events = [
        *_event_rows_for_window(
            df,
            symbol,
            hours=24,
            drop_type="sharp_drop",
            rise_type="sharp_rise",
            drop_threshold=thresholds["sharp_drop"],
            rise_threshold=thresholds["sharp_rise"],
        ),
        *_event_rows_for_window(
            df,
            symbol,
            hours=48,
            drop_type="major_drop",
            rise_type="major_rise",
            drop_threshold=thresholds["major_drop"],
            rise_threshold=thresholds["major_rise"],
        ),
    ]
    events_df = pd.DataFrame(events, columns=columns)
    if events_df.empty:
        return events_df

    events_df = (
        events_df.drop_duplicates(subset=["event_type", "event_time", "symbol"])
        .sort_values("event_time")
        .reset_index(drop=True)
    )
    events_df = _merge_events(events_df)

    if not events_df.empty and len(events_df) > 1:
        for sharp_t, major_t in [("sharp_drop", "major_drop"), ("sharp_rise", "major_rise")]:
            dup_mask = events_df.duplicated(subset=["symbol", "event_time"], keep=False)
            dup_df = events_df[dup_mask]
            sharp_dup = dup_df[dup_df["event_type"] == sharp_t]
            if not sharp_dup.empty and (dup_df["event_type"] == major_t).any():
                events_df = events_df.drop(sharp_dup.index).reset_index(drop=True)

    return events_df


def _normalise_timestamp(fill: dict[str, Any]) -> pd.Timestamp | None:
    raw_ts = fill.get("datetime") or fill.get("time")
    if raw_ts is None:
        return None
    if isinstance(raw_ts, (int, float)):
        return pd.Timestamp(raw_ts, unit="ms", tz="UTC")
    ts = pd.Timestamp(raw_ts)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _fill_matches_symbol(fill: dict[str, Any], symbol: str | None) -> bool:
    if not symbol:
        return True
    coin = str(fill.get("coin", "")).upper()
    return coin == symbol.upper() or coin.startswith(f"{symbol.upper()}-")


def _signed_size_delta(fill: dict[str, Any]) -> float:
    size = abs(float(fill.get("sz", 0) or 0))
    direction = str(fill.get("dir", "")).lower()
    side = str(fill.get("side", "")).lower()

    if "open long" in direction or "close short" in direction:
        return size
    if "open short" in direction or "close long" in direction:
        return -size
    if side == "buy":
        return size
    if side == "sell":
        return -size
    return 0.0


def _normalise_event_fills(
    fills: list[dict[str, Any]],
    event_time: pd.Timestamp,
    symbol: str | None,
) -> list[dict[str, Any]]:
    window_start = event_time - timedelta(hours=EVENT_WINDOW_BEFORE_H)
    window_end = event_time + timedelta(hours=EVENT_WINDOW_AFTER_H)
    normalised: list[dict[str, Any]] = []

    for fill in fills:
        if not _fill_matches_symbol(fill, symbol):
            continue
        dt = _normalise_timestamp(fill)
        if dt is None or not (window_start <= dt <= window_end):
            continue
        row = dict(fill)
        row["_dt"] = dt
        row["_signed_size_delta"] = _signed_size_delta(fill)
        normalised.append(row)

    return normalised


def score_wallet_for_event(
    fills: list[dict[str, Any]],
    event_time: pd.Timestamp,
    event_direction: str,
    symbol: str | None = None,
) -> dict[str, float]:
    """Score a wallet's performance around a price event."""
    normalised = _normalise_event_fills(fills, event_time, symbol)
    trade_count = len(normalised)
    if trade_count == 0:
        return {
            "pre_positioning_score": 0.0,
            "execution_score": 0.0,
            "exit_quality": 0.0,
            "estimated_pnl": 0.0,
            "trade_count_in_window": 0,
            "position_delta_before": 0.0,
            "direction_alignment_score": 0.0,
            "event_winner_score": 0.0,
        }

    pnl_series = pd.Series([float(f.get("closedPnl", 0) or 0) for f in normalised])
    pre_fills = [f for f in normalised if f["_dt"] < event_time]
    pre_pnl = sum(float(f.get("closedPnl", 0) or 0) for f in pre_fills)
    pre_delta = sum(float(f.get("_signed_size_delta", 0) or 0) for f in pre_fills)
    pre_abs_delta = sum(abs(float(f.get("_signed_size_delta", 0) or 0)) for f in pre_fills)

    event_sign = 1.0 if event_direction == "rise" else -1.0
    if pre_abs_delta > 0:
        alignment_ratio = (pre_delta * event_sign) / pre_abs_delta
        direction_alignment_score = clip_score((alignment_ratio + 1.0) * 50)
    else:
        buy_count = sum(1 for f in pre_fills if str(f.get("side", "")).lower() == "buy")
        sell_count = sum(1 for f in pre_fills if str(f.get("side", "")).lower() == "sell")
        side_ok = buy_count > sell_count if event_direction == "rise" else sell_count > buy_count
        direction_alignment_score = 60.0 if side_ok else 20.0 if pre_fills else 0.0

    if trade_count >= 5:
        base_positioning_score = 80.0 if pre_pnl > 0 else 40.0 if pre_fills else 10.0
    elif trade_count >= 2:
        base_positioning_score = 60.0 if pre_pnl > 0 else 30.0 if pre_fills else 5.0
    else:
        base_positioning_score = 30.0 if pre_fills else 0.0
    pre_positioning_score = clip_score(base_positioning_score * 0.45 + direction_alignment_score * 0.55)

    profitable_fills = int((pnl_series > 0).sum())
    win_rate = profitable_fills / max(trade_count, 1) * 100
    execution_score = min(win_rate * 1.2, 100.0)

    total_pnl = float(pnl_series.sum())
    peak_pnl = float(pnl_series.cumsum().max())
    exit_quality = (
        max(0.0, min(100.0, (total_pnl / peak_pnl) * 100)) if peak_pnl > 0 else (50.0 if total_pnl > 0 else 10.0)
    )
    pnl_score = 100.0 if total_pnl > 0 else 35.0 if total_pnl == 0 else 0.0
    event_winner_score = clip_score(
        0.35 * pre_positioning_score + 0.25 * execution_score + 0.20 * exit_quality + 0.20 * pnl_score
    )

    return {
        "pre_positioning_score": round(pre_positioning_score, 1),
        "execution_score": round(execution_score, 1),
        "exit_quality": round(exit_quality, 1),
        "estimated_pnl": round(total_pnl, 2),
        "trade_count_in_window": trade_count,
        "position_delta_before": round(pre_delta, 6),
        "direction_alignment_score": round(direction_alignment_score, 1),
        "event_winner_score": round(event_winner_score, 1),
    }


def run_event_discovery(
    wallet_addresses: list[str],
    symbols: list[str] | None = None,
    days: int = 7,
    thresholds: dict[str, float] | None = None,
    prefetched_fills: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Run event detection and score candidate wallets against the events."""
    symbols = symbols or ["BTC", "ETH", "SOL"]
    all_events: list[dict[str, Any]] = []
    errors: list[str] = []

    logger.info("Fetching price data and detecting events ...")
    for symbol in symbols:
        try:
            events_df = detect_events(symbol, days=days, thresholds=thresholds)
            if not events_df.empty:
                all_events.extend(row.to_dict() for _, row in events_df.iterrows())
                logger.info("%s: %d events detected", symbol, len(events_df))
            else:
                logger.info("%s: no significant events", symbol)
            time.sleep(COINGECKO_DELAY)
        except Exception as exc:
            msg = f"Failed to detect events for {symbol}: {exc}"
            errors.append(msg)
            logger.warning(msg)

    if not all_events:
        return {"events_found": 0, "events": [], "winners": {}, "errors": errors}

    wallet_fills: dict[str, list[dict[str, Any]]] = {}
    if prefetched_fills is not None:
        logger.info("Using prefetched fills for %d wallets", len(wallet_addresses))
        for address in wallet_addresses:
            wallet_fills[address] = prefetched_fills.get(address, [])
    else:
        logger.info("Fetching fills for %d wallets", len(wallet_addresses))
        for address in wallet_addresses:
            try:
                wallet_fills[address] = fetch_user_fills(address, lookback_days=days)
                logger.info("%s..: %d fills", address[:14], len(wallet_fills[address]))
            except Exception as exc:
                msg = f"Failed to fetch fills for {address[:14]}..: {exc}"
                errors.append(msg)
                wallet_fills[address] = []
                logger.warning(msg)
            time.sleep(HYPERLIQUID_RATE_LIMIT_DELAY)

    logger.info("Scoring %d wallets across %d events", len(wallet_addresses), len(all_events))
    winners: dict[str, list[dict[str, Any]]] = {}
    for event in all_events:
        event_time_str = event["event_time"]
        event_time = pd.Timestamp(event_time_str, tz="UTC")
        event_type = event["event_type"]
        event_direction = "rise" if "rise" in event_type else "drop"
        event_symbol = str(event.get("symbol", ""))
        event_key = f"{event_time_str[:19]}Z_{event_symbol}_{event_type}"

        wallet_results: list[dict[str, Any]] = []
        for address in wallet_addresses:
            fills = wallet_fills.get(address, [])
            if not fills:
                continue
            score = score_wallet_for_event(fills, event_time, event_direction, symbol=event_symbol)
            if score["trade_count_in_window"] > 0:
                score["wallet_address"] = address
                wallet_results.append(score)

        if wallet_results:
            wallet_results.sort(key=lambda w: (w["event_winner_score"], w["estimated_pnl"]), reverse=True)
            winners[event_key] = wallet_results

    logger.info("%d events with matched winners", len(winners))
    return {
        "events_found": len(all_events),
        "events": all_events,
        "winners": winners,
        "errors": errors,
    }
