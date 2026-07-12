from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.utils.config import PipelineConfig
from src.utils.scoring import clip_score as _clip_score
from src.utils.scoring import linear_score as _linear_score
from src.utils.scoring import safe_ratio as _safe_ratio

REALIZED_PNL_HIGH = 50_000
RISK_ADJUSTED_RETURN_HIGH = 5
NOTIONAL_MEDIAN_LOW = 100_000
NOTIONAL_MEDIAN_HIGH = 2_000_000
LOT_CV_TARGET = 1.0
LOT_CV_PENALTY_COEFFICIENT = 35
RETURN_SKEW_PENALTY_COEFFICIENT = 15
MAX_TRADE_PROFIT_PENALTY_COEFFICIENT = 35
INTERVAL_CV_TARGET = 1.0
INTERVAL_CV_PENALTY_COEFFICIENT = 30
NOTIONAL_P95_LOW = 250_000
NOTIONAL_P95_HIGH = 5_000_000
MAX_TRADE_PROFIT_SHARE_EXCLUSION = 0.50
MAX_COIN_PROFIT_SHARE_EXCLUSION = 0.60
MIN_PROFIT_FACTOR = 1.2
UNREALIZED_LOSS_TO_PROFIT_EXCLUSION = 0.50
RANK_A_MIN = 80
RANK_B_MIN = 65
RANK_C_MIN = 50


@dataclass(frozen=True)
class ScoreThresholds:
    realized_pnl_high: float = REALIZED_PNL_HIGH
    risk_adjusted_return_high: float = RISK_ADJUSTED_RETURN_HIGH
    notional_median_low: float = NOTIONAL_MEDIAN_LOW
    notional_median_high: float = NOTIONAL_MEDIAN_HIGH
    lot_cv_target: float = LOT_CV_TARGET
    lot_cv_penalty_coefficient: float = LOT_CV_PENALTY_COEFFICIENT
    return_skew_penalty_coefficient: float = RETURN_SKEW_PENALTY_COEFFICIENT
    max_trade_profit_penalty_coefficient: float = MAX_TRADE_PROFIT_PENALTY_COEFFICIENT
    interval_cv_target: float = INTERVAL_CV_TARGET
    interval_cv_penalty_coefficient: float = INTERVAL_CV_PENALTY_COEFFICIENT
    notional_p95_low: float = NOTIONAL_P95_LOW
    notional_p95_high: float = NOTIONAL_P95_HIGH
    max_trade_profit_share_exclusion: float = MAX_TRADE_PROFIT_SHARE_EXCLUSION
    max_coin_profit_share_exclusion: float = MAX_COIN_PROFIT_SHARE_EXCLUSION
    min_profit_factor: float = MIN_PROFIT_FACTOR
    unrealized_loss_to_profit_exclusion: float = UNREALIZED_LOSS_TO_PROFIT_EXCLUSION
    rank_a_min: float = RANK_A_MIN
    rank_b_min: float = RANK_B_MIN
    rank_c_min: float = RANK_C_MIN

    @classmethod
    def from_dict(cls, data: dict[str, float]) -> ScoreThresholds:
        return cls(
            realized_pnl_high=data.get("realized_pnl_high", REALIZED_PNL_HIGH),
            risk_adjusted_return_high=data.get("risk_adjusted_return_high", RISK_ADJUSTED_RETURN_HIGH),
            notional_median_low=data.get("notional_median_low", NOTIONAL_MEDIAN_LOW),
            notional_median_high=data.get("notional_median_high", NOTIONAL_MEDIAN_HIGH),
            lot_cv_target=data.get("lot_cv_target", LOT_CV_TARGET),
            lot_cv_penalty_coefficient=data.get("lot_cv_penalty_coefficient", LOT_CV_PENALTY_COEFFICIENT),
            return_skew_penalty_coefficient=data.get(
                "return_skew_penalty_coefficient", RETURN_SKEW_PENALTY_COEFFICIENT
            ),
            max_trade_profit_penalty_coefficient=data.get(
                "max_trade_profit_penalty_coefficient", MAX_TRADE_PROFIT_PENALTY_COEFFICIENT
            ),
            interval_cv_target=data.get("interval_cv_target", INTERVAL_CV_TARGET),
            interval_cv_penalty_coefficient=data.get(
                "interval_cv_penalty_coefficient", INTERVAL_CV_PENALTY_COEFFICIENT
            ),
            notional_p95_low=data.get("notional_p95_low", NOTIONAL_P95_LOW),
            notional_p95_high=data.get("notional_p95_high", NOTIONAL_P95_HIGH),
            max_trade_profit_share_exclusion=data.get(
                "max_trade_profit_share_exclusion", MAX_TRADE_PROFIT_SHARE_EXCLUSION
            ),
            max_coin_profit_share_exclusion=data.get(
                "max_coin_profit_share_exclusion", MAX_COIN_PROFIT_SHARE_EXCLUSION
            ),
            min_profit_factor=data.get("min_profit_factor", MIN_PROFIT_FACTOR),
            unrealized_loss_to_profit_exclusion=data.get(
                "unrealized_loss_to_profit_exclusion", UNREALIZED_LOSS_TO_PROFIT_EXCLUSION
            ),
            rank_a_min=data.get("rank_a_min", RANK_A_MIN),
            rank_b_min=data.get("rank_b_min", RANK_B_MIN),
            rank_c_min=data.get("rank_c_min", RANK_C_MIN),
        )


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


