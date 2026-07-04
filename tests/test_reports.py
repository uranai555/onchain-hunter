"""Tests for user-facing Markdown reports."""

from __future__ import annotations

import pandas as pd

from src.discovery.discovery_report import generate_discovery_report
from src.reports.markdown import generate_daily_report, generate_yield_report

MOJIBAKE_MARKERS = ("繧", "縺", "譁", "窶", "笏", "逅", "譛")


def assert_no_mojibake(text: str) -> None:
    assert not any(marker in text for marker in MOJIBAKE_MARKERS)


def test_daily_report_contains_readable_labels():
    wallets = pd.DataFrame(
        [
            {
                "wallet_address": "0xabc",
                "rank": "A",
                "perp_score_v2": 82.5,
                "trade_style": "trend-following",
                "trade_count": 42,
                "realized_pnl": 1234.5,
                "profit_factor": 2.1,
                "max_drawdown": 120.0,
                "max_trade_profit_share": 0.2,
                "max_coin_profit_share": 0.3,
                "pnl_30d": 1000,
                "pnl_90d": 1200,
                "pnl_180d": 1200,
                "gaming_resistance_score": 70,
                "lot_size_naturalness_score": 80,
                "return_distribution_quality_score": 75,
                "trade_interval_naturalness_score": 65,
                "out_of_sample_survival_score": 90,
                "pnl_concentration_inverse": 80,
                "leverage_tail_risk_inverse": 85,
                "unrealized_loss_to_profit": 0.1,
                "excluded": False,
                "exclusion_reasons": "",
            }
        ]
    )
    report = generate_daily_report(wallets, {})
    assert "# Hyperliquid Perp Winning Wallets Daily Report" in report
    assert "- Trade style: trend-following" in report
    assert_no_mojibake(report)


def test_yield_report_contains_readable_labels():
    yields = pd.DataFrame(
        [
            {
                "project": "Example",
                "symbol": "USDC",
                "chain": "base",
                "tvl_usd": 10_000_000,
                "apy": 8.2,
                "apy_mean_30d": 7.8,
                "apy_stability_score": 90,
                "il_risk": "no",
                "age_days": 365,
                "yield_score": 77,
                "verdict": "Watch",
            }
        ]
    )
    report = generate_yield_report(yields)
    assert "# DeFi Yield Watchlist" in report
    assert "- Chain: base" in report
    assert_no_mojibake(report)


def test_discovery_report_contains_readable_labels():
    new_candidates = pd.DataFrame(
        [
            {
                "wallet_address": "0xabcdef1234567890",
                "source_surface": "event",
                "discovery_reason": "BTC event winner",
                "source_confidence": "medium",
                "raw_score": 80,
            }
        ]
    )
    events = [
        {
            "event_time": "2024-01-01T00:00:00+00:00",
            "symbol": "BTC",
            "event_type": "sharp_rise",
            "price_change_pct": 6.5,
            "description": "BTC +6.5% in 24h",
        }
    ]
    winners = {
        "2024-01-01T00:00:00Z_BTC_sharp_rise": [
            {
                "wallet_address": "0xabcdef1234567890",
                "event_winner_score": 80,
                "pre_positioning_score": 80,
                "direction_alignment_score": 90,
                "execution_score": 70,
                "exit_quality": 75,
                "estimated_pnl": 1234,
                "trade_count_in_window": 3,
            }
        ]
    }
    report = generate_discovery_report(new_candidates, events, winners, [], existing_wallet_count=10)
    assert "# Onchain Wallet Discovery Report" in report
    assert "## Event Winners" in report
    assert_no_mojibake(report)
