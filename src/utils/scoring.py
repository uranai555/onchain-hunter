"""Shared scoring utility functions used across perp and yield scoring."""

from __future__ import annotations

import numpy as np
import pandas as pd


def clip_score(value: float) -> float:
    """Clip a score to [0, 100], returning 0 for NaN/inf."""
    if pd.isna(value) or not np.isfinite(value):
        return 0.0
    return float(np.clip(value, 0.0, 100.0))


def linear_score(value: float, low: float, high: float) -> float:
    """Map value linearly from [low, high] to [0, 100]."""
    if high == low:
        return 0.0
    return clip_score((value - low) / (high - low) * 100.0)


def inverse_linear_score(value: float, low: float, high: float) -> float:
    """Higher raw value -> lower score (e.g. APY volatility)."""
    if high == low:
        return 50.0
    return clip_score((high - value) / (high - low) * 100.0)


def safe_ratio(numerator: float, denominator: float) -> float:
    """Safe division returning 0 on zero denominator."""
    return float(numerator / denominator) if denominator else 0.0
