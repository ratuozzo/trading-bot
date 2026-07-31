"""Bybit v5 public WebSocket feed (free, no key).

Bybit's linear perpetuals are a good scalping feed: ``publicTrade`` pushes
every execution, and ``orderbook.1`` pushes top-of-book at ~10ms for linear
contracts. Perps generally lead spot in price discovery.

Docs: https://bybit-exchange.github.io/docs/v5/ws/connect
"""

from __future__ import annotations

from typing import Any, List, Optional

from ..models import Tick
from .ws_base import SubscribeWebSocketFeed

_LINEAR_URL = "wss://stream.bybit.com/v5/public/linear"
_SPOT_URL = "wss://stream.bybit.com/v5/public/spot"


class BybitFeed(SubscribeWebSocketFeed):
    name = "bybit"

    def __init__(
        self, symbol: str = "BTCUSDT", stream: str = "trade", market: str = "linear"
    ) -> None:
        self.symbol = symbol.upper()
        self.stream_type = stream
        self.url = _SPOT_URL if market == "spot" else _LINEAR_URL
        self._bid: Optional[float] = None
        self._ask: Optional[float] = None

    def _topic(self) -> str:
        if self.stream_type == "trade":
            return f"publicTrade.{self.symbol}"
        return f"orderbook.1.{self.symbol}"

    def _subscribe_payload(self) -> List[Any]:
        return [{"op": "subscribe", "args": [self._topic()]}]

    def _parse(self, msg: dict) -> Optional[Tick]:
        topic = msg.get("topic", "")
        data = msg.get("data")
        if not topic or data is None:
            return None

        if topic.startswith("publicTrade"):
            # data is a list of trades; use the most recent.
            if not isinstance(data, list) or not data:
                return None
            last = data[-1]
            try:
                return Tick(
                    symbol=last.get("s", self.symbol),
                    price=float(last["p"]),
                    quantity=float(last.get("v", 0.0)),
                    timestamp=float(last["T"]) / 1000.0,
                )
            except (KeyError, ValueError, TypeError):
                return None

        if topic.startswith("orderbook.1"):
            if not isinstance(data, dict):
                return None
            self._bid = _top_of(data.get("b"), self._bid)
            self._ask = _top_of(data.get("a"), self._ask)
            if self._bid is None or self._ask is None:
                return None
            return Tick(
                symbol=data.get("s", self.symbol),
                price=(self._bid + self._ask) / 2.0,
                bid=self._bid,
                ask=self._ask,
                timestamp=float(msg.get("ts", 0)) / 1000.0,
            )
        return None


def _top_of(levels, fallback: Optional[float]) -> Optional[float]:
    """Read the best price from a [[price, size], ...] delta, keeping the old
    value when the delta doesn't touch that side."""
    if not levels:
        return fallback
    try:
        price, size = float(levels[0][0]), float(levels[0][1])
    except (IndexError, ValueError, TypeError):
        return fallback
    if size == 0:  # level removed; wait for the next update
        return fallback
    return price
