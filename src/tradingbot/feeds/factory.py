"""Build a feed from config."""

from __future__ import annotations

from ..config import FeedConfig
from .base import PriceFeed
from .binance import BinanceWebSocketFeed
from .bybit import BybitFeed
from .okx import OKXFeed


def build_feed(cfg: FeedConfig) -> PriceFeed:
    exchange = cfg.exchange.lower()
    if exchange == "binance":
        return BinanceWebSocketFeed(
            symbol=cfg.symbol, stream=cfg.stream, market=cfg.market
        )
    if exchange == "bybit":
        return BybitFeed(symbol=cfg.symbol, stream=cfg.stream, market=cfg.market)
    if exchange == "okx":
        return OKXFeed(symbol=cfg.symbol, stream=cfg.stream)
    raise ValueError(
        f"Unknown exchange {cfg.exchange!r}. Supported: binance, bybit, okx."
    )
