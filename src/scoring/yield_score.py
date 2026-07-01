from __future__ import annotations

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


def _inverse_linear_score(value: float, low: float, high: float) -> float:
    """Higher raw value → lower score (e.g. APY volatility)."""
    if high == low:
        return 50.0
    return _clip_score((high - value) / (high - low) * 100.0)


def score_yields(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Score DefiLlama yield pools and return one row per pool.

    Columns added:
      - net_apy_score
      - tvl_depth_score
      - apy_stability_score
      - protocol_reputation_score
      - liquidity_exit_score
      - reward_source_quality_score
      - smart_money_participation_score
      - yield_score       (composite)
      - yield_rank        (A/B/C/D)
      - verdict           (Watch / Small trial / Avoid)
    """
    if df.empty:
        columns = [
            "pool", "project", "chain", "symbol", "tvl_usd", "apy",
            "net_apy_score", "tvl_depth_score", "apy_stability_score",
            "protocol_reputation_score", "liquidity_exit_score",
            "reward_source_quality_score", "smart_money_participation_score",
            "yield_score", "yield_rank", "verdict",
        ]
        return pd.DataFrame(columns=columns)

    result = df.copy()

    # --- net_apy_score: APY between 5% and 40%, sweet spot 10-20% ---
    result["net_apy_score"] = result["apy"].apply(
        lambda a: _linear_score(a, 5, 20) if a <= 20
        else _inverse_linear_score(a, 20, 40)
    )

    # --- tvl_depth_score: larger TVL = safer exit ---
    result["tvl_depth_score"] = result["tvl_usd"].apply(
        lambda t: _linear_score(t, 5_000_000, 200_000_000)
    )

    # --- apy_stability_score: use the pre-computed stability ---
    result["apy_stability_score"] = result.get("apy_stability_score", 50.0).fillna(50.0)
    # also penalise high APY std as a cross-check
    apy_std = result.get("apy_std", pd.Series(0.0, index=result.index)).fillna(0.0)
    result["apy_stability_score"] = result["apy_stability_score"].combine(
        apy_std.apply(lambda s: _inverse_linear_score(s, 0, 15)),
        min,
    )

    # --- protocol_reputation_score: age + preferred asset as proxy ---
    result["protocol_reputation_score"] = result.apply(
        lambda r: (
            _clip_score(30.0 + float(r.get("age_days", 0)) * 0.1)
            + (25.0 if r.get("is_preferred_asset") else 0.0)
        ),
        axis=1,
    ).clip(upper=100.0)

    # --- liquidity_exit_score: TVL + age proxy for exit depth ---
    result["liquidity_exit_score"] = result.apply(
        lambda r: _clip_score(
            _linear_score(r.get("tvl_usd", 0), 5_000_000, 100_000_000)
            + (10.0 if float(r.get("age_days", 0)) >= 180 else 0.0)
        ),
        axis=1,
    )

    # --- reward_source_quality_score: stablecoin = better ---
    result["reward_source_quality_score"] = result.apply(
        lambda r: (
            80.0 if r.get("is_preferred_asset") else
            50.0 if any(t in str(r.get("symbol", "")).lower() for t in ("eth", "btc", "weth", "wbtc")) else
            30.0
        ),
        axis=1,
    )

    # --- smart_money_participation_score: TVL growth / age as weak proxy ---
    # Pools with 90+ days and decent TVL = more likely vetted
    result["smart_money_participation_score"] = result.apply(
        lambda r: _clip_score(
            _linear_score(r.get("tvl_usd", 0), 5_000_000, 100_000_000) * 0.6
            + _linear_score(float(r.get("age_days", 0)), 0, 365) * 0.4
        ),
        axis=1,
    )

    # --- composite yield_score ---
    weights = {
        "net_apy_score": 0.20,
        "tvl_depth_score": 0.20,
        "apy_stability_score": 0.15,
        "protocol_reputation_score": 0.15,
        "liquidity_exit_score": 0.10,
        "reward_source_quality_score": 0.10,
        "smart_money_participation_score": 0.10,
    }
    result["yield_score"] = sum(
        result.get(col, pd.Series(0.0, index=result.index)) * weight
        for col, weight in weights.items()
    )

    # --- rank & verdict ---
    def _rank_and_verdict(score: float, apy: float, il_risk: str) -> tuple[str, str]:
        if score >= 75 and apy <= 25:
            return ("A", "Watch")
        if score >= 60:
            return ("B", "Small trial")
        if score >= 45:
            return ("C", "Small trial")
        if il_risk in ("high", "very high"):
            return ("D", "Avoid")
        return ("D", "Avoid")

    ranks_verdicts = result.apply(
        lambda r: _rank_and_verdict(
            r.get("yield_score", 0),
            r.get("apy", 0),
            str(r.get("il_risk", "")),
        ),
        axis=1,
    )
    result["yield_rank"] = [rv[0] for rv in ranks_verdicts]
    result["verdict"] = [rv[1] for rv in ranks_verdicts]

    return result.sort_values(["verdict", "yield_score"], ascending=[True, False])