@dataclass(frozen=True)
class _WalletMetrics:
    trade_count: int
    realized_pnl: float
    profit_factor: float
    max_drawdown: float
    positive_day_count: int
    active_day_count: int
    max_trade_profit_share: float
    max_coin_profit_share: float
    pnl_30d: float
    pnl_90d: float
    pnl_180d: float
    unrealized_loss_to_profit: float
    avg_interval_minutes: float
    interval_cv: float


@dataclass(frozen=True)
class _ReturnScores:
    realized_pnl: float
    risk_adjusted_return: float
    drawdown_control: float
    consistency: float


@dataclass(frozen=True)
class _GamingScores:
    lot_size_naturalness: float
    return_distribution_quality: float
    trade_interval_naturalness: float
    out_of_sample_survival: float
    pnl_concentration_inverse: float
    leverage_tail_risk_inverse: float
    resistance: float


@dataclass(frozen=True)
class _ExposureScores:
    liquidity_replicability: float
    style_clarity: float
    crowding_penalty_inverse: float


def _wallet_metrics(group: pd.DataFrame, now: pd.Timestamp) -> _WalletMetrics:
    pnl = group["closedPnl"]
    trade_count = len(group)
    realized_pnl = float(pnl.sum())
    total_profit = float(pnl[pnl > 0].sum())
    profit_factor = _profit_factor(pnl)
    max_drawdown = _max_drawdown(pnl)
    positive_days = group.loc[pnl > 0].groupby(group["datetime"].dt.date)["closedPnl"].sum()
    all_days = group.groupby(group["datetime"].dt.date)["closedPnl"].sum()

    max_trade_profit_share = _safe_ratio(float(pnl.max()), total_profit) if total_profit > 0 else 0.0
    coin_profit = group.groupby("coin")["closedPnl"].sum() if "coin" in group.columns else pd.Series(dtype=float)
    max_coin_profit_share = (
        _safe_ratio(float(coin_profit.max()), total_profit)
        if total_profit > 0 and not coin_profit.empty
        else 0.0
    )

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

    return _WalletMetrics(
        trade_count=trade_count,
        realized_pnl=realized_pnl,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        positive_day_count=len(positive_days),
        active_day_count=len(all_days),
        max_trade_profit_share=max_trade_profit_share,
        max_coin_profit_share=max_coin_profit_share,
        pnl_30d=pnl_30d,
        pnl_90d=pnl_90d,
        pnl_180d=pnl_180d,
        unrealized_loss_to_profit=unrealized_loss_to_profit,
        avg_interval_minutes=avg_interval_minutes,
        interval_cv=interval_cv,
    )


def _return_scores(metrics: _WalletMetrics, thresholds: ScoreThresholds) -> _ReturnScores:
    realized_pnl_score = _linear_score(metrics.realized_pnl, 0, thresholds.realized_pnl_high)
    risk_adjusted_return_score = (
        _linear_score(
            _safe_ratio(metrics.realized_pnl, metrics.max_drawdown),
            0,
            thresholds.risk_adjusted_return_high,
        )
        if metrics.max_drawdown
        else (100.0 if metrics.realized_pnl > 0 else 0.0)
    )
    drawdown_control_score = _clip_score(
        100
        - _linear_score(
            metrics.max_drawdown,
            0,
            max(abs(metrics.realized_pnl), 1),
        )
    )
    consistency_score = _clip_score(
        _safe_ratio(metrics.positive_day_count, max(metrics.active_day_count, 1)) * 100
    )
    return _ReturnScores(
        realized_pnl=realized_pnl_score,
        risk_adjusted_return=risk_adjusted_return_score,
        drawdown_control=drawdown_control_score,
        consistency=consistency_score,
    )


