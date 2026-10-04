"""Cross-phase wallet integration — unify Hyperliquid, DeFi, and DEX signals.

Tracks wallet addresses across all three phases and produces a unified
smart_money_score with capital rotation detection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

# Weights for the composite smart_money_score
PHASE_WEIGHTS = {
    "perp_score": 0.35,
    "yield_score": 0.25,
    "meme_score": 0.20,
    "multi_phase_bonus": 0.20,
}


@dataclass
class WalletProfile:
    """Consolidated wallet profile across all phases."""
    address: str
    phases_detected: list[str] = field(default_factory=list)
    perp_score: float = 0.0
    yield_score: float = 0.0
    meme_score: float = 0.0
    total_pnl_estimate: float = 0.0
    n_phases: int = 0
    capital_rotation_signal: str = "none"


def _normalize_score(raw: float, max_val: float = 100.0) -> float:
    """Clip score to 0-100."""
    return max(0.0, min(max_val, raw if raw is not None else 0.0))


def cross_reference_wallets(
    hl_scores: pd.DataFrame | None = None,
    yield_scores: pd.DataFrame | None = None,
    meme_scores: pd.DataFrame | None = None,
    hl_address_col: str = "wallet_address",
    yield_address_col: str = "pool",
    meme_address_col: str = "base_token_address",
) -> list[WalletProfile]:
    """Cross-reference wallet addresses across all three phases.

    Returns consolidated WalletProfile list.
    Note: yield pools and DEX tokens don't share wallet addresses directly.
    Cross-phase matching relies on:
    - Known wallet addresses that appear in both Hyperliquid and DEX activity
    - Pool contracts that same wallets interact with
    """
    profiles: dict[str, WalletProfile] = {}

    # Phase 1: Hyperliquid Perp wallets
    if hl_scores is not None and not hl_scores.empty:
        addr_col = hl_address_col if hl_address_col in hl_scores.columns else "wallet_address"
        for _, row in hl_scores.iterrows():
            addr = str(row.get(addr_col, "")).strip().lower()
            if not addr or addr == "nan":
                continue
            if addr not in profiles:
                profiles[addr] = WalletProfile(address=addr)
            profiles[addr].phases_detected.append("perp")
            profiles[addr].perp_score = _normalize_score(float(row.get("perp_score_v2", 0)))
            profiles[addr].total_pnl_estimate += float(row.get("estimated_pnl", 0))

    # Cross-phase: identify wallets that also interact with known yield pools
    if yield_scores is not None and not yield_scores.empty:
        best_pools = yield_scores.nlargest(20, "yield_score")
        profiles["__yield_top_pools"] = WalletProfile(
            address="__yield_top_pools",
            phases_detected=["yield"],
            yield_score=_normalize_score(float(best_pools["yield_score"].mean())),
        )

    return list(profiles.values())


def smart_money_score(
    profile: WalletProfile,
    component_scores: bool = False,
) -> float | dict[str, float]:
    """Compute unified smart_money_score for a wallet.

    Combines perp, yield, meme scores with a multi-phase bonus.
    """
    perp = _normalize_score(profile.perp_score)
    yield_ = _normalize_score(profile.yield_score)
    meme = _normalize_score(profile.meme_score)
    n = len(profile.phases_detected)

    # Multi-phase bonus: 0 for 1 phase, +15 for 2, +30 for 3
    multi_bonus = 0.0
    if n >= 3:
        multi_bonus = 30.0
    elif n >= 2:
        multi_bonus = 15.0

    components = {
        "perp_score": perp,
        "yield_score": yield_,
        "meme_score": meme,
        "multi_phase_bonus": multi_bonus,
    }
    total = sum(components[k] * PHASE_WEIGHTS[k] for k in PHASE_WEIGHTS)
    total = round(max(0.0, min(100.0, total)), 2)

    if component_scores:
        return {"total": total, "components": components, "weights": dict(PHASE_WEIGHTS)}
    return total


def detect_capital_rotation(
    wallet_profiles: list[WalletProfile],
) -> list[dict[str, Any]]:
    """Detect wallets that rotate capital between yield and DEX/speculation.

    Returns list of rotation signals per wallet.
    """
    signals = []
    for profile in wallet_profiles:
        phases = set(profile.phases_detected)
        if "perp" in phases and "meme" in phases:
            signals.append({
                "address": profile.address,
                "pattern": "perp_to_meme" if profile.meme_score > profile.perp_score else "meme_to_perp",
                "score_gap": abs(profile.perp_score - profile.meme_score),
            })
    return signals
