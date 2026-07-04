"""Unit tests for event detection logic."""

from __future__ import annotations

import pandas as pd
import pytest

from src.discovery.event_winners import (
    _merge_events,
    detect_events,
    score_wallet_for_event,
)


class TestMergeEvents:
    def test_empty_dataframe(self):
        df = pd.DataFrame(columns=["event_type", "event_time", "symbol", "price_change_pct"])
        result = _merge_events(df)
        assert result.empty

    def test_no_merge_needed(self):
        df = pd.DataFrame([
            {"event_type": "sharp_drop", "event_time": "2024-01-01T00:00:00+00:00", "symbol": "BTC", "price_change_pct": -6.0},
            {"event_type": "sharp_drop", "event_time": "2024-01-02T00:00:00+00:00", "symbol": "BTC", "price_change_pct": -7.0},
        ])
        result = _merge_events(df)
        assert len(result) == 2

    def test_merge_close_events(self):
        df = pd.DataFrame([
            {"event_type": "sharp_drop", "event_time": "2024-01-01T00:00:00+00:00", "symbol": "BTC", "price_change_pct": -6.0},
            {"event_type": "sharp_drop", "event_time": "2024-01-01T03:00:00+00:00", "symbol": "BTC", "price_change_pct": -8.0},
            {"event_type": "sharp_drop", "event_time": "2024-01-01T05:00:00+00:00", "symbol": "BTC", "price_change_pct": -5.5},
        ])
        result = _merge_events(df)
        # Should merge into 1 event (all within 6h window), keep the -8.0 one
        assert len(result) == 1
        assert result.iloc[0]["price_change_pct"] == -8.0

    def test_different_symbols_not_merged(self):
        df = pd.DataFrame([
            {"event_type": "sharp_drop", "event_time": "2024-01-01T00:00:00+00:00", "symbol": "BTC", "price_change_pct": -6.0},
            {"event_type": "sharp_drop", "event_time": "2024-01-01T01:00:00+00:00", "symbol": "ETH", "price_change_pct": -7.0},
        ])
        result = _merge_events(df)
        assert len(result) == 2


class TestScoreWalletForEvent:
    def test_no_fills_in_window(self):
        event_time = pd.Timestamp("2024-01-01T12:00:00+00:00")
        fills = [{"time": 1700000000000, "closedPnl": 100}]  # way before event
        result = score_wallet_for_event(fills, event_time, "rise")
        assert result["trade_count_in_window"] == 0
        assert result["estimated_pnl"] == 0.0

    def test_fills_in_window(self):
        event_time = pd.Timestamp("2024-01-01T12:00:00+00:00")
        # Create fills within the window (6h before to 12h after)
        base_ms = int(event_time.timestamp() * 1000)
        fills = [
            {"time": base_ms - 3600000, "closedPnl": 500, "side": "Buy", "coin": "BTC"},
            {"time": base_ms + 3600000, "closedPnl": 300, "side": "Sell", "coin": "BTC"},
            {"time": base_ms + 7200000, "closedPnl": -100, "side": "Sell", "coin": "BTC"},
        ]
        result = score_wallet_for_event(fills, event_time, "rise")
        assert result["trade_count_in_window"] == 3
        assert result["estimated_pnl"] == 700.0  # 500 + 300 - 100
        assert result["pre_positioning_score"] > 0
        assert result["execution_score"] > 0

    def test_direction_affects_positioning(self):
        event_time = pd.Timestamp("2024-01-01T12:00:00+00:00")
        base_ms = int(event_time.timestamp() * 1000)
        # Wallet was long (buying) before a rise event
        fills = [
            {"time": base_ms - 3600000, "closedPnl": 100, "side": "Buy"},
            {"time": base_ms + 3600000, "closedPnl": 200, "side": "Sell"},
        ]
        rise_result = score_wallet_for_event(fills, event_time, "rise")
        drop_result = score_wallet_for_event(fills, event_time, "drop")
        # Pre-positioning should be better for rise since they were buying
        assert rise_result["pre_positioning_score"] >= drop_result["pre_positioning_score"]


class TestDetectEvents:
    def test_unsupported_symbol(self):
        with pytest.raises(ValueError, match="Unsupported symbol"):
            detect_events("INVALID_COIN", days=7)


class TestConfigValidation:
    def test_pipeline_config_from_dict(self):
        from src.utils.config import PipelineConfig

        raw = {
            "run": {"output_dir": "reports", "lookback_days": 90},
            "hyperliquid": {
                "enabled": True,
                "min_trades": 50,
                "lookback_days": 60,
            },
            "yield": {"enabled": False, "min_tvl_usd": 10_000_000},
            "discovery": {"enabled": True, "event_detection": {"enabled": True, "symbols": ["BTC"]}},
        }
        cfg = PipelineConfig.from_dict(raw)
        assert cfg.hyperliquid.min_trades == 50
        assert cfg.hyperliquid.lookback_days == 60
        assert cfg.yield_.enabled is False
        assert cfg.yield_.min_tvl_usd == 10_000_000
        assert cfg.discovery.enabled is True
        assert cfg.discovery.event_detection.symbols == ["BTC"]
        assert cfg.raw == raw

    def test_defaults_on_empty_dict(self):
        from src.utils.config import PipelineConfig

        cfg = PipelineConfig.from_dict({})
        assert cfg.hyperliquid.enabled is True
        assert cfg.hyperliquid.min_trades == 30
        assert cfg.run.output_dir == "reports"


class TestDefillamaTimestampParsing:
    def test_parse_seconds_epoch(self):
        from src.collectors.defillama import _parse_timestamp

        dt = _parse_timestamp(1700000000)
        assert dt is not None
        assert dt.year == 2023

    def test_parse_milliseconds_epoch(self):
        from src.collectors.defillama import _parse_timestamp

        dt = _parse_timestamp(1700000000000)
        assert dt is not None
        assert dt.year == 2023

    def test_parse_iso_string(self):
        from src.collectors.defillama import _parse_timestamp

        dt = _parse_timestamp("2024-01-15T10:00:00Z")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_parse_invalid(self):
        from src.collectors.defillama import _parse_timestamp

        assert _parse_timestamp("not a date") is None
        assert _parse_timestamp(None) is None
        assert _parse_timestamp([]) is None
