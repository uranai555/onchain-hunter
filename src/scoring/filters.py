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


def apply_exclusion_filters(df_scores: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    if df_scores.empty:
        return df_scores.copy()

    df = df_scores.copy()
    df["excluded"] = df.get("excluded", False).fillna(False).astype(bool)
    df["exclusion_reasons"] = df.get("exclusion_reasons", "").fillna("")

    min_trades = int(config.get("hyperliquid", {}).get("min_trades", 30))
    exclude_cex = bool(config.get("filters", {}).get("exclude_cex_wallets", True))

    for idx, row in df.iterrows():
        reasons: list[str] = []
        address = str(row.get("wallet_address", "")).lower()
        if exclude_cex and any(address.startswith(prefix.lower()) for prefix in CEX_ADDRESS_PREFIXES):
            reasons.append("CEXウォレット疑い")
        if int(row.get("trade_count", 0) or 0) < min_trades:
            reasons.append("取引回数不足")
        if float(row.get("max_coin_profit_share", 0) or 0) >= 0.60:
            reasons.append("単一銘柄依存")
        if float(row.get("profit_factor", 0) or 0) < 1.20:
            reasons.append("プロフィットファクター不足")
        if float(row.get("unrealized_loss_to_profit", 0) or 0) >= 0.50:
            reasons.append("極端なリスク行動")

        for reason in reasons:
            df.at[idx, "excluded"] = True
            df.at[idx, "exclusion_reasons"] = _append_reason(df.at[idx, "exclusion_reasons"], reason)

    return df.sort_values(["excluded", "perp_score_v2"], ascending=[True, False])
