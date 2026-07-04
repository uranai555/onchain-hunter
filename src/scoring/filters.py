from __future__ import annotations

from typing import Any

import pandas as pd

CEX_ADDRESS_PREFIXES = (
    "0x0000000000000000000000000000000000000000",
)


def _append_reason(existing: object, reason: str) -> str:
    parts = [part.strip() for part in str(existing or "").split(";") if part.strip()]
    if reason not in parts:
        parts.append(reason)
    return "; ".join(parts)


def _append_reasons(existing: object, new_reasons: object) -> str:
    """Merge multiple semicolon-separated reason strings, deduplicating."""
    existing_parts = {p.strip() for p in str(existing or "").split(";") if p.strip()}
    new_parts = [p.strip() for p in str(new_reasons or "").split(";") if p.strip()]
    for part in new_parts:
        existing_parts.add(part)
    return "; ".join(sorted(existing_parts))


def apply_exclusion_filters(df_scores: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    if df_scores.empty:
        return df_scores.copy()

    df = df_scores.copy()
    df["excluded"] = df.get("excluded", False).fillna(False).astype(bool)
    df["exclusion_reasons"] = df.get("exclusion_reasons", "").fillna("")

    min_trades = int(config.get("hyperliquid", {}).get("min_trades", 30))
    exclude_cex = bool(config.get("filters", {}).get("exclude_cex_wallets", True))

    # Vectorized filter checks
    addresses = df.get("wallet_address", pd.Series("", index=df.index)).fillna("").str.lower()
    trade_counts = pd.to_numeric(df.get("trade_count", 0), errors="coerce").fillna(0).astype(int)
    max_coin_shares = pd.to_numeric(df.get("max_coin_profit_share", 0), errors="coerce").fillna(0.0)
    profit_factors = pd.to_numeric(df.get("profit_factor", 0), errors="coerce").fillna(0.0)
    unrealized_ratios = pd.to_numeric(df.get("unrealized_loss_to_profit", 0), errors="coerce").fillna(0.0)

    cex_mask = pd.Series(False, index=df.index)
    if exclude_cex:
        for prefix in CEX_ADDRESS_PREFIXES:
            cex_mask |= addresses.str.startswith(prefix.lower())
    low_trades_mask = trade_counts < min_trades
    coin_dep_mask = max_coin_shares >= 0.60
    low_pf_mask = profit_factors < 1.20
    risk_mask = unrealized_ratios >= 0.50

    # Build reason strings vectorized
    reason_parts = pd.Series("", index=df.index)
    for mask, reason in [
        (cex_mask, "CEXウォレット疑い"),
        (low_trades_mask, "取引回数不足"),
        (coin_dep_mask, "単一銘柄依存"),
        (low_pf_mask, "プロフィットファクター不足"),
        (risk_mask, "極端なリスク行動"),
    ]:
        reason_parts = reason_parts.where(~mask, reason_parts + "; " + reason)

    # Merge new reasons with existing ones
    any_excluded = cex_mask | low_trades_mask | coin_dep_mask | low_pf_mask | risk_mask
    new_reasons = reason_parts.str.lstrip("; ")
    df.loc[any_excluded, "excluded"] = True
    df.loc[any_excluded, "exclusion_reasons"] = df.loc[any_excluded, "exclusion_reasons"].combine(
        new_reasons[any_excluded],
        lambda existing, new: _append_reasons(existing, new),
    )

    return df.sort_values(["excluded", "perp_score_v2"], ascending=[True, False])
