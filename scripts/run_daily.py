from __future__ import annotations

import argparse
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
)
from src.discovery.discovery_report import generate_discovery_report, generate_discovery_report_short
from src.discovery.event_winners import run_event_discovery
from src.reports.markdown import generate_csv, generate_daily_report, generate_yield_report
from src.scoring.filters import apply_exclusion_filters
from src.scoring.perp_score import perp_score_v2
from src.scoring.yield_score import score_yields
from src.utils.config import PipelineConfig
from src.utils.io import ensure_directory, load_config, write_text
from src.utils.logger import get_logger, setup_logging

logger = get_logger("pipeline")


def _fills_df_to_wallet_dict(df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    """Convert a fills DataFrame into {wallet_address: [fill_dicts]} for reuse."""
    if df is None or df.empty or "wallet_address" not in df.columns:
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for addr, group in df.groupby("wallet_address"):
        result[str(addr)] = group.to_dict("records")
    return result


def main(dry_run: bool = False) -> None:
    setup_logging()
    config = load_config("config.yaml")
    pipeline_cfg = PipelineConfig.from_dict(config)
    output_dir = Path(pipeline_cfg.run.output_dir)
    ensure_directory(output_dir)
    ensure_directory("data")

    if dry_run:
        logger.info("DRY-RUN mode: skipping API calls, using cached data only")

    leaderboard_cfg = config.get("hyperliquid", {}).get("leaderboard_collection", {})
    if leaderboard_cfg.get("enabled", False) and leaderboard_cfg.get("run_before_scoring", False):
        if dry_run:
            logger.info("DRY-RUN: Skipping leaderboard collection")
        else:
            try:
                from scripts.collect_leaderboard import collect_leaderboard_candidates

                logger.info("Collecting Hyperliquid leaderboard wallets ...")
                collect_leaderboard_candidates(config)
            except Exception as exc:
                logger.warning("Leaderboard collection failed (non-fatal): %s", exc)
                logger.info("Continuing with existing candidates ...")

    # ---- Phase 1: Hyperliquid fill fetch (runs first so discovery can reuse) ----
    fills_df = pd.DataFrame()
    if config.get("hyperliquid", {}).get("enabled", True):
        if dry_run:
            # In dry-run mode, load from cache only
            from pathlib import Path as _P
            cache_path = _P(pipeline_cfg.hyperliquid.fills_output_file)
            if cache_path.exists():
                fills_df = pd.read_parquet(cache_path)
                logger.info("DRY-RUN: Loaded %d fills from cache", len(fills_df))
            else:
                logger.warning("DRY-RUN: No cached fills found at %s", cache_path)
        else:
            logger.info("Fetching Hyperliquid fills ...")
            fills_df = fetch_all_wallets(config)
            logger.info("%d fills loaded", len(fills_df))

    # ---- Discovery Phase 0: Wallet discovery (event winners, fresh traders) ----
    discovery_cfg = config.get("discovery", {})
    event_cfg = discovery_cfg.get("event_detection", {})
    if discovery_cfg.get("enabled", False) and event_cfg.get("enabled", False):
        logger.info("Initialising discovery DB ...")
        db_path = discovery_cfg.get("db_path", "data/onchain_wallets.sqlite")
        conn = get_connection(db_path)
        try:
            # Import legacy CSV into SQLite (one-time migration)
            csv_path = config.get("hyperliquid", {}).get(
                "candidate_wallets_file", "data/candidate_hyperliquid_wallets.csv"
            )
            migration = import_from_csv(conn, csv_path)
            if migration["imported"] > 0:
                logger.info("Imported %d legacy wallets into SQLite", migration['imported'])

            # Get existing addresses
            addresses = get_all_wallet_addresses(conn)
            logger.info("%d wallet candidates in DB", len(addresses))

            if addresses:
                logger.info("Running event discovery ...")
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

                # Store events and winners in DB (batch commit for performance)
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
                        auto_commit=False,
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
                            auto_commit=False,
                        )
                conn.commit()

                # Generate discovery report

                # Candidates added THIS run (first_seen_at == created_at ≈ today)
                today_str = dt_mod.datetime.now(dt_mod.UTC).strftime("%Y-%m-%d")
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
                logger.info("Discovery report -> %s", output_dir / 'discovery_report.md')
                logger.info(generate_discovery_report_short(
                    result.get("events_found", 0),
                    len(result.get("winners", {})),
                    len(new_this_run),
                    len(result.get("errors", [])),
                ))
            else:
                logger.info("No wallet candidates found — skipping event discovery.")
        finally:
            conn.close()
    else:
        logger.info("Discovery phase disabled in config.")

    # ---- Phase 1 (continued): Hyperliquid scoring ----
    if config.get("hyperliquid", {}).get("enabled", True) and not fills_df.empty:
        scores_df = perp_score_v2(fills_df, config)
        filtered_df = apply_exclusion_filters(scores_df, config)

        report = generate_daily_report(filtered_df, config)
        write_text(output_dir / "hyperliquid_top_wallets_daily.md", report)
        generate_csv(filtered_df, str(output_dir / "hyperliquid_wallet_profiles.csv"))
        filtered_df.to_parquet(output_dir / "hyperliquid_wallet_profiles.parquet", index=False)
        logger.info("Hyperliquid report -> %s", output_dir / 'hyperliquid_top_wallets_daily.md')
    elif config.get("hyperliquid", {}).get("enabled", True):
        logger.info("No Hyperliquid fills to score.")
    else:
        logger.info("Hyperliquid phase disabled in config.")

    # ---- Phase 2: DefiLlama ----
    if config.get("yield", {}).get("enabled", True):
        if dry_run:
            logger.info("DRY-RUN: Skipping DefiLlama fetch")
        else:
            logger.info("Fetching DefiLlama yields ...")
            pools_df = fetch_filtered_pools(config)
            logger.info("%d pools after initial filter", len(pools_df))

            if not pools_df.empty:
                save_yields(pools_df, "data")
                scored_df = score_yields(pools_df, config)
                yield_report = generate_yield_report(scored_df)
                write_text(output_dir / "defi_yield_watchlist.md", yield_report)
                scored_df.to_parquet(output_dir / "defi_yield_scored.parquet", index=False)
                logger.info("DefiLlama report -> %s", output_dir / 'defi_yield_watchlist.md')
            else:
                logger.info("No pools matched DefiLlama filters.")
    else:
        logger.info("DefiLlama phase disabled in config.")

    logger.info("All phases complete.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Onchain Hunter daily pipeline")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Skip API calls and use cached data only",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--log-file", default=None,
        help="Optional log file path",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    setup_logging(level=args.log_level, log_file=args.log_file)
    main(dry_run=args.dry_run)
