"""Render discovery report in Markdown.

Generates reports/discovery_report.md with:
  - Summary of discovery runs
  - New candidates this run
  - Recent detected events
  - Event winners
  - Watchlist changes
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd


def _fmt_number(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


def generate_discovery_report(
    new_candidates: pd.DataFrame,
    events: list[dict[str, Any]],
    winners: dict[str, list[dict[str, Any]]],
    errors: list[str],
    existing_wallet_count: int = 0,
) -> str:
    """Generate the full discovery report in Markdown."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_events = len(events)
    events_with_winners = len(winners)

    lines: list[str] = [
        "# オンチェーンウォレット発見レポート",
        "",
        f"**生成時刻**: {now}",
        f"**既存候補数**: {existing_wallet_count}",
        f"**検出イベント数**: {total_events}",
        f"**勝者マッチイベント数**: {events_with_winners}",
        "",
    ]

    # ── Errors ──
    if errors:
        lines.extend([
            "## エラー",
            "",
        ])
        for err in errors[:5]:
            lines.append(f"- ⚠️ {err}")
        if len(errors) > 5:
            lines.append(f"- ... 他 {len(errors) - 5} 件")
        lines.append("")

    # ── New candidates ──
    if not new_candidates.empty:
        lines.extend([
            "---",
            "## 新規候補ウォレット",
            "",
            "| アドレス | 発見面 | 理由 | 信頼度 | スコア |",
            "|---------|--------|------|--------|-------|",
        ])
        for _, row in new_candidates.iterrows():
            addr = str(row.get("wallet_address", ""))[:14] + "..."
            surface = str(row.get("source_surface", "-"))
            reason = str(row.get("discovery_reason", "-"))
            confidence = str(row.get("source_confidence", "low"))
            score = _fmt_number(float(row.get("raw_score", 0) or 0), 0)
            lines.append(f"| {addr} | {surface} | {reason} | {confidence} | {score} |")
        lines.append("")

    # ── Detected events ──
    if events:
        lines.extend([
            "---",
            "## 検出イベント",
            "",
            "| 時刻 (UTC) | 銘柄 | 種別 | 変動率 | 説明 |",
            "|-----------|------|------|--------|------|",
        ])
        for ev in sorted(events, key=lambda e: e.get("event_time", ""), reverse=True)[:20]:
            ts = str(ev.get("event_time", ""))[:19]
            symbol = ev.get("symbol", "-")
            etype = ev.get("event_type", "-")
            change = _fmt_pct(ev.get("price_change_pct"))
            desc = str(ev.get("description", ""))[:60]
            lines.append(f"| {ts} | {symbol} | {etype} | {change} | {desc} |")
        lines.append("")

    # ── Event winners ──
    if winners:
        lines.extend([
            "---",
            "## イベント勝者",
            "",
        ])
        for event_key in sorted(winners.keys(), reverse=True)[:10]:
            wallet_list = winners[event_key]
            lines.extend([
                f"### {event_key}",
                "",
                f"**該当ウォレット数**: {len(wallet_list)}",
                "",
                "| アドレス | 事前ポジション | エントリー品質 | 利確品質 | 推定PnL | 取引数 |",
                "|---------|---------------|--------------|---------|--------|-------|",
            ])
            for w in wallet_list[:5]:
                addr = str(w.get("wallet_address", ""))[:14] + "..."
                pre = _fmt_number(w.get("pre_positioning_score", 0), 0)
                exec_ = _fmt_number(w.get("execution_score", 0), 0)
                exit_ = _fmt_number(w.get("exit_quality", 0), 0)
                pnl = _fmt_number(w.get("estimated_pnl", 0))
                tc = int(w.get("trade_count_in_window", 0))
                lines.append(f"| {addr} | {pre} | {exec_} | {exit_} | {pnl} | {tc} |")
            if len(wallet_list) > 5:
                lines.append(f"| ... 他 {len(wallet_list) - 5} 件 |")
            lines.append("")

    # ── Summary ──
    lines.extend([
        "---",
        "## サマリー",
        "",
        f"- 検出イベント数: {total_events}",
        f"- 勝者ウォレットありイベント: {events_with_winners}",
        f"- 新規候補: {len(new_candidates)}",
        f"- エラー: {len(errors)}",
        "",
    ])

    return "\n".join(lines)


def generate_discovery_report_short(
    events_found: int,
    events_with_winners: int,
    new_candidates: int,
    errors: int,
) -> str:
    """Short summary for the pipeline log."""
    lines = [
        "── Discovery Report ──",
        f"Events: {events_found} detected, {events_with_winners} with winners",
        f"New candidates: {new_candidates}",
        f"Errors: {errors}",
        "─────────────────────",
    ]
    return "\n".join(lines)
