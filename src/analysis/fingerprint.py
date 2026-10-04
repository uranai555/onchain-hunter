"""Strategy fingerprint extraction for Hyperliquid fill histories."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import numpy as np
import pandas as pd

from src.utils.logger import get_logger

logger = get_logger("analysis.fingerprint")

FINGERPRINT_VERSION = 1
ENTRY_DIRS = {"Open Long", "Open Short", "Buy", "Long > Short", "Short > Long"}
EXIT_DIRS = {"Close Long", "Close Short"}
LONG_DIRS = {"Open Long", "Buy", "Short > Long"}
SHORT_DIRS = {"Open Short", "Sell", "Long > Short"}
STABLECOIN_MARKERS = ("USDC", "USDT", "DAI", "USDE", "USDS")


def extract_fingerprint(fills: pd.DataFrame, wallet: str) -> dict[str, Any]:
    """Extract a complete strategy fingerprint for one wallet."""
    clean = _prepare_fills(fills)
    return {
        "wallet": str(wallet),
        "fingerprint_version": FINGERPRINT_VERSION,
        "generated_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "time_patterns": extract_time_patterns(clean),
        "coin_profile": extract_coin_profile(clean),
        "position_sizing": extract_position_sizing(clean),
        "direction_bias": extract_direction_bias(clean),
        "entry_exit": extract_entry_exit(clean),
        "performance": extract_performance(clean),
        "risk_management": extract_risk_management(clean),
        "event_response": {
            "pre_event_positioning_score": 0,
            "event_reaction_delay_ms": 0,
            "event_win_rate": 0.0,
        },
        "bot_indicators": extract_bot_indicators(clean),
    }


def extract_time_patterns(fills: pd.DataFrame) -> dict[str, Any]:
    """Analyze session, hour, and weekday timing patterns."""
    df = _prepare_fills(fills)
    if df.empty:
        return {
            "session_bias": "balanced",
            "hour_entropy": 0.0,
            "peak_hours": [],
            "weekday_concentration": 0.0,
            "night_trading_ratio": 0.0,
        }

    entries = _entry_fills(df)
    if entries.empty:
        entries = df

    hours = entries["datetime"].dt.hour
    weekdays = entries["datetime"].dt.weekday
    hour_counts = hours.value_counts().reindex(range(24), fill_value=0).astype(float)
    weekday_counts = weekdays.value_counts().reindex(range(7), fill_value=0).astype(float)

    sessions = pd.cut(
        hours,
        bins=[-1, 7, 15, 23],
        labels=["asian", "european", "us"],
    )
    session_share = sessions.value_counts(normalize=True).reindex(["asian", "european", "us"], fill_value=0.0)
    top_session = str(session_share.idxmax())
    session_bias = top_session if float(session_share.max()) >= 0.45 else "balanced"

    peak = hour_counts[hour_counts > 0].sort_values(ascending=False).head(3).index.astype(int).tolist()
    return {
        "session_bias": session_bias,
        "hour_entropy": _normalized_entropy(hour_counts),
        "peak_hours": sorted(peak),
        "weekday_concentration": _concentration(weekday_counts),
        "night_trading_ratio": _safe_float(hours.between(0, 5).mean()),
    }


def extract_coin_profile(fills: pd.DataFrame) -> dict[str, Any]:
    """Analyze coin preference and rotation behavior."""
    df = _prepare_fills(fills)
    if df.empty:
        return {
            "primary_coins": [],
            "coin_count": 0,
            "top3_concentration": 0.0,
            "rotation_score": 0.0,
            "stablecoin_pairs_ratio": 0.0,
            "synthetic_assets_ratio": 0.0,
        }

    volume = _position_usd(df)
    by_coin = (
        df.assign(position_usd=volume)
        .groupby("coin", dropna=False)
        .agg(trade_count=("coin", "size"), pnl=("closedPnl", "sum"), volume=("position_usd", "sum"))
        .sort_values(["volume", "trade_count"], ascending=False)
    )
    total_volume = float(volume.sum())
    top_volume = float(by_coin["volume"].head(3).sum()) if not by_coin.empty else 0.0
    coins = df["coin"].fillna("").astype(str)
    ordered_coins = df.sort_values("datetime")["coin"].fillna("").astype(str)
    rotation = ordered_coins.ne(ordered_coins.shift()).iloc[1:].mean() if len(ordered_coins) > 1 else 0.0
    stable_mask = coins.str.contains("|".join(STABLECOIN_MARKERS), case=False, regex=True, na=False)
    synthetic_mask = coins.str.startswith("xyz:", na=False)

    return {
        "primary_coins": [str(coin) for coin in by_coin.head(3).index.tolist()],
        "coin_count": int(coins.nunique()),
        "top3_concentration": _safe_ratio(top_volume, total_volume),
        "rotation_score": _safe_float(rotation),
        "stablecoin_pairs_ratio": _weighted_ratio(volume, stable_mask),
        "synthetic_assets_ratio": _weighted_ratio(volume, synthetic_mask),
    }


def extract_position_sizing(fills: pd.DataFrame) -> dict[str, Any]:
    """Analyze position sizing and scaling behavior."""
    df = _prepare_fills(fills)
    position_usd = _position_usd(df)
    if df.empty or position_usd.empty:
        return {
            "sizing_style": "fixed",
            "avg_position_usd": 0.0,
            "position_std_pct": 0.0,
            "max_position_usd": 0.0,
            "scaling_in_ratio": 0.0,
            "scaling_out_ratio": 0.0,
            "risk_per_trade_pct": 0.0,
        }

    avg_position = float(position_usd.mean())
    std_position = float(position_usd.std(ddof=0))
    median_position = float(position_usd.median())
    cv = _safe_ratio(std_position, avg_position)
    max_to_median = _safe_ratio(float(position_usd.max()), median_position)
    if cv > 0.8 and max_to_median > 10:
        sizing_style = "martingale"
    elif _scaling_ratio(_entry_fills(df)) > 0.2:
        sizing_style = "scaling"
    elif cv < 0.2:
        sizing_style = "fixed"
    else:
        sizing_style = "variable"

    pnl = pd.to_numeric(df.get("closedPnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0).abs()
    return {
        "sizing_style": sizing_style,
        "avg_position_usd": _round(avg_position, 2),
        "position_std_pct": _round(cv, 4),
        "max_position_usd": _round(float(position_usd.max()), 2),
        "scaling_in_ratio": _scaling_ratio(_entry_fills(df)),
        "scaling_out_ratio": _scaling_ratio(_exit_fills(df)),
        "risk_per_trade_pct": _round(_safe_ratio(float(pnl.mean()), avg_position) * 100.0, 4),
    }


def extract_direction_bias(fills: pd.DataFrame) -> dict[str, Any]:
    """Analyze long/short directional preference."""
    df = _prepare_fills(fills)
    if df.empty:
        return {
            "net_direction": "neutral",
            "long_ratio": 0.0,
            "short_ratio": 0.0,
            "directional_consistency": 0.0,
        }

    dirs = df["dir"].fillna("").astype(str)
    long_mask = dirs.isin(LONG_DIRS)
    short_mask = dirs.isin(SHORT_DIRS)
    directional = df.loc[long_mask | short_mask].sort_values("datetime").copy()
    if directional.empty:
        return {
            "net_direction": "neutral",
            "long_ratio": 0.0,
            "short_ratio": 0.0,
            "directional_consistency": 0.0,
        }

    direction = pd.Series(np.where(directional["dir"].isin(LONG_DIRS), "long", "short"), index=directional.index)
    long_ratio = _safe_float((direction == "long").mean())
    short_ratio = _safe_float((direction == "short").mean())
    consistency = _safe_float(direction.eq(direction.shift()).iloc[1:].mean()) if len(direction) > 1 else 1.0
    if max(long_ratio, short_ratio) < 0.6:
        net_direction = "scalping" if consistency < 0.4 else "neutral"
    else:
        net_direction = "long_biased" if long_ratio > short_ratio else "short_biased"

    return {
        "net_direction": net_direction,
        "long_ratio": _round(long_ratio, 4),
        "short_ratio": _round(short_ratio, 4),
        "directional_consistency": _round(consistency, 4),
    }


def extract_entry_exit(fills: pd.DataFrame) -> dict[str, Any]:
    """Analyze order execution and estimated hold-time behavior."""
    df = _prepare_fills(fills)
    if df.empty:
        return {
            "market_order_ratio": 0.0,
            "entry_spread_cost_bps": 0.0,
            "avg_hold_ms": 0.0,
            "hold_std_ms": 0.0,
            "quick_trade_ratio": 0.0,
            "overnight_ratio": 0.0,
        }

    holds = _estimate_holds(df)
    hold_ms = holds["hold_ms"] if not holds.empty else pd.Series(dtype=float)
    entry_spread_cost_bps = _safe_float(pd.to_numeric(df.get("fee", 0.0), errors="coerce").fillna(0.0).sum())
    entry_notional = _position_usd(_entry_fills(df)).sum()
    entry_spread_cost_bps = _safe_ratio(entry_spread_cost_bps, float(entry_notional)) * 10000.0
    return {
        "market_order_ratio": _safe_float(df.get("crossed", pd.Series(dtype=bool)).fillna(False).astype(bool).mean()),
        "entry_spread_cost_bps": _round(entry_spread_cost_bps, 4),
        "avg_hold_ms": _round(float(hold_ms.mean()) if not hold_ms.empty else 0.0, 2),
        "hold_std_ms": _round(float(hold_ms.std(ddof=0)) if len(hold_ms) > 1 else 0.0, 2),
        "quick_trade_ratio": _safe_float((hold_ms < 60000).mean()) if not hold_ms.empty else 0.0,
        "overnight_ratio": _safe_float(holds["overnight"].mean()) if not holds.empty else 0.0,
    }


def extract_performance(fills: pd.DataFrame) -> dict[str, Any]:
    """Analyze realized PnL and basic performance characteristics."""
    df = _prepare_fills(fills)
    if df.empty:
        return {
            "total_pnl": 0.0,
            "total_trades": 0,
            "win_rate": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 0.0,
            "sharpe_approx": 0.0,
            "max_drawdown": 0.0,
            "best_coin": "",
            "worst_coin": "",
            "pnl_by_session": {"asian": 0.0, "european": 0.0, "us": 0.0},
        }

    pnl = pd.to_numeric(df["closedPnl"], errors="coerce").fillna(0.0)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_win = float(wins.sum())
    gross_loss = float(losses.abs().sum())
    ordered_pnl = pnl.loc[df.sort_values("datetime").index]
    equity = ordered_pnl.cumsum()
    drawdown = equity.cummax() - equity
    coin_pnl = df.assign(_pnl=pnl).groupby("coin")["_pnl"].sum()
    session = pd.cut(
        df["datetime"].dt.hour,
        bins=[-1, 7, 15, 23],
        labels=["asian", "european", "us"],
    )
    pnl_by_session = (
        df.assign(_pnl=pnl, _session=session)
        .groupby("_session", observed=False)["_pnl"]
        .sum()
        .reindex(["asian", "european", "us"], fill_value=0.0)
    )
    pnl_std = float(pnl.std(ddof=0))

    return {
        "total_pnl": _round(float(pnl.sum()), 6),
        "total_trades": int(len(df)),
        "win_rate": _safe_float((pnl > 0).mean()),
        "avg_win": _round(float(wins.mean()) if not wins.empty else 0.0, 6),
        "avg_loss": _round(float(losses.mean()) if not losses.empty else 0.0, 6),
        "profit_factor": _round(_safe_ratio(gross_win, gross_loss), 6),
        "sharpe_approx": _round(_safe_ratio(float(pnl.mean()), pnl_std) * math.sqrt(len(pnl)), 6),
        "max_drawdown": _round(float(drawdown.max()) if not drawdown.empty else 0.0, 6),
        "best_coin": str(coin_pnl.idxmax()) if not coin_pnl.empty else "",
        "worst_coin": str(coin_pnl.idxmin()) if not coin_pnl.empty else "",
        "pnl_by_session": {str(k): _round(float(v), 6) for k, v in pnl_by_session.items()},
    }


def extract_risk_management(fills: pd.DataFrame) -> dict[str, Any]:
    """Analyze loss, sizing/PnL correlation, and liquidation risk patterns."""
    df = _prepare_fills(fills)
    if df.empty:
        return {
            "stop_loss_style": "none",
            "estimated_stop_pct": 0.0,
            "max_adverse_excursion_pct": 0.0,
            "position_size_vs_pnl_corr": 0.0,
            "liquidation_count": 0,
        }

    pnl = pd.to_numeric(df["closedPnl"], errors="coerce").fillna(0.0)
    position_usd = _position_usd(df)
    avg_position = float(position_usd.mean()) if not position_usd.empty else 0.0
    loss_pct = pnl[pnl < 0].abs() / avg_position * 100.0 if avg_position > 0 else pd.Series(dtype=float)
    estimated_stop = float(loss_pct.quantile(0.95)) if not loss_pct.empty else 0.0
    max_loss_ratio = _safe_ratio(float(pnl.min() * -1.0) if not pnl.empty else 0.0, avg_position)
    if max_loss_ratio < 0.01:
        stop_loss_style = "tight"
    elif max_loss_ratio < 0.03:
        stop_loss_style = "moderate"
    elif max_loss_ratio < 0.05:
        stop_loss_style = "loose"
    else:
        stop_loss_style = "none"

    coin_px = df.groupby("coin")["px"]
    price_range = (coin_px.transform("max") - coin_px.transform("min")) / coin_px.transform("median").replace(0, np.nan) * 100.0
    price_range = price_range.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    corr = position_usd.corr(pnl) if len(position_usd) > 1 and position_usd.nunique() > 1 and pnl.nunique() > 1 else 0.0
    liquidation = df.get("liquidation", pd.Series(index=df.index, dtype=object))
    liquidation_count = int(liquidation.fillna("").astype(str).str.strip().ne("").sum())

    return {
        "stop_loss_style": stop_loss_style,
        "estimated_stop_pct": _round(estimated_stop, 4),
        "max_adverse_excursion_pct": _round(float(price_range.max()) if not price_range.empty else 0.0, 4),
        "position_size_vs_pnl_corr": _round(_safe_float(corr), 4),
        "liquidation_count": liquidation_count,
    }


def extract_bot_indicators(fills: pd.DataFrame) -> dict[str, Any]:
    """Estimate automation likelihood from timing and sizing regularity."""
    df = _prepare_fills(fills)
    if df.empty:
        return {
            "automation_likelihood": 0.0,
            "sub_second_trades_ratio": 0.0,
            "same_second_multiple_ratio": 0.0,
            "round_lot_bias": 0.0,
            "regular_interval_score": 0.0,
            "hour_synced_pattern": False,
        }

    ordered = df.sort_values("datetime")
    second = ordered["datetime"].dt.floor("s")
    same_second = second.duplicated(keep=False)
    same_second_coin = ordered.assign(_second=second).duplicated(["coin", "_second"], keep=False)
    deltas = ordered["datetime"].diff().dt.total_seconds().dropna()
    regular_interval_score = _regular_interval_score(deltas)
    minute_counts = ordered["datetime"].dt.minute.value_counts(normalize=True)
    hour_synced = bool((len(ordered) >= 10) and (not minute_counts.empty) and float(minute_counts.max()) >= 0.5)
    sz = pd.to_numeric(ordered["sz"], errors="coerce").fillna(0.0).abs()
    round_lot = _round_lot_mask(sz)
    automation = (
        _safe_float(same_second.mean()) * 0.25
        + _safe_float(same_second_coin.mean()) * 0.20
        + regular_interval_score * 0.25
        + _safe_float(round_lot.mean()) * 0.15
        + (0.15 if hour_synced else 0.0)
    )

    return {
        "automation_likelihood": _round(min(max(automation, 0.0), 1.0), 4),
        "sub_second_trades_ratio": _round(_safe_float(same_second.mean()), 4),
        "same_second_multiple_ratio": _round(_safe_float(same_second_coin.mean()), 4),
        "round_lot_bias": _round(_safe_float(round_lot.mean()), 4),
        "regular_interval_score": _round(regular_interval_score, 4),
        "hour_synced_pattern": hour_synced,
    }


def _prepare_fills(fills: pd.DataFrame) -> pd.DataFrame:
    if fills is None or fills.empty:
        return pd.DataFrame(columns=["coin", "px", "sz", "dir", "closedPnl", "datetime", "wallet_address"])
    df = fills.copy()
    if "wallet_address" in df.columns:
        wallet = df["wallet_address"].fillna("").astype(str).str.strip()
        df = df.loc[wallet.ne("")].copy()
    if df.empty:
        return df
    if "datetime" not in df.columns:
        df["datetime"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    else:
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    for column in ("px", "sz", "closedPnl", "fee"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)
    for column in ("coin", "dir"):
        if column not in df.columns:
            df[column] = ""
    return df.sort_values("datetime").reset_index(drop=True)


def _position_usd(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    return (pd.to_numeric(df.get("px", 0.0), errors="coerce").fillna(0.0).abs() * pd.to_numeric(df.get("sz", 0.0), errors="coerce").fillna(0.0).abs()).astype(float)


def _entry_fills(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "dir" not in df.columns:
        return df.iloc[0:0]
    return df.loc[df["dir"].fillna("").astype(str).isin(ENTRY_DIRS)]


def _exit_fills(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "dir" not in df.columns:
        return df.iloc[0:0]
    return df.loc[df["dir"].fillna("").astype(str).isin(EXIT_DIRS)]


def _estimate_holds(df: pd.DataFrame) -> pd.DataFrame:
    entries = _entry_fills(df)
    exits = _exit_fills(df)
    if entries.empty or exits.empty:
        if len(df) < 2:
            return pd.DataFrame(columns=["hold_ms", "overnight"])
        grouped = df.groupby("coin")["datetime"].agg(entry_time="min", exit_time="max")
    else:
        keys = ["coin"]
        if "oid" in df.columns and entries["oid"].notna().any() and exits["oid"].notna().any():
            keys = ["coin", "oid"]
        entry_times = entries.groupby(keys)["datetime"].min()
        exit_times = exits.groupby(keys)["datetime"].max()
        grouped = pd.concat([entry_times.rename("entry_time"), exit_times.rename("exit_time")], axis=1).dropna()
        if grouped.empty:
            grouped = pd.concat(
                [
                    entries.groupby("coin")["datetime"].min().rename("entry_time"),
                    exits.groupby("coin")["datetime"].max().rename("exit_time"),
                ],
                axis=1,
            ).dropna()
    grouped = grouped.loc[grouped["exit_time"] >= grouped["entry_time"]].copy()
    if grouped.empty:
        return pd.DataFrame(columns=["hold_ms", "overnight"])
    grouped["hold_ms"] = (grouped["exit_time"] - grouped["entry_time"]).dt.total_seconds() * 1000.0
    grouped["overnight"] = grouped["entry_time"].dt.normalize().ne(grouped["exit_time"].dt.normalize())
    return grouped[["hold_ms", "overnight"]]


def _scaling_ratio(df: pd.DataFrame) -> float:
    if df.empty or len(df) < 2:
        return 0.0
    ordered = df.sort_values(["coin", "datetime"])
    delta = ordered.groupby("coin")["datetime"].diff().dt.total_seconds()
    return _safe_float(delta.between(0, 300).mean())


def _regular_interval_score(deltas: pd.Series) -> float:
    if deltas.empty:
        return 0.0
    rounded = deltas.round().abs()
    canonical = np.array([1, 5, 10, 30, 60, 300, 600, 900, 1800, 3600], dtype=float)
    near_canonical = pd.Series((np.abs(rounded.to_numpy()[:, None] - canonical) <= 1).any(axis=1), index=rounded.index)
    concentration = rounded.value_counts(normalize=True).head(1).sum()
    return _round(min(1.0, float(near_canonical.mean()) * 0.6 + float(concentration) * 0.4), 4)


def _round_lot_mask(sz: pd.Series) -> pd.Series:
    rounded_0 = np.isclose(sz, sz.round(0), rtol=0.0, atol=1e-9)
    rounded_1 = np.isclose(sz * 10, (sz * 10).round(0), rtol=0.0, atol=1e-9)
    powers = np.array([1, 10, 100, 1000, 10000], dtype=float)
    power_lots = (np.abs(sz.to_numpy()[:, None] - powers) <= 1e-9).any(axis=1)
    return pd.Series(rounded_0 | rounded_1 | power_lots, index=sz.index)


def _normalized_entropy(counts: pd.Series) -> float:
    total = float(counts.sum())
    if total <= 0 or len(counts) <= 1:
        return 0.0
    probs = counts[counts > 0] / total
    entropy = float(-(probs * np.log(probs)).sum())
    return _round(entropy / math.log(len(counts)), 4)


def _concentration(counts: pd.Series) -> float:
    total = float(counts.sum())
    if total <= 0:
        return 0.0
    return _round(float(counts.max()) / total, 4)


def _weighted_ratio(weights: pd.Series, mask: pd.Series) -> float:
    return _round(_safe_ratio(float(weights.loc[mask.to_numpy()].sum()), float(weights.sum())), 4)


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0 or not np.isfinite(denominator):
        return 0.0
    value = numerator / denominator
    return _safe_float(value)


def _safe_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(result):
        return 0.0
    return result


def _round(value: float, digits: int = 4) -> float:
    return round(_safe_float(value), digits)
