"""Build a feed from config."""

from __future__ import annotations

from ..config import FeedConfig
from .base import PriceFeed
from .binance import BinanceWebSocketFeed


def build_feed(cfg: FeedConfig) -> PriceFeed:
    exchange = cfg.exchange.lower()
    if exchange == "binance":
        return BinanceWebSocketFeed(symbol=cfg.symbol, stream=cfg.stream)
    raise ValueError(
        f"Unknown exchange {cfg.exchange!r}. Only 'binance' is built in so far."
    )
