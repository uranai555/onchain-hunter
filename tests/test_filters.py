"""Unit tests for exclusion filters."""

from __future__ import annotations

import pandas as pd

from src.scoring.filters import _append_reason, _append_reasons, apply_exclusion_filters


class TestAppendReason:
    def test_empty_existing(self):
        assert _append_reason("", "新しい理由") == "新しい理由"

    def test_none_existing(self):
        assert _append_reason(None, "新しい理由") == "新しい理由"

    def test_no_duplicate(self):
        result = _append_reason("理由A; 理由B", "理由C")
        assert result == "理由A; 理由B; 理由C"

    def test_skip_duplicate(self):
        result = _append_reason("理由A; 理由B", "理由A")
        assert result == "理由A; 理由B"


class TestAppendReasons:
    def test_merge_new_reasons(self):
        result = _append_reasons("理由A", "理由B; 理由C")
        assert "理由A" in result
        assert "理由B" in result
        assert "理由C" in result

    def test_deduplicate(self):
        result = _append_reasons("理由A; 理由B", "理由A; 理由C")
        parts = [p.strip() for p in result.split(";")]
        assert len(parts) == 3  # A, B, C


class TestApplyExclusionFilters:
    def _make_scores_df(self, **overrides) -> pd.DataFrame:
        defaults = {
            "wallet_address": "0xabcdef1234567890abcdef1234567890abcdef12",
            "trade_count": 50,
            "profit_factor": 2.0,
            "max_coin_profit_share": 0.3,
            "unrealized_loss_to_profit": 0.1,
            "perp_score_v2": 70.0,
            "excluded": False,
            "exclusion_reasons": "",
        }
        defaults.update(overrides)
        return pd.DataFrame([defaults])

    def test_no_exclusion(self):
        df = self._make_scores_df()
        config = {"hyperliquid": {"min_trades": 30}, "filters": {"exclude_cex_wallets": True}}
        result = apply_exclusion_filters(df, config)
        assert not result.iloc[0]["excluded"]

    def test_low_trade_count(self):
        df = self._make_scores_df(trade_count=10)
        config = {"hyperliquid": {"min_trades": 30}, "filters": {}}
        result = apply_exclusion_filters(df, config)
        assert result.iloc[0]["excluded"]
        assert "取引回数不足" in result.iloc[0]["exclusion_reasons"]

    def test_low_profit_factor(self):
        df = self._make_scores_df(profit_factor=1.0)
        config = {"hyperliquid": {"min_trades": 30}, "filters": {}}
        result = apply_exclusion_filters(df, config)
        assert result.iloc[0]["excluded"]
        assert "プロフィットファクター不足" in result.iloc[0]["exclusion_reasons"]

    def test_high_coin_concentration(self):
        df = self._make_scores_df(max_coin_profit_share=0.7)
        config = {"hyperliquid": {"min_trades": 30}, "filters": {}}
        result = apply_exclusion_filters(df, config)
        assert result.iloc[0]["excluded"]
        assert "単一銘柄依存" in result.iloc[0]["exclusion_reasons"]

    def test_high_unrealized_loss(self):
        df = self._make_scores_df(unrealized_loss_to_profit=0.6)
        config = {"hyperliquid": {"min_trades": 30}, "filters": {}}
        result = apply_exclusion_filters(df, config)
        assert result.iloc[0]["excluded"]
        assert "極端なリスク行動" in result.iloc[0]["exclusion_reasons"]

    def test_cex_wallet(self):
        df = self._make_scores_df(
            wallet_address="0x0000000000000000000000000000000000000000"
        )
        config = {"hyperliquid": {"min_trades": 30}, "filters": {"exclude_cex_wallets": True}}
        result = apply_exclusion_filters(df, config)
        assert result.iloc[0]["excluded"]
        assert "CEXウォレット疑い" in result.iloc[0]["exclusion_reasons"]

    def test_empty_df(self):
        df = pd.DataFrame()
        config = {"hyperliquid": {"min_trades": 30}, "filters": {}}
        result = apply_exclusion_filters(df, config)
        assert result.empty

    def test_multiple_reasons(self):
        df = self._make_scores_df(trade_count=5, profit_factor=0.5)
        config = {"hyperliquid": {"min_trades": 30}, "filters": {}}
        result = apply_exclusion_filters(df, config)
        assert result.iloc[0]["excluded"]
        reasons = result.iloc[0]["exclusion_reasons"]
        assert "取引回数不足" in reasons
        assert "プロフィットファクター不足" in reasons
