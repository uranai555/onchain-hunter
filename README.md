# Onchain Smart Money Hunter

Pipeline for finding on-chain wallets that show repeatable, copyable trading behavior rather than only headline PnL.

## Phases

- **Phase 1** Hyperliquid perp wallet discovery
- **Phase 2** DeFi yield discovery with DefiLlama
- **Phase 3** Emerging-chain and DEX wallet discovery

## Outputs

- `reports/hyperliquid_top_wallets_daily.md` - daily Hyperliquid wallet report
- `reports/hyperliquid_wallet_profiles.csv` - scored wallet profiles
- `reports/defi_yield_watchlist.md` - DeFi yield candidates
- `reports/discovery_report.md` - event-driven wallet discovery report
- `reports/copyability_backtest.md` - copyability validation

## Setup

```bash
cp .env.example .env
pip install -r requirements.txt
```

Install Playwright's browser runtime when collecting the Hyperliquid leaderboard:

```bash
python -m playwright install chromium
```

## Run

```bash
python -m scripts.run_daily
python -m scripts.run_daily --dry-run
```

## Config

Use `config.yaml` to control collection surfaces, thresholds, output paths, and notifications.
