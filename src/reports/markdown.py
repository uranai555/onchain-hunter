from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src.utils.io import ensure_directory


def _fmt_number(value: object, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if pd.isna(number):
        return "-"
    return f"{number:,.{digits}f}"


def _wallet_block(row: pd.Series) -> list[str]:
    gr = float(row.get("gaming_resistance_score", 0) or 0)
    gr_v = "Normal" if gr >= 60 else ("Suspicious" if gr >= 40 else "Exclude")
    return [
        f"### {row.get('wallet_address', '-')}",
        "",
        f"- Rank: {row.get('rank', '-')}",
        f"- perp_score_v2: {_fmt_number(row.get('perp_score_v2'))}",
        f"- Trade style: {row.get('trade_style', '-')}",
        f"- Trade count: {_fmt_number(row.get('trade_count'), 0)}",
        f"- Realized PnL: {_fmt_number(row.get('realized_pnl'))}",
        f"- Profit factor: {_fmt_number(row.get('profit_factor'))}",
        f"- Max drawdown: {_fmt_number(row.get('max_drawdown'))}",
        f"- Max trade profit share: {_fmt_number(float(row.get('max_trade_profit_share', 0)) * 100)}%",
        f"- Max coin profit share: {_fmt_number(float(row.get('max_coin_profit_share', 0)) * 100)}%",
        f"- 30D PnL: {_fmt_number(row.get('pnl_30d'))}",
        f"- 90D PnL: {_fmt_number(row.get('pnl_90d'))}",
        f"- 180D PnL: {_fmt_number(row.get('pnl_180d'))}",
        "",
        "#### Gaming Resistance",
        f"- Composite score: {_fmt_number(gr)} - {gr_v}",
        f"- Lot size naturalness: {_fmt_number(row.get('lot_size_naturalness_score'))}",
        f"- Return distribution quality: {_fmt_number(row.get('return_distribution_quality_score'))}",
        f"- Trade interval naturalness: {_fmt_number(row.get('trade_interval_naturalness_score'))}",
        f"- Out-of-sample survival: {_fmt_number(row.get('out_of_sample_survival_score'))}",
        f"- PnL concentration inverse: {_fmt_number(row.get('pnl_concentration_inverse'))}",
        f"- Leverage tail-risk inverse: {_fmt_number(row.get('leverage_tail_risk_inverse'))}",
        f"- Unrealized loss / profit: {_fmt_number(float(row.get('unrealized_loss_to_profit', 0)) * 100)}%",
        "",
    ]


def generate_daily_report(wallets_df: pd.DataFrame, config: dict[str, Any]) -> str:
    title = "# Hyperliquid Perp Winning Wallets Daily Report"
    if wallets_df.empty:
        return "\n".join([title, "", "No scored candidate wallets.", ""])

    df = wallets_df.copy().sort_values(["excluded", "rank", "perp_score_v2"], ascending=[True, True, False])
    active = df[~df["excluded"].fillna(False)]
    excluded = df[df["excluded"].fillna(False)]

    lines = [
        title,
        "",
        f"- Wallets scored: {len(df)}",
        f"- Active candidates: {len(active)}",
        f"- Excluded: {len(excluded)}",
        "",
    ]

    for rank in ("A", "B", "C", "D"):
        rank_df = active[active["rank"] == rank]
        lines.extend([f"## Rank {rank}", ""])
        if rank_df.empty:
            lines.extend(["None.", ""])
            continue
        for _, row in rank_df.iterrows():
            lines.extend(_wallet_block(row))

    lines.extend(["## Excluded Wallets", ""])
    if excluded.empty:
        lines.extend(["None.", ""])
    else:
        for _, row in excluded.iterrows():
            lines.extend(
                [
                    f"### {row.get('wallet_address', '-')}",
                    "",
                    f"- Exclusion reasons: {row.get('exclusion_reasons', '-') or '-'}",
                    f"- perp_score_v2: {_fmt_number(row.get('perp_score_v2'))}",
                    f"- Trade count: {_fmt_number(row.get('trade_count'), 0)}",
                    f"- Realized PnL: {_fmt_number(row.get('realized_pnl'))}",
                    "",
                ]
            )

    return "\n".join(lines)


def generate_yield_report(yields_df: pd.DataFrame) -> str:
    """Generate a DefiLlama yield watchlist report in markdown."""
    title = "# DeFi Yield Watchlist"
    if yields_df.empty:
        return "\n".join([title, "", "No matching pools found.", ""])

    df = yields_df.copy()
    watch = df[df["verdict"] == "Watch"]
    trial = df[df["verdict"] == "Small trial"]
    avoid = df[df["verdict"] == "Avoid"]

    lines = [
        title,
        "",
        f"- Total candidates: {len(df)}",
        f"- Watch: {len(watch)}",
        f"- Small trial: {len(trial)}",
        f"- Avoid: {len(avoid)}",
        "",
    ]

    for section_name, section_df, max_rows in [
        ("Watch - monitoring candidates", watch, 20),
        ("Small trial - small-size candidates", trial, 20),
        ("Avoid - skip candidates", avoid, 20),
    ]:
        if section_df.empty:
            lines.extend([f"## {section_name}", "", "None.", ""])
            continue
        lines.extend([f"## {section_name}", ""])
        for _, row in section_df.head(max_rows).iterrows():
            lines.extend(
                [
                    f"### {row.get('project', '-')} / {row.get('symbol', '-')}",
                    "",
                    f"- Chain: {row.get('chain', '-')}",
                    f"- TVL: ${_fmt_number(row.get('tvl_usd'), 0)}",
                    f"- APY: {_fmt_number(row.get('apy'))}%",
                    f"- 30D mean APY: {_fmt_number(row.get('apy_mean_30d'))}%",
                    f"- APY stability: {_fmt_number(row.get('apy_stability_score'))}",
                    f"- IL risk: {row.get('il_risk', '-')}",
                    f"- Pool age: {_fmt_number(row.get('age_days'), 0)} days",
                    f"- Yield score: {_fmt_number(row.get('yield_score'))}",
                    f"- Verdict: **{row.get('verdict', '-')}**",
                    "",
                ]
            )

    return "\n".join(lines)


def generate_csv(wallets_df: pd.DataFrame, path: str) -> None:
    output_path = Path(path)
    ensure_directory(output_path.parent)
    wallets_df.to_csv(output_path, index=False)
