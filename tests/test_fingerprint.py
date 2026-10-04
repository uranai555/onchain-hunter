from __future__ import annotations

import pandas as pd
import yaml

from src.analysis.fingerprint import (
    extract_bot_indicators,
    extract_coin_profile,
    extract_direction_bias,
    extract_entry_exit,
    extract_fingerprint,
    extract_performance,
    extract_position_sizing,
    extract_risk_management,
    extract_time_patterns,
)
from src.analysis.report import generate_fingerprint_report


def _fills(rows: int = 24, wallet: str = "0xabc", interval_seconds: int = 60) -> pd.DataFrame:
    times = pd.date_range("2026-07-01T00:00:00Z", periods=rows, freq=f"{interval_seconds}s")
    dirs = (["Open Long", "Close Long", "Open Short", "Close Short"] * ((rows // 4) + 1))[:rows]
    coins = (["BTC", "ETH", "xyz:GOLD"] * ((rows // 3) + 1))[:rows]
    side = ["B" if direction in {"Open Long", "Close Short", "Buy"} else "A" for direction in dirs]
    pnl = [10.0 if "Close" in direction and idx % 3 else -4.0 if "Close" in direction else 0.0 for idx, direction in enumerate(dirs)]
    return pd.DataFrame(
        {
            "coin": coins,
            "px": [100.0 + idx for idx in range(rows)],
            "sz": [1.0 if idx < rows // 2 else 2.5 for idx in range(rows)],
            "side": side,
            "time": (times.view("int64") // 1_000_000).astype("int64"),
            "startPosition": ["0"] * rows,
            "dir": dirs,
            "closedPnl": pnl,
            "hash": [f"0x{idx:064x}" for idx in range(rows)],
            "oid": [idx // 2 for idx in range(rows)],
            "crossed": [idx % 2 == 0 for idx in range(rows)],
            "fee": [0.05] * rows,
            "tid": list(range(rows)),
            "feeToken": ["USDC"] * rows,
            "twapId": [None] * rows,
            "wallet_address": [wallet] * rows,
            "cloid": [""] * rows,
            "liquidation": [None] * rows,
            "datetime": times,
        }
    )


def test_extract_fingerprint_has_all_sections():
    fp = extract_fingerprint(_fills(32), "0xabc")

    assert fp["wallet"] == "0xabc"
    assert fp["fingerprint_version"] == 1
    assert set(fp) >= {
        "time_patterns",
        "coin_profile",
        "position_sizing",
        "direction_bias",
        "entry_exit",
        "performance",
        "risk_management",
        "event_response",
        "bot_indicators",
    }


def test_extractors_return_expected_ranges():
    fills = _fills(36)

    time_patterns = extract_time_patterns(fills)
    coin_profile = extract_coin_profile(fills)
    sizing = extract_position_sizing(fills)
    direction = extract_direction_bias(fills)
    entry_exit = extract_entry_exit(fills)
    performance = extract_performance(fills)
    risk = extract_risk_management(fills)

    assert 0 <= time_patterns["hour_entropy"] <= 1
    assert 0 <= time_patterns["weekday_concentration"] <= 1
    assert 0 <= coin_profile["top3_concentration"] <= 1
    assert 0 <= coin_profile["rotation_score"] <= 1
    assert sizing["sizing_style"] in {"fixed", "variable", "scaling", "martingale"}
    assert sizing["avg_position_usd"] > 0
    assert 0 <= direction["long_ratio"] <= 1
    assert 0 <= direction["short_ratio"] <= 1
    assert 0 <= entry_exit["market_order_ratio"] <= 1
    assert entry_exit["avg_hold_ms"] >= 0
    assert performance["total_trades"] == 36
    assert performance["best_coin"]
    assert risk["liquidation_count"] == 0


def test_bot_pattern_detection_for_regular_same_second_trades():
    fills = _fills(20, interval_seconds=1)
    fills.loc[1::2, "datetime"] = fills.loc[0::2, "datetime"].to_numpy()
    fills["time"] = (fills["datetime"].astype("int64") // 1_000_000).astype("int64")
    fills["sz"] = 1.0
    fills["coin"] = "BTC"

    bot = extract_bot_indicators(fills)

    assert bot["sub_second_trades_ratio"] > 0
    assert bot["same_second_multiple_ratio"] > 0
    assert bot["round_lot_bias"] == 1.0
    assert 0 <= bot["automation_likelihood"] <= 1
    assert bot["automation_likelihood"] > 0.4


def test_empty_and_one_trade_edges_do_not_crash():
    empty_fp = extract_fingerprint(pd.DataFrame(), "0xempty")
    one = _fills(1)

    assert empty_fp["performance"]["total_trades"] == 0
    assert extract_entry_exit(one)["avg_hold_ms"] == 0.0
    assert extract_direction_bias(one)["directional_consistency"] == 1.0


def test_skips_empty_wallet_address():
    fills = _fills(4)
    fills.loc[:, "wallet_address"] = ""

    fp = extract_fingerprint(fills, "0xabc")

    assert fp["performance"]["total_trades"] == 0


def test_generate_fingerprint_report_writes_yaml_index_and_comparisons(tmp_path):
    fp = extract_fingerprint(_fills(12), "0xabc")
    generate_fingerprint_report([fp], str(tmp_path))

    yaml_path = tmp_path / "0xabc.fingerprint.yaml"
    assert yaml_path.exists()
    loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert loaded["wallet"] == "0xabc"
    assert (tmp_path / "index.md").exists()
    assert (tmp_path / "comparison" / "sizing_comparison.csv").exists()
    assert (tmp_path / "comparison" / "time_patterns_comparison.csv").exists()
    assert (tmp_path / "comparison" / "coin_preference_comparison.csv").exists()
