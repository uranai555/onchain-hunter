from __future__ import annotations

import datetime as dt_mod
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.collectors.defillama import fetch_filtered_pools, save_yields
from src.collectors.hyperliquid import fetch_all_wallets
from src.discovery.db import (
    get_all_wallet_addresses,
    get_connection,
    import_from_csv,
    record_event,
    record_winner,
    upsert_candidate,
)
from src.discovery.discovery_report import generate_discovery_report, generate_discovery_report_short
from src.discovery.event_winners import run_event_discovery
from src.reports.markdown import generate_csv, generate_daily_report, generate_yield_report
from src.scoring.filters import apply_exclusion_filters
from src.scoring.perp_score import perp_score_v2
from src.scoring.yield_score import score_yields
from src.utils.io import ensure_directory, load_config, write_text


def _fills_df_to_wallet_dict(df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    """Convert a fills DataFrame into {wallet_address: [fill_dicts]} for reuse."""
    if df is None or df.empty or "wallet_address" not in df.columns:
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for addr, group in df.groupby("wallet_address"):
        result[str(addr)] = group.to_dict("records")
    return result


def main() -> None:
    config = load_config("config.yaml")
    output_dir = Path(config.get("run", {}).get("output_dir", "reports"))
    ensure_directory(output_dir)
    ensure_directory("data")

    leaderboard_cfg = config.get("hyperliquid", {}).get("leaderboard_collection", {})
    if leaderboard_cfg.get("enabled", False) and leaderboard_cfg.get("run_before_scoring", False):
        try:
            from scripts.collect_leaderboard import collect_leaderboard_candidates

            print("[pipeline] Collecting Hyperliquid leaderboard wallets ...")
            collect_leaderboard_candidates(config)
        except Exception as exc:
            print(f"[pipeline]  Leaderboard collection failed (non-fatal): {exc}")
            print("[pipeline]  Continuing with existing candidates ...")

    # ---- Phase 1: Hyperliquid fill fetch (runs first so discovery can reuse) ----
    fills_df = pd.DataFrame()
    if config.get("hyperliquid", {}).get("enabled", True):
        print("[pipeline] Fetching Hyperliquid fills ...")
        fills_df = fetch_all_wallets(config)
        print(f"[pipeline]  {len(fills_df)} fills loaded")

    # ---- Discovery Phase 0: Wallet discovery (event winners, fresh traders) ----
    discovery_cfg = config.get("discovery", {})
    event_cfg = discovery_cfg.get("event_detection", {})
    if discovery_cfg.get("enabled", False) and event_cfg.get("enabled", False):
        print("[pipeline] Initialising discovery DB ...")
        db_path = discovery_cfg.get("db_path", "data/onchain_wallets.sqlite")
        conn = get_connection(db_path)
        try:
            # Import legacy CSV into SQLite (one-time migration)
            csv_path = config.get("hyperliquid", {}).get(
                "candidate_wallets_file", "data/candidate_hyperliquid_wallets.csv"
            )
            migration = import_from_csv(conn, csv_path)
            if migration["imported"] > 0:
                print(f"[pipeline]  Imported {migration['imported']} legacy wallets into SQLite")

            # Get existing addresses
            addresses = get_all_wallet_addresses(conn)
            print(f"[pipeline]  {len(addresses)} wallet candidates in DB")

            if addresses:
                print("[pipeline] Running event discovery ...")
                symbols = event_cfg.get("symbols", ["BTC", "ETH", "SOL"])
                lookback_days = int(event_cfg.get("lookback_days", 7))
                thresholds = event_cfg.get("thresholds") or None

                # Reuse fills already fetched by Phase 1 to avoid duplicate API calls.
                prefetched = _fills_df_to_wallet_dict(fills_df) if not fills_df.empty else None
                result = run_event_discovery(
                    addresses,
                    symbols=symbols,
                    days=lookback_days,
                    thresholds=thresholds,
                    prefetched_fills=prefetched,
                )

                # Store events and winners in DB
                for event in result.get("events", []):
                    event_id = record_event(
                        conn,
                        event_type=event.get("event_type", "unknown"),
                        event_time=event.get("event_time", ""),
                        symbol=event.get("symbol", ""),
                        price_before=event.get("price_before"),
                        price_after=event.get("price_after"),
                        price_change_pct=event.get("price_change_pct"),
                        description=event.get("description"),
                    )
                    # Store winners for this event
                    event_key = f"{event['event_time'][:19]}Z_{event['symbol']}_{event['event_type']}"
                    for winner in result.get("winners", {}).get(event_key, []):
                        record_winner(
                            conn,
                            event_id=event_id,
                            wallet_address=winner.get("wallet_address", ""),
                            pre_positioning_score=winner.get("pre_positioning_score", 0),
                            execution_score=winner.get("execution_score", 0),
                            exit_quality=winner.get("exit_quality", 0),
                            estimated_pnl=winner.get("estimated_pnl", 0),
                            trade_count_in_window=winner.get("trade_count_in_window", 0),
                        )

                # Generate discovery report
                from src.discovery.db import get_candidates

                # Candidates added THIS run (first_seen_at == created_at ≈ today)
                today_str = dt_mod.datetime.now(dt_mod.timezone.utc).strftime("%Y-%m-%d")
                new_this_run = pd.read_sql_query(
                    """SELECT * FROM wallet_candidates
                       WHERE first_seen_at >= ? AND status = 'candidate'
                         AND source_surface != 'legacy_csv'
                       ORDER BY raw_score DESC LIMIT 50""",
                    conn,
                    params=(today_str,),
                )

                discovery_report = generate_discovery_report(
                    new_candidates=new_this_run,
                    events=result.get("events", []),
                    winners=result.get("winners", {}),
                    errors=result.get("errors", []),
                    existing_wallet_count=len(addresses),
                )
                write_text(output_dir / "discovery_report.md", discovery_report)
                print(f"[pipeline] Discovery report -> {output_dir / 'discovery_report.md'}")
                print(generate_discovery_report_short(
                    result.get("events_found", 0),
                    len(result.get("winners", {})),
                    len(new_this_run),
                    len(result.get("errors", [])),
                ))
            else:
                print("[pipeline]  No wallet candidates found — skipping event discovery.")
        finally:
            conn.close()
    else:
        print("[pipeline] Discovery phase disabled in config.")

    # ---- Phase 1 (continued): Hyperliquid scoring ----
    if config.get("hyperliquid", {}).get("enabled", True) and not fills_df.empty:
        scores_df = perp_score_v2(fills_df, config)
        filtered_df = apply_exclusion_filters(scores_df, config)

        report = generate_daily_report(filtered_df, config)
        write_text(output_dir / "hyperliquid_top_wallets_daily.md", report)
        generate_csv(filtered_df, str(output_dir / "hyperliquid_wallet_profiles.csv"))
        filtered_df.to_parquet(output_dir / "hyperliquid_wallet_profiles.parquet", index=False)
        print(f"[pipeline] Hyperliquid report -> {output_dir / 'hyperliquid_top_wallets_daily.md'}")
    elif config.get("hyperliquid", {}).get("enabled", True):
        print("[pipeline] No Hyperliquid fills to score.")
    else:
        print("[pipeline] Hyperliquid phase disabled in config.")

    # ---- Phase 2: DefiLlama ----
    if config.get("yield", {}).get("enabled", True):
        print("[pipeline] Fetching DefiLlama yields ...")
        pools_df = fetch_filtered_pools(config)
        print(f"[pipeline]  {len(pools_df)} pools after initial filter")

        if not pools_df.empty:
            save_yields(pools_df, "data")
            scored_df = score_yields(pools_df, config)
            yield_report = generate_yield_report(scored_df)
            write_text(output_dir / "defi_yield_watchlist.md", yield_report)
            scored_df.to_parquet(output_dir / "defi_yield_scored.parquet", index=False)
            print(f"[pipeline] DefiLlama report -> {output_dir / 'defi_yield_watchlist.md'}")
        else:
            print("[pipeline] No pools matched DefiLlama filters.")
    else:
        print("[pipeline] DefiLlama phase disabled in config.")

    print("[pipeline] All phases complete.")


if __name__ == "__main__":
    main()
