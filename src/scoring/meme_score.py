"""Meme wallet score — Phase 3 scoring for DEX/shi*tcoin wallets.

Implements meme_wallet_score from the onchain-smart-money-hunter spec:
  realized_pnl (0.20)
  multi_token_consistency (0.15)
  entry_timing (0.15)
  exit_quality (0.15)
  rug_avoidance (0.10)
  size_replicability (0.10)
  wallet_naturalness (0.10)
  latency_edge (0.05)

Also includes copyability_score for backtesting how followable a wallet is.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

# ── Weights ──────────────────────────────────────────────────────────

MEME_WALLET_WEIGHTS = {
    "realized_pnl": 0.20,
    "multi_token_consistency": 0.15,
    "entry_timing": 0.15,
    "exit_quality": 0.15,
    "rug_avoidance": 0.10,
    "size_replicability": 0.10,
    "wallet_naturalness": 0.10,
    "latency_edge": 0.05,
}

COPYABILITY_WEIGHTS = {
    "liquidity_after_signal": 0.30,
    "price_move_after_detection": 0.25,
    "average_time_to_exit": 0.20,
    "slippage_tolerance": 0.15,
    "repeatability": 0.10,
}


def _clip100(v: float) -> float:
    """Clip to valid score range 0-100."""
    return max(0.0, min(100.0, v))


def _shrinkage_est(success: int, total: int, prior: float, strength: float = 20.0) -> float:
    """Empirical Bayes shrinkage posterior rate."""
    alpha = prior * strength
    beta = (1.0 - prior) * strength
    return (alpha + success) / (alpha + beta + total)


# ── Sub-scores ──────────────────────────────────────────────────────

def realized_pnl_score(
    wallet_pnl: float,
    median_pnl: float,
    min_pnl: float,
) -> float:
    """Score wallet PnL relative to peer distribution.

    PnL in USD. Returns 0-100 where 60 = median performer.
    """
    if median_pnl == 0:
        return 50.0
    ratio = wallet_pnl / abs(median_pnl)
    # ratio = 1 → 60, ratio = 0 → 0, ratio > 3 → 100
    raw = 60.0 * (ratio / (abs(ratio) + 1.0)) * 2.0
    return _clip100(raw)


def multi_token_consistency_score(
    n_tokens_traded: int,
    n_profitable_tokens: int,
    min_tokens: int = 3,
) -> float:
    """Score consistency across multiple tokens.

    A wallet that profits on many different tokens scores higher.
    Minimum 3 tokens to get non-zero score.
    """
    if n_tokens_traded < min_tokens:
        return 0.0
    if n_tokens_traded == 0:
        return 0.0
    hit_rate = n_profitable_tokens / n_tokens_traded
    # Bonus for trading many tokens
    breadth_bonus = min(1.0, n_tokens_traded / 20.0) * 20.0
    return _clip100(hit_rate * 80.0 + breadth_bonus)


def entry_timing_score(
    avg_time_to_entry: float | None,
    optimal_window_seconds: tuple[float, float] = (10.0, 600.0),
) -> float:
    """Score how early the wallet enters tokens.

    avg_time_to_entry: average seconds from token launch/detection to first buy.
    The optimal window is 10s-10min: early enough to catch the pump but not
    so early it looks like insider/creator.
    """
    if avg_time_to_entry is None or avg_time_to_entry < 0:
        return 50.0  # neutral if unknown

    low, high = optimal_window_seconds
    if low <= avg_time_to_entry <= high:
        return 90.0
    elif avg_time_to_entry < low:
        # Too early — looks suspicious (insider/bot)
        penalty = max(0, low - avg_time_to_entry) / low
        return _clip100(80.0 - penalty * 50.0)
    else:
        # Late entry — less edge
        lateness = (avg_time_to_entry - high) / high
        return _clip100(70.0 - min(lateness, 10.0) * 7.0)


def exit_quality_score(
    avg_exit_pct_of_peak: float | None,
) -> float:
    """Score exit quality — how close to peak they sell.

    avg_exit_pct_of_peak: 0.0-1.0 fraction of maximum price achieved at exit.
    0.8+ = excellent, 0.5 = mediocre, 0.0 = held to zero.
    """
    if avg_exit_pct_of_peak is None:
        return 50.0
    return _clip100(avg_exit_pct_of_peak * 100.0)


def rug_avoidance_score(
    n_rug_tokens_exposed: int,
    n_rug_tokens_exited_early: int,
    n_total_tokens: int,
) -> float:
    """Score ability to avoid or exit early from rug tokens.

    A wallet that avoids rugs entirely or exits them before the dump scores high.
    """
    if n_total_tokens == 0:
        return 50.0
    if n_rug_tokens_exposed == 0:
        return 90.0  # No rug exposure at all — great selection
    avoidance_rate = n_rug_tokens_exited_early / n_rug_tokens_exposed
    return _clip100(avoidance_rate * 100.0)


def size_replicability_score(
    avg_position_usd: float,
    max_position_usd: float,
    min_replicable: float = 50.0,
) -> float:
    """Score whether the wallet's position sizes are replicable.

    Large positions (>min_replicable) may be hard to copy without slippage.
    """
    if max_position_usd <= 0:
        return 50.0
    # Smaller positions are more replicable
    avg = min(avg_position_usd, max_position_usd)
    if avg <= min_replicable:
        return 90.0  # Highly replicable
    # Penalty grows logarithmically
    penalty = min(50.0, math.log2(avg / min_replicable) * 10.0)
    return _clip100(90.0 - penalty)


def wallet_naturalness_score(
    account_age_days: int,
    has_social_activity: bool = False,
    n_cex_deposits: int = 0,
    txn_count: int = 0,
) -> float:
    """Score whether the wallet looks like a natural real user.

    Penalises: brand new wallets, no social footprint, zero CEX deposits.
    """
    score = 50.0
    if account_age_days >= 30:
        score += 20.0
    elif account_age_days >= 7:
        score += 10.0
    if has_social_activity:
        score += 10.0
    if n_cex_deposits > 0:
        score += 10.0
    if txn_count > 100:
        score += 10.0
    elif txn_count > 10:
        score += 5.0
    return _clip100(score)


def latency_edge_score(
    avg_latency_to_first_trade_s: float | None,
    detection_delay_s: float = 60.0,
) -> float:
    """Score the latency edge — how fast after detection the wallet acts.

    Lower latency = more edge. Detection delay is the time between token
    becoming detectable and wallet's first trade.
    """
    if avg_latency_to_first_trade_s is None:
        return 50.0
    if avg_latency_to_first_trade_s < detection_delay_s:
        return 90.0  # Faster than typical detection
    latency_ratio = avg_latency_to_first_trade_s / detection_delay_s
    return _clip100(max(0.0, 90.0 - min(latency_ratio, 20.0) * 5.0))


# ── Composite score ─────────────────────────────────────────────────

def meme_wallet_score(
    realized_pnl: float = 0.0,
    median_pnl: float = 0.0,
    min_pnl: float = 0.0,
    n_tokens_traded: int = 0,
    n_profitable_tokens: int = 0,
    avg_time_to_entry_s: float | None = None,
    avg_exit_pct_of_peak: float | None = None,
    n_rug_tokens_exposed: int = 0,
    n_rug_tokens_exited_early: int = 0,
    n_total_tokens: int = 0,
    avg_position_usd: float = 0.0,
    max_position_usd: float = 0.0,
    account_age_days: int = 0,
    has_social_activity: bool = False,
    n_cex_deposits: int = 0,
    txn_count: int = 0,
    avg_latency_to_first_trade_s: float | None = None,
    component_scores: bool = False,
) -> float | dict[str, Any]:
    """Compute the composite meme_wallet_score (0-100).

    Returns either the total score (float) or a dict with breakdown.
    """
    scores = {
        "realized_pnl": realized_pnl_score(realized_pnl, median_pnl, min_pnl),
        "multi_token_consistency": multi_token_consistency_score(
            n_tokens_traded, n_profitable_tokens
        ),
        "entry_timing": entry_timing_score(avg_time_to_entry_s),
        "exit_quality": exit_quality_score(avg_exit_pct_of_peak),
        "rug_avoidance": rug_avoidance_score(
            n_rug_tokens_exposed, n_rug_tokens_exited_early, n_total_tokens
        ),
        "size_replicability": size_replicability_score(
            avg_position_usd, max_position_usd
        ),
        "wallet_naturalness": wallet_naturalness_score(
            account_age_days, has_social_activity, n_cex_deposits, txn_count
        ),
        "latency_edge": latency_edge_score(avg_latency_to_first_trade_s),
    }
    total = sum(
        scores[k] * MEME_WALLET_WEIGHTS[k]
        for k in MEME_WALLET_WEIGHTS
    )
    total = round(_clip100(total), 2)

    if component_scores:
        return {"total": total, "components": scores, "weights": dict(MEME_WALLET_WEIGHTS)}
    return total


def copyability_score(
    liquidity_after_signal: float = 0.0,
    price_move_after_detection: float = 0.0,
    avg_time_to_exit_min: float | None = None,
    slippage_tolerance_pct: float = 1.0,
    repeatability_pct: float = 0.0,
    component_scores: bool = False,
) -> float | dict[str, Any]:
    """Score how copyable (followable) a wallet's trades are.

    Higher score = easier to follow profitably after detection.
    """
    # Normalise inputs to 0-100 range
    liq_score = _clip100(min(100.0, liquidity_after_signal / 100_000 * 100))
    price_score = _clip100(price_move_after_detection * 100)
    time_score = 100.0 if avg_time_to_exit_min is None else _clip100(
        max(0, 60.0 - avg_time_to_exit_min) / 60.0 * 100 if avg_time_to_exit_min < 60
        else 100.0
    )
    slip_score = _clip100(max(0, 100.0 - slippage_tolerance_pct * 20))
    repeat_score = _clip100(repeatability_pct * 100)

    scores = {
        "liquidity_after_signal": liq_score,
        "price_move_after_detection": price_score,
        "average_time_to_exit": time_score,
        "slippage_tolerance": slip_score,
        "repeatability": repeat_score,
    }
    total = sum(
        scores[k] * COPYABILITY_WEIGHTS[k]
        for k in COPYABILITY_WEIGHTS
    )
    total = round(_clip100(total), 2)

    if component_scores:
        return {"total": total, "components": scores, "weights": dict(COPYABILITY_WEIGHTS)}
    return total