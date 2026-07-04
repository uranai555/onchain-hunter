from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.collectors.hyperliquid_leaderboard import fetch_leaderboard_wallets
from src.utils.io import ensure_directory, load_config

DEFAULT_WINDOWS = ["7d", "30d", "90d"]
CSV_COLUMNS = [
    "wallet_address", "source", "rank", "window", "name",
    "account_value", "account_value_usd",
    "pnl", "roi", "roi_pct", "volume", "notes",
]


def _parse_usd(val: str) -> float:
    """Parse '$12.3K' -> 12300, '$5.4M' -> 5400000, '-' -> 0."""
    if not val or val == "-":
        return 0.0
    val = str(val)
    val = val.replace("$", "").replace(",", "").strip()
    multiplier = 1
    if val.upper().endswith("K"):
        multiplier = 1000
        val = val[:-1]
    elif val.upper().endswith("M"):
        multiplier = 1000000
        val = val[:-1]
    elif val.upper().endswith("B"):
        multiplier = 1000000000
        val = val[:-1]
    try:
        return float(val) * multiplier
    except (ValueError, TypeError):
        return 0.0


def _parse_pct(val: str) -> float:
    """Parse '+54.2%' -> 54.2, '-12.3%' -> -12.3."""
    if not val or val == "-":
        return 0.0
    val = str(val)
    val = val.replace("%", "").replace("+", "").replace(",", "").strip()
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _normalise_address(address: str) -> str:
    return str(address or "").strip().lower()


def _load_existing_addresses(path: Path) -> set[str]:
    if not path.exists():
        return set()
    df = pd.read_csv(path)
    if df.empty or "wallet_address" not in df.columns:
        return set()
    return {_normalise_address(value) for value in df["wallet_address"].dropna() if _normalise_address(value)}


def _best_rows_by_address(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        address = _normalise_address(row.get("address", ""))
        if not address:
            continue
        current = best.get(address)
        if current is None or int(row.get("rank", 999999)) < int(current.get("rank", 999999)):
            best[address] = row
    return best


def _append_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_directory(path.parent)
    if path.exists() and path.stat().st_size > 0:
        existing_df = pd.read_csv(path)
        for column in CSV_COLUMNS:
            if column not in existing_df.columns:
                existing_df[column] = ""
        existing_rows = existing_df[CSV_COLUMNS].to_dict("records")
    else:
        existing_rows = []

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in [*existing_rows, *rows]:
            writer.writerow(row)


def _candidate_row(row: dict[str, Any], window: str) -> dict[str, Any]:
    """Build a candidate dictionary from a leaderboard row."""
    account_value_usd = _parse_usd(row.get("account_value", ""))
    roi_pct = _parse_pct(row.get("roi", ""))
    return {
        "wallet_address": _normalise_address(row.get("address", "")),
        "source": "hyperliquid_leaderboard",
        "rank": row.get("rank", 0),
        "window": window,
        "name": row.get("name", ""),
        "account_value": row.get("account_value", ""),
        "account_value_usd": account_value_usd,
        "pnl": row.get("pnl", ""),
        "roi": row.get("roi", ""),
        "roi_pct": roi_pct,
        "volume": row.get("volume", ""),
        "notes": f"window={window}, rank={row.get('rank', 0)}",
    }


def collect_leaderboard_candidates(config: dict[str, Any] | None = None) -> dict[str, int]:
    config = config or load_config("config.yaml")
    hyper_cfg = config.get("hyperliquid", {})
    leaderboard_cfg = hyper_cfg.get("leaderboard_collection", {})
    if not leaderboard_cfg.get("enabled", True):
        print("[leaderboard] Collection disabled in config.")
        return {"collected": 0, "new": 0, "existing": 0}

    windows = leaderboard_cfg.get("windows", DEFAULT_WINDOWS)
    max_rank = int(leaderboard_cfg.get("max_rank", 200))
    output_path = Path(hyper_cfg.get("candidate_wallets_file", "data/candidate_hyperliquid_wallets.csv"))

    all_rows: list[dict[str, Any]] = []
    for window in windows:
        print(f"[leaderboard] Collecting {window} leaderboard through rank {max_rank} ...")
        window_rows = fetch_leaderboard_wallets(str(window), max_rank=max_rank)
        print(f"[leaderboard] {window}: collected {len(window_rows)} public wallet rows")
        all_rows.extend(window_rows)

    all_raw_rows = list(all_rows)

    # Filter by account value (small retail wallets only)
    min_value = float(leaderboard_cfg.get("min_account_value_usd", 1_000))
    max_value = float(leaderboard_cfg.get("max_account_value_usd", 250_000))
    before_filter = len(all_rows)
    all_rows = [
        row for row in all_rows
        if min_value <= _parse_usd(row.get("account_value", "")) <= max_value
    ]
    print(
        f"[leaderboard] Account value filter: {before_filter} -> {len(all_rows)} rows "
        f"(range: ${min_value:,.0f}-${max_value:,.0f})"
    )

    best_by_address = _best_rows_by_address(all_rows)
    raw_path = "data/hyperliquid_leaderboard_rows.csv"
    if all_raw_rows:
        pd.DataFrame(all_raw_rows).to_csv(raw_path, index=False)
        print(f"[leaderboard] Raw leaderboard ({len(all_raw_rows)} rows) -> {raw_path}")

    existing = _load_existing_addresses(output_path)
    new_candidates = [
        _candidate_row(row, str(row.get("window", "")))
        for address, row in sorted(best_by_address.items(), key=lambda item: int(item[1].get("rank", 999999)))
        if address not in existing
    ]

    _append_rows(output_path, new_candidates)
    existing_count = len(best_by_address) - len(new_candidates)
    print(
        "[leaderboard] Summary: "
        f"{len(all_rows)} collected rows, "
        f"{len(best_by_address)} unique addresses, "
        f"{len(new_candidates)} new, "
        f"{existing_count} already present"
    )
    print(f"[leaderboard] Candidate CSV -> {output_path}")
    return {"collected": len(all_rows), "new": len(new_candidates), "existing": existing_count}


def main() -> None:
    collect_leaderboard_candidates()


if __name__ == "__main__":
    main()
