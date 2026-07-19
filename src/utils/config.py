"""Configuration validation and structured access."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HyperliquidConfig:
    enabled: bool = True
    min_trades: int = 30
    lookback_days: int = 90
    candidate_wallets_file: str = "data/candidate_hyperliquid_wallets.csv"
    fills_output_file: str = "data/hyperliquid_fills.parquet"
    rate_limit_delay: float = 3.5

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HyperliquidConfig:
        return cls(
            enabled=bool(data.get("enabled", True)),
            min_trades=int(data.get("min_trades", 30)),
            lookback_days=int(data.get("lookback_days", 90)),
            candidate_wallets_file=str(data.get("candidate_wallets_file", cls.candidate_wallets_file)),
            fills_output_file=str(data.get("fills_output_file", cls.fills_output_file)),
            rate_limit_delay=float(data.get("rate_limit_delay", cls.rate_limit_delay)),
        )


@dataclass
class LeaderboardConfig:
    enabled: bool = False
    max_rank: int = 200
    windows: list[str] = field(default_factory=lambda: ["7d", "30d", "90d"])
    run_before_scoring: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LeaderboardConfig:
        return cls(
            enabled=bool(data.get("enabled", False)),
            max_rank=int(data.get("max_rank", 200)),
            windows=list(data.get("windows", cls().windows)),
            run_before_scoring=bool(data.get("run_before_scoring", True)),
        )


@dataclass
class YieldConfig:
    enabled: bool = True
    min_tvl_usd: float = 5_000_000
    min_apy: float = 5.0
    max_apy: float = 40.0
    chains: list[str] = field(default_factory=list)
    prefer_assets: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> YieldConfig:
        return cls(
            enabled=bool(data.get("enabled", True)),
            min_tvl_usd=float(data.get("min_tvl_usd", 5_000_000)),
            min_apy=float(data.get("min_apy", 5)),
            max_apy=float(data.get("max_apy", 40)),
            chains=list(data.get("chains", [])),
            prefer_assets=list(data.get("prefer_assets", [])),
        )


@dataclass
class EventDetectionConfig:
    enabled: bool = False
    symbols: list[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL"])
    lookback_days: int = 7
    thresholds: dict[str, float] = field(default_factory=lambda: {
        "sharp_drop": -5.0,
        "sharp_rise": 5.0,
        "major_drop": -10.0,
        "major_rise": 10.0,
    })

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventDetectionConfig:
        return cls(
            enabled=bool(data.get("enabled", False)),
            symbols=list(data.get("symbols", cls().symbols)),
            lookback_days=int(data.get("lookback_days", 7)),
            thresholds=dict(data.get("thresholds", cls().thresholds)),
        )


@dataclass
class DiscoveryConfig:
    enabled: bool = False
    db_path: str = "data/onchain_wallets.sqlite"
    event_detection: EventDetectionConfig = field(default_factory=EventDetectionConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscoveryConfig:
        return cls(
            enabled=bool(data.get("enabled", False)),
            db_path=str(data.get("db_path", cls.db_path)),
            event_detection=EventDetectionConfig.from_dict(data.get("event_detection", {})),
        )


@dataclass
class RunConfig:
    timezone: str = "UTC"
    lookback_days: int = 90
    output_dir: str = "reports"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunConfig:
        return cls(
            timezone=str(data.get("timezone", "UTC")),
            lookback_days=int(data.get("lookback_days", 90)),
            output_dir=str(data.get("output_dir", "reports")),
        )


@dataclass
class ScoringConfig:
    gaming_resistance_enabled: bool = True
    strategy_complexity_enabled: bool = True
    cross_pool_exposure_enabled: bool = True
    crowding_penalty_enabled: bool = True
    thresholds: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScoringConfig:
        return cls(
            gaming_resistance_enabled=bool(data.get("gaming_resistance_enabled", True)),
            strategy_complexity_enabled=bool(data.get("strategy_complexity_enabled", True)),
            cross_pool_exposure_enabled=bool(data.get("cross_pool_exposure_enabled", True)),
            crowding_penalty_enabled=bool(data.get("crowding_penalty_enabled", True)),
            thresholds={
                str(name): float(value)
                for name, value in data.get("thresholds", {}).items()
            },
        )


@dataclass
class NotificationConfig:
    """Webhook notification settings (optional)."""
    enabled: bool = False
    webhook_url: str = ""
    on_completion: bool = True
    on_error: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NotificationConfig:
        return cls(
            enabled=bool(data.get("enabled", False)),
            webhook_url=str(data.get("webhook_url", "")),
            on_completion=bool(data.get("on_completion", True)),
            on_error=bool(data.get("on_error", True)),
        )


@dataclass
class PipelineConfig:
    """Top-level validated configuration for the entire pipeline."""
    run: RunConfig = field(default_factory=RunConfig)
    hyperliquid: HyperliquidConfig = field(default_factory=HyperliquidConfig)
    leaderboard: LeaderboardConfig = field(default_factory=LeaderboardConfig)
    yield_: YieldConfig = field(default_factory=YieldConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    notification: NotificationConfig = field(default_factory=NotificationConfig)

    # Keep raw dict for backward compatibility with code that reads config["..."]
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PipelineConfig:
        """Parse and validate a raw config dict (from YAML)."""
        hyper_raw = data.get("hyperliquid", {})
        return cls(
            run=RunConfig.from_dict(data.get("run", {})),
            hyperliquid=HyperliquidConfig.from_dict(hyper_raw),
            leaderboard=LeaderboardConfig.from_dict(hyper_raw.get("leaderboard_collection", {})),
            yield_=YieldConfig.from_dict(data.get("yield", {})),
            discovery=DiscoveryConfig.from_dict(data.get("discovery", {})),
            scoring=ScoringConfig.from_dict(data.get("scoring", {})),
            notification=NotificationConfig.from_dict(data.get("notification", {})),
            _raw=data,
        )

    @property
    def raw(self) -> dict[str, Any]:
        """Access the original unvalidated config dict for backward compatibility."""
        return self._raw
