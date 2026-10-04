"""DEX token score — score tokens on pump potential.

Uses DexScreener data to evaluate tokens for Phase 3 DEX wallet hunting.
Token-level scoring identifies promising tokens early, before they're widely
known. Wallet extraction follows in a downstream step.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def _clip100(v: float) -> float:
    return max(0.0, min(100.0, v))


# ── Token scoring components ────────────────────────────────────────

def liquidity_depth_score(liquidity_usd: float) -> float:
    """Score liquidity depth.

    More liquidity = better entry/exit, but extremely high liquidity
    may mean the token is already mature.
    """
    if liquidity_usd <= 0:
        return 0.0
    # Sweet spot: $5k-$500k
    if liquidity_usd < 5_000:
        return _clip100(liquidity_usd / 5_000 * 30)  # up to 30
    elif 5_000 <= liquidity_usd <= 500_000:
        return 80.0
    elif 500_000 < liquidity_usd <= 5_000_000:
        return 60.0
    else:
        return 40.0  # Too mature


def volume_momentum_score(volume_24h_usd: float, liquidity_usd: float) -> float:
    """Score volume relative to liquidity — turnover ratio.

    High volume / low liquidity = high velocity = early pump stage.
    """
    if liquidity_usd <= 0:
        return 0.0
    turnover = volume_24h_usd / liquidity_usd
    if turnover > 10:
        return 90.0  # Extremely high velocity
    elif turnover > 3:
        return 70.0
    elif turnover > 1:
        return 50.0
    elif turnover > 0.3:
        return 30.0
    else:
        return 10.0


def age_score(age_hours: float | None) -> float:
    """Score token age — younger is better for early entry.

    Tokens < 24h old: prime hunting ground.
    Tokens > 7d: likely already mature, lower score.
    """
    if age_hours is None:
        return 50.0
    if age_hours <= 1:
        return 95.0  # Just created
    elif age_hours <= 6:
        return 85.0
    elif age_hours <= 24:
        return 70.0
    elif age_hours <= 72:
        return 50.0
    elif age_hours <= 168:  # 7d
        return 30.0
    else:
        return 10.0


def social_signal_score(
    has_website: bool = False,
    has_twitter: bool = False,
    has_telegram: bool = False,
    description: str = "",
    cto: bool = False,
) -> float:
    """Score social signals for a token.

    Tokens with genuine social presence are slightly less likely to be
    instant rugs, but also more likely to be known.
    """
    score = 50.0
    if has_twitter:
        score += 10.0
    if has_website:
        score += 10.0
    if has_telegram:
        score += 5.0
    if len(description.strip()) > 20:
        score += 5.0
    if cto:
        score -= 20.0  # CTO (community take-over) = higher rug risk
    return _clip100(score)


def price_action_score(
    price_usd: float,
    price_change_24h_pct: float | None = None,
    price_change_6h_pct: float | None = None,
) -> float:
    """Score price action — looking for early pump signals.

    A moderate positive price change (20-200%) with recent acceleration
    is the sweet spot.
    """
    if price_usd <= 0:
        return 0.0
    base = 50.0

    # Use 6h change if available, else 24h
    pct = price_change_6h_pct if price_change_6h_pct is not None else price_change_24h_pct
    if pct is None:
        return base

    if pct > 1000:
        return 30.0  # Already mooned — too late
    elif 200 < pct <= 1000:
        return 40.0  # Pumped hard but maybe still room
    elif 50 < pct <= 200:
        return 80.0  # Sweet spot: pumping but not topped
    elif 10 < pct <= 50:
        return 70.0  # Good early signal
    elif -10 <= pct <= 10:
        return 50.0  # Flat
    elif -50 <= pct < -10:
        return 30.0  # Declining
    else:
        return 10.0  # Crashing


# ── Composite token score ───────────────────────────────────────────

TOKEN_SCORE_WEIGHTS = {
    "liquidity_depth": 0.20,
    "volume_momentum": 0.25,
    "age": 0.20,
    "social_signal": 0.10,
    "price_action": 0.25,
}


def token_pump_score(
    row: dict[str, Any] | pd.Series,
    component_scores: bool = False,
) -> float | dict[str, Any]:
    """Compute composite token pump score (0-100).

    Higher score = better candidate for early entry.
    """
    scores = {
        "liquidity_depth": liquidity_depth_score(float(row.get("liquidity_usd", 0))),
        "volume_momentum": volume_momentum_score(
            float(row.get("volume_24h_usd", 0)),
            float(row.get("liquidity_usd", 0)),
        ),
        "age": age_score(row.get("age_hours")),
        "social_signal": social_signal_score(
            has_website=bool(row.get("has_website", False)),
            has_twitter=bool(row.get("has_twitter", False)),
            has_telegram=bool(row.get("has_telegram", False)),
            description=str(row.get("description", "")),
            cto=bool(row.get("cto", False)),
        ),
        "price_action": price_action_score(
            float(row.get("price_usd", 0)),
            price_change_24h_pct=row.get("price_change_24h_pct"),
            price_change_6h_pct=row.get("price_change_6h_pct"),
        ),
    }
    total = sum(
        scores[k] * TOKEN_SCORE_WEIGHTS[k]
        for k in TOKEN_SCORE_WEIGHTS
    )
    total = round(_clip100(total), 2)

    if component_scores:
        return {"total": total, "components": scores, "weights": dict(TOKEN_SCORE_WEIGHTS)}
    return total


def score_token_discovery_df(
    tokens_df: pd.DataFrame,
) -> pd.DataFrame:
    """Add pump score columns to a token discovery DataFrame."""
    df = tokens_df.copy()
    score_results = df.apply(token_pump_score, axis=1, result_type="expand")
    if isinstance(score_results, pd.Series):
        df["pump_score"] = score_results
    elif isinstance(score_results, pd.DataFrame):
        # component scores
        df["pump_score"] = score_results.apply(
            lambda r: r["total"] if isinstance(r, dict) and "total" in r else r,
            axis=1,
        )
    df = df.sort_values("pump_score", ascending=False)
    return df
