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
    gr = float(row.get('gaming_resistance_score', 0) or 0)
    gr_v = "Normal" if gr >= 60 else ("Suspicious" if gr >= 40 else "Exclude")
    return [
        f"### {row.get('wallet_address', '-')}",
        "",
        f"- ランク: {row.get('rank', '-')}",
        f"- perp_score_v2: {_fmt_number(row.get('perp_score_v2'))}",
        f"- 取引スタイル: {row.get('trade_style', '-')}",
        f"- 取引回数: {_fmt_number(row.get('trade_count'), 0)}",
        f"- 実現損益: {_fmt_number(row.get('realized_pnl'))}",
        f"- プロフィットファクター: {_fmt_number(row.get('profit_factor'))}",
        f"- 最大ドローダウン: {_fmt_number(row.get('max_drawdown'))}",
        f"- 最大利益トレード比率: {_fmt_number(float(row.get('max_trade_profit_share', 0)) * 100)}%",
        f"- 最大利益銘柄比率: {_fmt_number(float(row.get('max_coin_profit_share', 0)) * 100)}%",
        f"- 30日損益: {_fmt_number(row.get('pnl_30d'))}",
        f"- 90日損益: {_fmt_number(row.get('pnl_90d'))}",
        f"- 180日損益: {_fmt_number(row.get('pnl_180d'))}",
        "",
        "#### Gaming Resistance",
        f"- 総合スコア: {_fmt_number(gr)} — {gr_v}",
        f"- ロットサイズ自然性: {_fmt_number(row.get('lot_size_naturalness_score'))}",
        f"- 損益分布品質: {_fmt_number(row.get('return_distribution_quality_score'))}",
        f"- 取引間隔自然性: {_fmt_number(row.get('trade_interval_naturalness_score'))}",
        f"- アウトオブサンプル生存: {_fmt_number(row.get('out_of_sample_survival_score'))}",
        f"- PnL集中度逆転: {_fmt_number(row.get('pnl_concentration_inverse'))}",
        f"- レバレッジテールリスク逆転: {_fmt_number(row.get('leverage_tail_risk_inverse'))}",
        f"- 未実現損失/利益比率: {_fmt_number(float(row.get('unrealized_loss_to_profit', 0)) * 100)}%",
        "",
    ]


def generate_daily_report(wallets_df: pd.DataFrame, config: dict[str, Any]) -> str:
    title = "# Hyperliquid Perp 勝ちウォレット日次レポート"
    if wallets_df.empty:
        return "\n".join([title, "", "候補ウォレットのスコアリング結果はありません。", ""])

    df = wallets_df.copy().sort_values(["excluded", "rank", "perp_score_v2"], ascending=[True, True, False])
    active = df[~df["excluded"].fillna(False)]
    excluded = df[df["excluded"].fillna(False)]

    lines = [
        title,
        "",
        f"- 対象ウォレット数: {len(df)}",
        f"- 採用候補: {len(active)}",
        f"- 除外: {len(excluded)}",
        "",
    ]

    for rank in ("A", "B", "C", "D"):
        rank_df = active[active["rank"] == rank]
        lines.extend([f"## {rank}ランク", ""])
        if rank_df.empty:
            lines.extend(["該当なし。", ""])
            continue
        for _, row in rank_df.iterrows():
            lines.extend(_wallet_block(row))

    lines.extend(["## 除外ウォレット", ""])
    if excluded.empty:
        lines.extend(["該当なし。", ""])
    else:
        for _, row in excluded.iterrows():
            lines.extend(
                [
                    f"### {row.get('wallet_address', '-')}",
                    "",
                    f"- 除外理由: {row.get('exclusion_reasons', '-') or '-'}",
                    f"- perp_score_v2: {_fmt_number(row.get('perp_score_v2'))}",
                    f"- 取引回数: {_fmt_number(row.get('trade_count'), 0)}",
                    f"- 実現損益: {_fmt_number(row.get('realized_pnl'))}",
                    "",
                ]
            )

    return "\n".join(lines)


def generate_yield_report(yields_df: pd.DataFrame) -> str:
    """Generate a DefiLlama yield watchlist report in markdown."""
    title = "# DeFi 利回りウォッチリスト"
    if yields_df.empty:
        return "\n".join([title, "", "該当するプールは見つかりませんでした。", ""])

    df = yields_df.copy()
    watch = df[df["verdict"] == "Watch"]
    trial = df[df["verdict"] == "Small trial"]
    avoid = df[df["verdict"] == "Avoid"]

    lines = [
        title,
        "",
        f"- 全候補: {len(df)}",
        f"- Watch: {len(watch)}",
        f"- Small trial: {len(trial)}",
        f"- Avoid: {len(avoid)}",
        "",
    ]

    for section_name, section_df, max_rows in [
        ("Watch — 監視候補", watch, 20),
        ("Small trial — 少量トライアル候補", trial, 20),
        ("Avoid — 回避推奨", avoid, 20),
    ]:
        if section_df.empty:
            lines.extend([f"## {section_name}", "", "該当なし。", ""])
            continue
        lines.extend([f"## {section_name}", ""])
        for _, row in section_df.head(max_rows).iterrows():
            lines.extend([
                f"### {row.get('project', '-')} / {row.get('symbol', '-')}",
                "",
                f"- チェーン: {row.get('chain', '-')}",
                f"- TVL: ${_fmt_number(row.get('tvl_usd'), 0)}",
                f"- APY: {_fmt_number(row.get('apy'))}%",
                f"- 30日平均APY: {_fmt_number(row.get('apy_mean_30d'))}%",
                f"- APY安定性: {_fmt_number(row.get('apy_stability_score'))}",
                f"- ILリスク: {row.get('il_risk', '-')}",
                f"- プール年齢: {_fmt_number(row.get('age_days'), 0)}日",
                f"- 利回りスコア: {_fmt_number(row.get('yield_score'))}",
                f"- 評決: **{row.get('verdict', '-')}**",
                "",
            ])

    return "\n".join(lines)


def generate_csv(wallets_df: pd.DataFrame, path: str) -> None:
    output_path = Path(path)
    ensure_directory(output_path.parent)
    wallets_df.to_csv(output_path, index=False)