def _gaming_scores(
    group: pd.DataFrame,
    metrics: _WalletMetrics,
    thresholds: ScoreThresholds,
) -> _GamingScores:
    pnl = group["closedPnl"]
    lot_cv = float(group["sz"].std() / group["sz"].mean()) if group["sz"].mean() else 0.0
    lot_size_naturalness_score = _clip_score(
        100 - abs(lot_cv - thresholds.lot_cv_target) * thresholds.lot_cv_penalty_coefficient
    )
    return_distribution_quality_score = _clip_score(
        100
        - abs(float(pnl.skew() or 0)) * thresholds.return_skew_penalty_coefficient
        - metrics.max_trade_profit_share * thresholds.max_trade_profit_penalty_coefficient
    )
    trade_interval_naturalness_score = _clip_score(
        100 - abs(metrics.interval_cv - thresholds.interval_cv_target) * thresholds.interval_cv_penalty_coefficient
    )
    sorted_group = group.sort_values("datetime")
    first_half = sorted_group.head(max(metrics.trade_count // 2, 1))["closedPnl"].sum()
    second_half = sorted_group.tail(max(metrics.trade_count // 2, 1))["closedPnl"].sum()
    out_of_sample_survival_score = (
        100.0
        if first_half > 0 and second_half > 0
        else _linear_score(second_half, 0, max(abs(first_half), 1))
    )
    pnl_concentration_inverse = _clip_score(100 - metrics.max_trade_profit_share * 100)
    leverage_tail_risk_inverse = _clip_score(
        100
        - _linear_score(
            float(group["notional"].quantile(0.95)),
            thresholds.notional_p95_low,
            thresholds.notional_p95_high,
        )
    )
    resistance = _clip_score(
        0.25 * lot_size_naturalness_score
        + 0.20 * return_distribution_quality_score
        + 0.20 * trade_interval_naturalness_score
        + 0.15 * out_of_sample_survival_score
        + 0.10 * pnl_concentration_inverse
        + 0.10 * leverage_tail_risk_inverse
    )
    return _GamingScores(
        lot_size_naturalness=lot_size_naturalness_score,
        return_distribution_quality=return_distribution_quality_score,
        trade_interval_naturalness=trade_interval_naturalness_score,
        out_of_sample_survival=out_of_sample_survival_score,
        pnl_concentration_inverse=pnl_concentration_inverse,
        leverage_tail_risk_inverse=leverage_tail_risk_inverse,
        resistance=resistance,
    )


def _exposure_scores(
    group: pd.DataFrame,
    metrics: _WalletMetrics,
    thresholds: ScoreThresholds,
) -> _ExposureScores:
    liquidity_replicability_score = _clip_score(
        100
        - _linear_score(
            float(group["notional"].median()),
            thresholds.notional_median_low,
            thresholds.notional_median_high,
        )
    )
    side_clarity = (
        group.get("side", pd.Series(dtype=str)).value_counts(normalize=True).max()
        if "side" in group
        else 0.5
    )
    style_clarity_score = _clip_score(max(side_clarity, metrics.max_coin_profit_share) * 100)
    crowding_penalty_inverse = _clip_score(100 - metrics.max_coin_profit_share * 100)
    return _ExposureScores(
        liquidity_replicability=liquidity_replicability_score,
        style_clarity=style_clarity_score,
        crowding_penalty_inverse=crowding_penalty_inverse,
    )


def _composite_score(
    returns: _ReturnScores,
    gaming: _GamingScores,
    exposure: _ExposureScores,
) -> float:
    return _clip_score(
        0.20 * returns.realized_pnl
        + 0.15 * returns.risk_adjusted_return
        + 0.15 * returns.drawdown_control
        + 0.15 * returns.consistency
        + 0.10 * exposure.liquidity_replicability
        + 0.10 * exposure.style_clarity
        + 0.10 * gaming.resistance
        + 0.05 * exposure.crowding_penalty_inverse
    )


def _exclusion_reasons(
    metrics: _WalletMetrics,
    min_trades: int,
    thresholds: ScoreThresholds,
) -> list[str]:
    reasons: list[str] = []
    if metrics.trade_count < min_trades:
        reasons.append("取引回数不足")
    if metrics.max_trade_profit_share >= thresholds.max_trade_profit_share_exclusion:
        reasons.append("最大利益トレード依存")
    if metrics.max_coin_profit_share >= thresholds.max_coin_profit_share_exclusion:
        reasons.append("単一銘柄利益依存")
    if metrics.profit_factor < thresholds.min_profit_factor:
        reasons.append("プロフィットファクター不足")
    if metrics.pnl_30d > 0 and (metrics.pnl_90d < 0 or metrics.pnl_180d < 0):
        reasons.append("直近だけ好調")
    if (
        metrics.realized_pnl > 0
        and metrics.unrealized_loss_to_profit >= thresholds.unrealized_loss_to_profit_exclusion
    ):
        reasons.append("未実現損失過大")
    return reasons


def _rank(score: float, thresholds: ScoreThresholds) -> str:
    if score >= thresholds.rank_a_min:
        return "A"
    if score >= thresholds.rank_b_min:
        return "B"
    if score >= thresholds.rank_c_min:
        return "C"
    return "D"


def _score_wallet(
    address: str,
    group: pd.DataFrame,
    now: pd.Timestamp,
    min_trades: int,
    thresholds: ScoreThresholds,
) -> WalletScores:
    group = group.copy()
    group["closedPnl"] = pd.to_numeric(group.get("closedPnl", 0), errors="coerce").fillna(0.0)
    group["px"] = pd.to_numeric(group.get("px", 0), errors="coerce").fillna(0.0)
    group["sz"] = pd.to_numeric(group.get("sz", 0), errors="coerce").fillna(0.0)
    group["notional"] = (group["px"].abs() * group["sz"].abs()).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    group["datetime"] = pd.to_datetime(group.get("datetime", pd.NaT), utc=True, errors="coerce")

    metrics = _wallet_metrics(group, now)
    returns = _return_scores(metrics, thresholds)
    gaming = _gaming_scores(group, metrics, thresholds)
    exposure = _exposure_scores(group, metrics, thresholds)
    perp_score = _composite_score(returns, gaming, exposure)
    reasons = _exclusion_reasons(metrics, min_trades, thresholds)

    return WalletScores(
        wallet_address=address,
        trade_count=metrics.trade_count,
        realized_pnl=metrics.realized_pnl,
        profit_factor=metrics.profit_factor,
        max_drawdown=metrics.max_drawdown,
        realized_pnl_score=returns.realized_pnl,
        risk_adjusted_return_score=returns.risk_adjusted_return,
        drawdown_control_score=returns.drawdown_control,
        consistency_score=returns.consistency,
        liquidity_replicability_score=exposure.liquidity_replicability,
        style_clarity_score=exposure.style_clarity,
        gaming_resistance_score=gaming.resistance,
        crowding_penalty_inverse=exposure.crowding_penalty_inverse,
        perp_score_v2=perp_score,
        rank=_rank(perp_score, thresholds),
        trade_style=_classify_style(group, metrics.trade_count, metrics.avg_interval_minutes),
        excluded=bool(reasons),
        exclusion_reasons="; ".join(reasons),
        max_trade_profit_share=metrics.max_trade_profit_share,
        max_coin_profit_share=metrics.max_coin_profit_share,
        pnl_30d=metrics.pnl_30d,
        pnl_90d=metrics.pnl_90d,
        pnl_180d=metrics.pnl_180d,
        unrealized_loss_to_profit=metrics.unrealized_loss_to_profit,
        lot_size_naturalness_score=gaming.lot_size_naturalness,
        return_distribution_quality_score=gaming.return_distribution_quality,
        trade_interval_naturalness_score=gaming.trade_interval_naturalness,
        out_of_sample_survival_score=gaming.out_of_sample_survival,
        pnl_concentration_inverse=gaming.pnl_concentration_inverse,
        leverage_tail_risk_inverse=gaming.leverage_tail_risk_inverse,
    )


def perp_score_v2(
    fills_df: pd.DataFrame,
    config: dict[str, Any] | PipelineConfig | None = None,
) -> pd.DataFrame:
    """Score Hyperliquid perp wallets and return one row per wallet."""
    pipeline_config = config if isinstance(config, PipelineConfig) else PipelineConfig.from_dict(config or {})
    min_trades = pipeline_config.hyperliquid.min_trades
    thresholds = ScoreThresholds.from_dict(pipeline_config.scoring.thresholds)
    columns = [field.name for field in WalletScores.__dataclass_fields__.values()]
    if fills_df.empty or "wallet_address" not in fills_df.columns:
        return pd.DataFrame(columns=columns)

    now = pd.Timestamp.now(tz="UTC")
    scores = [
        _score_wallet(address, group, now=now, min_trades=min_trades, thresholds=thresholds)
        for address, group in fills_df.groupby("wallet_address", dropna=False)
    ]
    return pd.DataFrame([score.__dict__ for score in scores]).sort_values(
        ["excluded", "perp_score_v2"], ascending=[True, False]
    )
