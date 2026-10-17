"""Tests for Hyperliquid collector retry/env handling (no live API calls)."""

from __future__ import annotations

import types

import pytest
import requests

import src.collectors.hyperliquid as hl


class TestMaxRetriesEnv:
    def test_zero_still_allows_first_request(self, monkeypatch):
        # HL_MAX_RETRIES=0 must not skip the initial request entirely.
        monkeypatch.setenv("HL_MAX_RETRIES", "0")
        assert hl._max_retries_from_env() == 1

    def test_negative_clamped_to_one(self, monkeypatch):
        monkeypatch.setenv("HL_MAX_RETRIES", "-2")
        assert hl._max_retries_from_env() == 1

    def test_positive_value_preserved(self, monkeypatch):
        monkeypatch.setenv("HL_MAX_RETRIES", "5")
        assert hl._max_retries_from_env() == 5


class TestFetchWithRetry:
    def test_zero_retries_still_sends_first_request(self, monkeypatch):
        monkeypatch.setattr(hl, "MAX_RETRIES", 0)
        calls: list[str] = []
        monkeypatch.setattr(
            hl,
            "fetch_user_fills",
            lambda address, lookback_days: calls.append(address) or [{"fill": 1}],
        )
        assert hl._fetch_with_retry("0xabc", 90) == [{"fill": 1}]
        assert calls == ["0xabc"]

    def test_retries_429_up_to_max_attempts(self, monkeypatch):
        monkeypatch.setattr(hl, "MAX_RETRIES", 3)
        monkeypatch.setattr(hl, "RATE_LIMIT_DELAY", 1.0)
        sleeps: list[float] = []
        monkeypatch.setattr(hl, "time", types.SimpleNamespace(sleep=sleeps.append))
        call_count = 0

        def _always_429(address: str, lookback_days: int) -> list[dict]:
            nonlocal call_count
            call_count += 1
            response = requests.Response()
            response.status_code = 429
            raise requests.HTTPError(response=response)

        monkeypatch.setattr(hl, "fetch_user_fills", _always_429)
        with pytest.raises(requests.HTTPError):
            hl._fetch_with_retry("0xabc", 90)
        assert call_count == 3
        # Backoff: RATE_LIMIT_DELAY * (attempt + 2) for attempts 0 and 1.
        assert sleeps == [2.0, 3.0]

    def test_non_429_http_error_not_retried(self, monkeypatch):
        monkeypatch.setattr(hl, "MAX_RETRIES", 3)
        call_count = 0

        def _always_500(address: str, lookback_days: int) -> list[dict]:
            nonlocal call_count
            call_count += 1
            response = requests.Response()
            response.status_code = 500
            raise requests.HTTPError(response=response)

        monkeypatch.setattr(hl, "fetch_user_fills", _always_500)
        with pytest.raises(requests.HTTPError):
            hl._fetch_with_retry("0xabc", 90)
        assert call_count == 1
