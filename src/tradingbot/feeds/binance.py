"""Binance public WebSocket feed (spot and USD-M futures).

Binance exposes free, no-authentication market-data streams:

* ``<symbol>@trade``      - every executed trade
* ``<symbol>@aggTrade``   - aggregated trades (futures; fewer, denser messages)
* ``<symbol>@bookTicker`` - best bid/ask, pushed on every change

Prefer ``market="futures"``: perpetual futures lead spot in price discovery
because leveraged flow arrives there first, so impulses show up sooner.

Docs: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams

The feed auto-reconnects with exponential backoff so a dropped connection
does not kill the bot.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import websockets

from ..models import Tick
from .base import PriceFeed

log = logging.getLogger(__name__)

_SPOT_URL = "wss://stream.binance.com:9443/ws"
_FUTURES_URL = "wss://fstream.binance.com/ws"
_VALID_STREAMS = {"trade", "aggTrade", "bookTicker"}


class BinanceWebSocketFeed(PriceFeed):
    def __init__(
        self,
        symbol: str = "btcusdt",
        stream: str = "trade",
        market: str = "spot",
    ) -> None:
        if stream not in _VALID_STREAMS:
            raise ValueError(
                f"Unsupported stream {stream!r}. Choose one of {sorted(_VALID_STREAMS)}."
            )
        if market not in {"spot", "futures"}:
            raise ValueError(f"market must be 'spot' or 'futures', got {market!r}.")
        self.symbol = symbol.lower()
        self.stream_type = stream
        self.market = market
        base = _FUTURES_URL if market == "futures" else _SPOT_URL
        self.url = f"{base}/{self.symbol}@{stream}"

    async def stream(self) -> AsyncIterator[Tick]:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(
                    self.url, ping_interval=20, ping_timeout=20
                ) as ws:
                    log.info("Connected to %s", self.url)
                    backoff = 1.0  # reset after a successful connect
                    async for raw in ws:
                        tick = self._parse(raw)
                        if tick is not None:
                            yield tick
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any drop
                log.warning("Feed error (%s); reconnecting in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _parse(self, raw: str) -> Tick | None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return None

        if self.stream_type in ("trade", "aggTrade"):
            try:
                return Tick(
                    symbol=msg["s"],
                    price=float(msg["p"]),
                    quantity=float(msg.get("q", 0.0)),
                    timestamp=float(msg["T"]) / 1000.0,
                )
            except (KeyError, ValueError):
                return None

        # bookTicker: best bid/ask
        try:
            bid = float(msg["b"])
            ask = float(msg["a"])
            return Tick(
                symbol=msg["s"],
                price=(bid + ask) / 2.0,
                bid=bid,
                ask=ask,
                quantity=float(msg.get("B", 0.0)),
                timestamp=_event_time(msg),
            )
        except (KeyError, ValueError):
            return None


def _event_time(msg: dict) -> float:
    # bookTicker does not always carry an event time; fall back to now.
    if "E" in msg:
        return float(msg["E"]) / 1000.0
    import time

    return time.time()
