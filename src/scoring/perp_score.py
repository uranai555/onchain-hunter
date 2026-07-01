from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


def _clip_score(value: float) -> float:
    if pd.isna(value) or not np.isfinite(value):
        return 0.0
    return float(np.clip(value, 0.0, 100.0))


def _linear_score(value: float, low: float, high: float) -> float:
    if high == low:
        return 0.0
    return _clip_score((value - low) / (high - low) * 100.0)


def _profit_factor(pnl: pd.Series) -> float:
    gains = pnl[pnl > 0].sum()
    losses = abs(pnl[pnl < 0].sum())
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def _max_drawdown(pnl: pd.Series) -> float:
    equity = pnl.cumsum()
    peak = equity.cummax()
    drawdown = equity - peak
    return float(abs(drawdown.min())) if len(drawdown) else 0.0


def _safe_ratio(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _classify_style(group: pd.DataFrame, trade_count: int, avg_interval_minutes: float) -> str:
    coins = group.get("coin", pd.Series(dtype="object")).nunique()
    sides = group.get("side", pd.Series(dtype="object")).astype(str)
    side_balance = sides.value_counts(normalize=True).max() if not sides.empty else 1.0
    avg_notional = group.get("notional", pd.Series(dtype=float)).mean()

    if trade_count >= 150 and avg_interval_minutes <= 30:
        return "高レバスキャル型"
    if coins <= 2 and side_balance >= 0.85:
        return "片張りホールド型"
    if trade_count >= 100 and side_balance <= 0.60:
        return "マーケットメイクっぽい"
    if avg_interval_minutes <= 180 and avg_notional > group.get("notional", pd.Series(dtype=float)).median():
        return "ニュース反応型"
    if side_balance >= 0.70:
        return "トレンドフォロー型"
    return "逆張り型"


@dataclass
class WalletScores:
    wallet_address: str
    trade_count: int
    realized_pnl: float
    profit_factor: float
    max_drawdown: float
    realized_pnl_score: float
    risk_adjusted_return_score: float
    drawdown_control_score: float
    consistency_score: float
    liquidity_replicability_score: float
    style_clarity_score: float
    gaming_resistance_score: float
    crowding_penalty_inverse: float
    perp_score_v2: float
    rank: str
    trade_style: str
    excluded: bool
    exclusion_reasons: str
    max_trade_profit_share: float
    max_coin_profit_share: float
    pnl_30d: float
    pnl_90d: float
    pnl_180d: float
    unrealized_loss_to_profit: float
    lot_size_naturalness_score: float
    return_distribution_quality_score: float
    trade_interval_naturalness_score: float
    out_of_sample_survival_score: float
    pnl_concentration_inverse: float
    leverage_tail_risk_inverse: float


def _score_wallet(address: str, group: pd.DataFrame, now: pd.Timestamp, min_trades: int) -> WalletScores:
    group = group.copy()
    group["closedPnl"] = pd.to_numeric(group.get("closedPnl", 0), errors="coerce").fillna(0.0)
    group["px"] = pd.to_numeric(group.get("px", 0), errors="coerce").fillna(0.0)
    group["sz"] = pd.to_numeric(group.get("sz", 0), errors="coerce").fillna(0.0)
    group["notional"] = (group["px"].abs() * group["sz"].abs()).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    group["datetime"] = pd.to_datetime(group.get("datetime", pd.NaT), utc=True, errors="coerce")

    pnl = group["closedPnl"]
    trade_count = int(len(group))
    realized_pnl = float(pnl.sum())
    total_profit = float(pnl[pnl > 0].sum())
    pf = _profit_factor(pnl)
    max_dd = _max_drawdown(pnl)
    positive_days = group.loc[pnl > 0].groupby(group["datetime"].dt.date)["closedPnl"].sum()
    all_days = group.groupby(group["datetime"].dt.date)["closedPnl"].sum()

    max_trade_profit_share = _safe_ratio(float(pnl.max()), total_profit) if total_profit > 0 else 0.0
    coin_profit = group.groupby("coin")["closedPnl"].sum() if "coin" in group.columns else pd.Series(dtype=float)
    max_coin_profit_share = _safe_ratio(float(coin_profit.max()), total_profit) if total_profit > 0 and not coin_profit.empty else 0.0

    pnl_30d = float(group.loc[group["datetime"] >= now - pd.Timedelta(days=30), "closedPnl"].sum())
    pnl_90d = float(group.loc[group["datetime"] >= now - pd.Timedelta(days=90), "closedPnl"].sum())
    pnl_180d = float(group.loc[group["datetime"] >= now - pd.Timedelta(days=180), "closedPnl"].sum())
    if "unrealizedPnl" in group.columns:
        unrealized = pd.to_numeric(group["unrealizedPnl"], errors="coerce").fillna(0.0)
    else:
        unrealized = pd.Series(0.0, index=group.index)
    unrealized_loss = abs(float(unrealized.clip(upper=0).sum()))
    unrealized_loss_to_profit = _safe_ratio(unrealized_loss, total_profit)

    sorted_times = group["datetime"].dropna().sort_values()
    intervals = sorted_times.diff().dt.total_seconds().dropna() / 60
    avg_interval_minutes = float(intervals.mean()) if not intervals.empty else 0.0
    interval_cv = float(intervals.std() / intervals.mean()) if len(intervals) > 1 and intervals.mean() else 0.0

    realized_pnl_score = _linear_score(realized_pnl, 0, 50_000)
    risk_adjusted_return_score = _linear_score(_safe_ratio(realized_pnl, max_dd), 0, 5) if max_dd else (100.0 if realized_pnl > 0 else 0.0)
    drawdown_control_score = _clip_score(100 - _linear_score(max_dd, 0, max(abs(realized_pnl), 1)))
    consistency_score = _clip_score(_safe_ratio(len(positive_days), max(len(all_days), 1)) * 100)
    liquidity_replicability_score = _clip_score(100 - _linear_score(float(group["notional"].median()), 100_000, 2_000_000))
    style_clarity_score = _clip_score(max(group.get("side", pd.Series(dtype=str)).value_counts(normalize=True).max() if "side" in group else 0.5, max_coin_profit_share) * 100)
    crowding_penalty_inverse = _clip_score(100 - max_coin_profit_share * 100)

    lot_cv = float(group["sz"].std() / group["sz"].mean()) if group["sz"].mean() else 0.0
    lot_size_naturalness_score = _clip_score(100 - abs(lot_cv - 1.0) * 35)
    return_distribution_quality_score = _clip_score(100 - abs(float(pnl.skew() or 0)) * 15 - max_trade_profit_share * 35)
    trade_interval_naturalness_score = _clip_score(100 - abs(interval_cv - 1.0) * 30)
    first_half = group.sort_values("datetime").head(max(trade_count // 2, 1))["closedPnl"].sum()
    second_half = group.sort_values("datetime").tail(max(trade_count // 2, 1))["closedPnl"].sum()
    out_of_sample_survival_score = 100.0 if first_half > 0 and second_half > 0 else _linear_score(second_half, 0, max(abs(first_half), 1))
    pnl_concentration_inverse = _clip_score(100 - max_trade_profit_share * 100)
    leverage_tail_risk_inverse = _clip_score(100 - _linear_score(float(group["notional"].quantile(0.95)), 250_000, 5_000_000))

    gaming_resistance_score = _clip_score(
        0.25 * lot_size_naturalness_score
        + 0.20 * return_distribution_quality_score
        + 0.20 * trade_interval_naturalness_score
        + 0.15 * out_of_sample_survival_score
        + 0.10 * pnl_concentration_inverse
        + 0.10 * leverage_tail_risk_inverse
    )

    perp_score = _clip_score(
        0.20 * realized_pnl_score
        + 0.15 * risk_adjusted_return_score
        + 0.15 * drawdown_control_score
        + 0.15 * consistency_score
        + 0.10 * liquidity_replicability_score
        + 0.10 * style_clarity_score
        + 0.10 * gaming_resistance_score
        + 0.05 * crowding_penalty_inverse
    )

    reasons: list[str] = []
    if trade_count < min_trades:
        reasons.append("取引回数不足")
    if max_trade_profit_share >= 0.50:
        reasons.append("最大利益トレード依存")
    if max_coin_profit_share >= 0.60:
        reasons.append("単一銘柄利益依存")
    if pf < 1.2:
        reasons.append("プロフィットファクター不足")
    if pnl_30d > 0 and (pnl_90d < 0 or pnl_180d < 0):
        reasons.append("直近だけ好調")
    if realized_pnl > 0 and unrealized_loss_to_profit >= 0.50:
        reasons.append("未実現損失過大")

    if perp_score >= 80:
        rank = "A"
    elif perp_score >= 65:
        rank = "B"
    elif perp_score >= 50:
        rank = "C"
    else:
        rank = "D"

    return WalletScores(
        wallet_address=address,
        trade_count=trade_count,
        realized_pnl=realized_pnl,
        profit_factor=pf,
        max_drawdown=max_dd,
        realized_pnl_score=realized_pnl_score,
        risk_adjusted_return_score=risk_adjusted_return_score,
        drawdown_control_score=drawdown_control_score,
        consistency_score=consistency_score,
        liquidity_replicability_score=liquidity_replicability_score,
        style_clarity_score=style_clarity_score,
        gaming_resistance_score=gaming_resistance_score,
        crowding_penalty_inverse=crowding_penalty_inverse,
        perp_score_v2=perp_score,
        rank=rank,
        trade_style=_classify_style(group, trade_count, avg_interval_minutes),
        excluded=bool(reasons),
        exclusion_reasons="; ".join(reasons),
        max_trade_profit_share=max_trade_profit_share,
        max_coin_profit_share=max_coin_profit_share,
        pnl_30d=pnl_30d,
        pnl_90d=pnl_90d,
        pnl_180d=pnl_180d,
        unrealized_loss_to_profit=unrealized_loss_to_profit,
        lot_size_naturalness_score=lot_size_naturalness_score,
        return_distribution_quality_score=return_distribution_quality_score,
        trade_interval_naturalness_score=trade_interval_naturalness_score,
        out_of_sample_survival_score=out_of_sample_survival_score,
        pnl_concentration_inverse=pnl_concentration_inverse,
        leverage_tail_risk_inverse=leverage_tail_risk_inverse,
    )


def perp_score_v2(fills_df: pd.DataFrame, config: dict[str, Any] | None = None) -> pd.DataFrame:
    """Score Hyperliquid perp wallets and return one row per wallet."""
    config = config or {}
    min_trades = int(config.get("hyperliquid", {}).get("min_trades", 30))
    columns = [field.name for field in WalletScores.__dataclass_fields__.values()]
    if fills_df.empty or "wallet_address" not in fills_df.columns:
        return pd.DataFrame(columns=columns)

    now = pd.Timestamp.now(tz="UTC")
    scores = [
        _score_wallet(address, group, now=now, min_trades=min_trades)
        for address, group in fills_df.groupby("wallet_address", dropna=False)
    ]
    return pd.DataFrame([score.__dict__ for score in scores]).sort_values(
        ["excluded", "perp_score_v2"], ascending=[True, False]
    )
