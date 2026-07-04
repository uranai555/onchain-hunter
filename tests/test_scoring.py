"""Unit tests for scoring utilities and perp_score logic."""

from __future__ import annotations

import pandas as pd

from src.utils.scoring import clip_score, inverse_linear_score, linear_score, safe_ratio


class TestClipScore:
    def test_normal_value(self):
        assert clip_score(50.0) == 50.0

    def test_below_zero(self):
        assert clip_score(-10.0) == 0.0

    def test_above_hundred(self):
        assert clip_score(150.0) == 100.0

    def test_nan(self):
        assert clip_score(float("nan")) == 0.0

    def test_inf(self):
        assert clip_score(float("inf")) == 0.0

    def test_negative_inf(self):
        assert clip_score(float("-inf")) == 0.0

    def test_zero(self):
        assert clip_score(0.0) == 0.0

    def test_hundred(self):
        assert clip_score(100.0) == 100.0


class TestLinearScore:
    def test_midpoint(self):
        assert linear_score(50, 0, 100) == 50.0

    def test_at_low(self):
        assert linear_score(0, 0, 100) == 0.0

    def test_at_high(self):
        assert linear_score(100, 0, 100) == 100.0

    def test_below_low(self):
        assert linear_score(-10, 0, 100) == 0.0

    def test_above_high(self):
        assert linear_score(200, 0, 100) == 100.0

    def test_same_low_high(self):
        assert linear_score(50, 50, 50) == 0.0


class TestInverseLinearScore:
    def test_at_low(self):
        assert inverse_linear_score(0, 0, 100) == 100.0

    def test_at_high(self):
        assert inverse_linear_score(100, 0, 100) == 0.0

    def test_midpoint(self):
        assert inverse_linear_score(50, 0, 100) == 50.0

    def test_same_low_high(self):
        assert inverse_linear_score(10, 10, 10) == 50.0


class TestSafeRatio:
    def test_normal(self):
        assert safe_ratio(10.0, 2.0) == 5.0

    def test_zero_denominator(self):
        assert safe_ratio(10.0, 0.0) == 0.0

    def test_zero_numerator(self):
        assert safe_ratio(0.0, 5.0) == 0.0


class TestPerpScoreV2:
    def test_empty_dataframe(self):
        from src.scoring.perp_score import perp_score_v2

        df = pd.DataFrame()
        result = perp_score_v2(df)
        assert result.empty

    def test_missing_wallet_column(self):
        from src.scoring.perp_score import perp_score_v2

        df = pd.DataFrame({"price": [100, 200]})
        result = perp_score_v2(df)
        assert result.empty

    def test_basic_scoring(self):
        from src.scoring.perp_score import perp_score_v2

        # Create minimal valid fills data
        fills = pd.DataFrame({
            "wallet_address": ["0xabc"] * 50,
            "closedPnl": [100.0] * 30 + [-50.0] * 20,
            "px": [50000.0] * 50,
            "sz": [0.1] * 50,
            "coin": ["BTC"] * 50,
            "side": ["Buy"] * 30 + ["Sell"] * 20,
            "time": list(range(1700000000000, 1700000000000 + 50 * 3600000, 3600000)),
        })
        fills["datetime"] = pd.to_datetime(fills["time"], unit="ms", utc=True)

        result = perp_score_v2(fills)
        assert len(result) == 1
        assert result.iloc[0]["wallet_address"] == "0xabc"
        assert 0 <= result.iloc[0]["perp_score_v2"] <= 100
        assert result.iloc[0]["trade_count"] == 50
