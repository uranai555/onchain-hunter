"""Unit tests for scoring utilities and perp_score logic."""

from __future__ import annotations

import subprocess
import sys
import types

import pandas as pd
import pytest

from src.utils.scoring import clip_score, inverse_linear_score, linear_score, safe_ratio

# Commit that contains the pre-refactor perp_score implementation (PR base).
_BASE_SCORING_COMMIT = "d568648feb276de65d9c33d3ff39171988310763"


def _deterministic_fills() -> pd.DataFrame:
    """Fixed fills: two wallets, mixed PnL, recent timestamps inside all windows."""
    now_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    hour = 3_600_000
    base = now_ms - 5 * 24 * hour
    fills = pd.DataFrame({
        "wallet_address": ["0xabc"] * 40 + ["0xdef"] * 35,
        "closedPnl": [100.0, -30.0] * 20 + [50.0, -20.0, 80.0] * 11 + [50.0, -20.0],
        "px": [50_000.0] * 40 + [3_000.0] * 35,
        "sz": [0.1, 0.2] * 20 + [1.0, 1.5] * 17 + [1.0],
        "coin": ["BTC", "ETH"] * 20 + ["SOL", "ETH"] * 17 + ["SOL"],
        "side": ["Buy", "Sell"] * 20 + ["Buy", "Sell"] * 17 + ["Buy"],
        "time": [base + i * hour for i in range(75)],
    })
    fills["datetime"] = pd.to_datetime(fills["time"], unit="ms", utc=True)
    return fills


def _load_base_perp_score() -> types.ModuleType:
    """Load the pre-refactor implementation from git into a standalone module."""
    proc = subprocess.run(
        ["git", "show", f"{_BASE_SCORING_COMMIT}:src/scoring/perp_score.py"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip("base-branch scoring source not available in this clone")
    module = types.ModuleType("perp_score_base")
    sys.modules[module.__name__] = module
    exec(compile(proc.stdout, "perp_score_base.py", "exec"), module.__dict__)
    return module


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

    def test_threshold_override_changes_scores(self):
        from src.scoring.perp_score import perp_score_v2

        fills = _deterministic_fills()
        base = perp_score_v2(fills, {"hyperliquid": {"min_trades": 30}})
        overridden = perp_score_v2(
            fills,
            {"scoring": {"thresholds": {"realized_pnl_high": 100.0}}},
        )
        for addr in base["wallet_address"]:
            base_row = base[base["wallet_address"] == addr].iloc[0]
            new_row = overridden[overridden["wallet_address"] == addr].iloc[0]
            # realized_pnl_high=100 pushes every positive-PnL wallet to the cap
            assert new_row["realized_pnl_score"] == 100.0
            assert new_row["realized_pnl_score"] > base_row["realized_pnl_score"]
            assert new_row["perp_score_v2"] > base_row["perp_score_v2"]

    def test_matches_base_implementation(self):
        """The scoring split must be output-identical to the pre-refactor code."""
        from src.scoring.perp_score import perp_score_v2

        base_module = _load_base_perp_score()
        fills = _deterministic_fills()
        config = {"hyperliquid": {"min_trades": 30}}
        expected = base_module.perp_score_v2(fills, config)
        actual = perp_score_v2(fills, config)
        pd.testing.assert_frame_equal(expected, actual, check_exact=True)
