from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.collectors.defillama import fetch_filtered_pools, save_yields
from src.collectors.hyperliquid import fetch_all_wallets
from src.reports.markdown import generate_csv, generate_daily_report, generate_yield_report
from src.scoring.filters import apply_exclusion_filters
from src.scoring.perp_score import perp_score_v2
from src.scoring.yield_score import score_yields
from src.utils.io import ensure_directory, load_config, write_text


def main() -> None:
    config = load_config("config.yaml")
    output_dir = Path(config.get("run", {}).get("output_dir", "reports"))
    ensure_directory(output_dir)
    ensure_directory("data")

    # ---- Phase 1: Hyperliquid ----
    if config.get("hyperliquid", {}).get("enabled", True):
        print("[pipeline] Fetching Hyperliquid fills ...")
        fills_df = fetch_all_wallets(config)
        print(f"[pipeline]  {len(fills_df)} fills loaded")

        scores_df = perp_score_v2(fills_df, config)
        filtered_df = apply_exclusion_filters(scores_df, config)

        report = generate_daily_report(filtered_df, config)
        write_text(output_dir / "hyperliquid_top_wallets_daily.md", report)
        generate_csv(filtered_df, str(output_dir / "hyperliquid_wallet_profiles.csv"))
        filtered_df.to_parquet(output_dir / "hyperliquid_wallet_profiles.parquet", index=False)
        print(f"[pipeline] Hyperliquid report -> {output_dir / 'hyperliquid_top_wallets_daily.md'}")
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