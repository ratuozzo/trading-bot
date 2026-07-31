"""Configuration loading.

Config is read from a YAML file (``config.yaml`` by default) and any value
can be overridden by an environment variable. This keeps secrets (API keys)
out of the repo while letting you version-control the strategy settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from typing import Any, Dict

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a declared dependency
    yaml = None


@dataclass
class FeedConfig:
    exchange: str = "mexc"         # mexc | bybit | okx | binance
    symbol: str = "BTC_USDT"       # venue symbol (MEXC uses BTC_USDT)
    stream: str = "trade"          # "trade"/"aggTrade" (prints) or "bookTicker"
    market: str = "futures"        # "futures" (perps lead spot) or "spot"


@dataclass
class StrategyConfig:
    # Shorter lookback = stricter velocity filter: the same threshold has to
    # happen faster. Sub-second values are fine, and are where cascades live.
    lookback_seconds: float = 1.0      # window used to measure the impulse
    allow_shorts: bool = True          # trade downward impulses as well as up
    entry_threshold: float = 0.0025    # ±0.25% over the window triggers entry
    take_profit: float = 0.006         # exit at +0.60% (clears ~18bp costs)
    stop_loss: float = 0.0025          # exit at -0.25%
    reversal_exit: float = 0.0015      # exit if momentum flips by 0.15% quickly
    reversal_window: float = 1.0       # window (s) used to detect a reversal
    max_hold_seconds: float = 45.0     # give up on a stalled position
    cooldown_seconds: float = 3.0      # wait after an exit before re-entering


@dataclass
class RiskConfig:
    order_size_pct: float = 0.95       # fraction of available cash per entry
    min_notional: float = 10.0         # skip orders smaller than this (quote ccy)
    daily_loss_limit_pct: float = 0.05 # stop trading after -5% on the day


@dataclass
class BrokerConfig:
    mode: str = "paper"                # "paper" or "live"
    starting_cash: float = 10_000.0    # paper starting balance (quote currency)
    fee_bps: float = 8.0               # MEXC futures via API: 0.08% taker
    slippage_bps: float = 2.0          # assumed slippage per fill
    # Live-only (loaded from env, never commit these):
    api_key: str = ""
    api_secret: str = ""


@dataclass
class Config:
    feed: FeedConfig = field(default_factory=FeedConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    broker: BrokerConfig = field(default_factory=BrokerConfig)

    @classmethod
    def load(cls, path: str = "config.yaml") -> "Config":
        data: Dict[str, Any] = {}
        if path and os.path.exists(path):
            if yaml is None:
                raise RuntimeError(
                    "PyYAML is required to read config files. Install it with "
                    "`pip install pyyaml` or drop the config file."
                )
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}

        cfg = cls(
            feed=_build(FeedConfig, data.get("feed", {})),
            strategy=_build(StrategyConfig, data.get("strategy", {})),
            risk=_build(RiskConfig, data.get("risk", {})),
            broker=_build(BrokerConfig, data.get("broker", {})),
        )
        _apply_env_overrides(cfg)
        return cfg


def _build(cls, data: Dict[str, Any]):
    """Build a dataclass from a dict, ignoring unknown keys."""
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


# Maps ENV_VAR -> (section, attribute, caster)
_ENV_MAP = {
    "TB_SYMBOL": ("feed", "symbol", str),
    "TB_STREAM": ("feed", "stream", str),
    "TB_EXCHANGE": ("feed", "exchange", str),
    "TB_MARKET": ("feed", "market", str),
    "TB_MODE": ("broker", "mode", str),
    "TB_STARTING_CASH": ("broker", "starting_cash", float),
    "TB_FEE_BPS": ("broker", "fee_bps", float),
    "TB_API_KEY": ("broker", "api_key", str),
    "TB_API_SECRET": ("broker", "api_secret", str),
    "TB_ENTRY_THRESHOLD": ("strategy", "entry_threshold", float),
    "TB_TAKE_PROFIT": ("strategy", "take_profit", float),
    "TB_STOP_LOSS": ("strategy", "stop_loss", float),
    "TB_ORDER_SIZE_PCT": ("risk", "order_size_pct", float),
}


def _apply_env_overrides(cfg: "Config") -> None:
    for env_var, (section, attr, cast) in _ENV_MAP.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        setattr(getattr(cfg, section), attr, cast(raw))
