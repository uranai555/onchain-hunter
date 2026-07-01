from __future__ import annotations

import pandas as pd


def filter_by_lookback(df: pd.DataFrame, days: int) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    if "datetime" not in out.columns:
        if "time" not in out.columns:
            return out
        out["datetime"] = pd.to_datetime(out["time"], unit="ms", utc=True, errors="coerce")
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    return out[out["datetime"] >= cutoff].copy()


def utc_now_jst_label() -> str:
    now = pd.Timestamp.now(tz="Asia/Tokyo")
    return now.strftime("%Y%m%d")
