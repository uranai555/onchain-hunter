"""Report generation for strategy fingerprint analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.utils.io import ensure_directory
from src.utils.logger import get_logger

logger = get_logger("analysis.report")


def generate_fingerprint_report(fingerprints: list[dict[str, Any]], output_dir: str) -> None:
    """Generate per-wallet YAML reports, an index, and comparison CSVs."""
    out = ensure_directory(output_dir)
    ensure_directory(out / "comparison")

    for fingerprint in fingerprints:
        wallet = str(fingerprint.get("wallet", "unknown"))
        path = out / f"{wallet}.fingerprint.yaml"
        with path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(_to_builtin(fingerprint), handle, sort_keys=False, allow_unicode=True)

    _generate_index(fingerprints, str(out))
    _generate_comparison(fingerprints, str(out))
    logger.info("Strategy fingerprint report -> %s", out)


def _generate_index(fingerprints: list[dict[str, Any]], output_dir: str) -> None:
    """Generate a Markdown index for all analyzed wallets."""
    out = Path(output_dir)
    rows = []
    for fp in fingerprints:
        perf = fp.get("performance", {})
        coin = fp.get("coin_profile", {})
        bot = fp.get("bot_indicators", {})
        sizing = fp.get("position_sizing", {})
        rows.append(
            {
                "wallet": str(fp.get("wallet", "")),
                "total_pnl": float(perf.get("total_pnl", 0.0)),
                "win_rate": float(perf.get("win_rate", 0.0)),
                "profit_factor": float(perf.get("profit_factor", 0.0)),
                "total_trades": int(perf.get("total_trades", 0)),
                "primary_coins": ", ".join(coin.get("primary_coins", [])),
                "sizing_style": str(sizing.get("sizing_style", "")),
                "automation_likelihood": float(bot.get("automation_likelihood", 0.0)),
            }
        )

    df = pd.DataFrame(rows).sort_values("total_pnl", ascending=False) if rows else pd.DataFrame(rows)
    lines = [
        "# Strategy Fingerprint Index",
        "",
        f"Wallets analyzed: {len(fingerprints)}",
        "",
    ]
    if df.empty:
        lines.append("No wallets met the analysis threshold.")
    else:
        lines.extend(
            [
                "| Rank | Wallet | Total PnL | Win Rate | Profit Factor | Trades | Primary Coins | Sizing | Automation |",
                "|---:|---|---:|---:|---:|---:|---|---|---:|",
            ]
        )
        for rank, row in enumerate(df.itertuples(index=False), start=1):
            wallet = str(row.wallet)
            link = f"[{wallet}]({wallet}.fingerprint.yaml)"
            lines.append(
                f"| {rank} | {link} | {row.total_pnl:.2f} | {row.win_rate:.2%} | "
                f"{row.profit_factor:.2f} | {row.total_trades} | {row.primary_coins} | "
                f"{row.sizing_style} | {row.automation_likelihood:.2f} |"
            )

    (out / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _generate_comparison(fingerprints: list[dict[str, Any]], output_dir: str) -> None:
    """Generate comparison CSV tables across wallets."""
    out = ensure_directory(Path(output_dir) / "comparison")
    sizing_rows = []
    time_rows = []
    coin_rows = []
    for fp in fingerprints:
        wallet = str(fp.get("wallet", ""))
        sizing = fp.get("position_sizing", {})
        time = fp.get("time_patterns", {})
        coin = fp.get("coin_profile", {})
        sizing_rows.append({"wallet": wallet, **sizing})
        time_rows.append({"wallet": wallet, **time})
        coin_rows.append(
            {
                "wallet": wallet,
                **{
                    key: (", ".join(value) if isinstance(value, list) else value)
                    for key, value in coin.items()
                },
            }
        )

    pd.DataFrame(sizing_rows).to_csv(out / "sizing_comparison.csv", index=False)
    pd.DataFrame(time_rows).to_csv(out / "time_patterns_comparison.csv", index=False)
    pd.DataFrame(coin_rows).to_csv(out / "coin_preference_comparison.csv", index=False)


def _to_builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _to_builtin(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_builtin(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    return value
