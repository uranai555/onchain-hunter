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
CSV_COLUMNS = ["wallet_address", "source", "rank", "name", "notes"]


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
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _candidate_row(row: dict[str, Any]) -> dict[str, str]:
    notes = (
        f"window={row.get('window', '')}; "
        f"account_value={row.get('account_value', '')}; "
        f"pnl={row.get('pnl', '')}; "
        f"roi={row.get('roi', '')}; "
        f"volume={row.get('volume', '')}"
    )
    return {
        "wallet_address": _normalise_address(row.get("address", "")),
        "source": "hyperliquid_leaderboard",
        "rank": str(row.get("rank", "")),
        "name": str(row.get("name", "") or ""),
        "notes": notes,
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

    best_by_address = _best_rows_by_address(all_rows)
    existing = _load_existing_addresses(output_path)
    new_candidates = [
        _candidate_row(row)
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
