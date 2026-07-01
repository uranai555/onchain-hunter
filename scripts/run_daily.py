from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.collectors.hyperliquid import fetch_all_wallets
from src.reports.markdown import generate_csv, generate_daily_report
from src.scoring.filters import apply_exclusion_filters
from src.scoring.perp_score import perp_score_v2
from src.utils.io import ensure_directory, load_config, write_text


def main() -> None:
    config = load_config("config.yaml")
    output_dir = Path(config.get("run", {}).get("output_dir", "reports"))
    ensure_directory(output_dir)
    ensure_directory("data")

    fills_df = fetch_all_wallets(config)
    scores_df = perp_score_v2(fills_df, config)
    filtered_df = apply_exclusion_filters(scores_df, config)

    report = generate_daily_report(filtered_df, config)
    write_text(output_dir / "hyperliquid_top_wallets_daily.md", report)
    generate_csv(filtered_df, str(output_dir / "hyperliquid_wallet_profiles.csv"))
    filtered_df.to_parquet(output_dir / "hyperliquid_wallet_profiles.parquet", index=False)


if __name__ == "__main__":
    main()
