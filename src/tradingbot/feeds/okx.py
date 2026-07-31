"""OKX v5 public WebSocket feed (free, no key).

OKX's ``bbo-tbt`` channel is genuinely tick-by-tick top-of-book — one of the
lowest-latency free feeds available — and ``trades`` pushes every execution.
Swap instruments (``BTC-USDT-SWAP``) lead spot in price discovery.

Docs: https://www.okx.com/docs-v5/en/#overview-websocket
"""

from __future__ import annotations

from typing import Any, List, Optional

from ..models import Tick
from .ws_base import SubscribeWebSocketFeed

_URL = "wss://ws.okx.com:8443/ws/v5/public"


class OKXFeed(SubscribeWebSocketFeed):
    name = "okx"
    # OKX closes idle connections after 30s; a raw "ping" string keeps it open.
    keepalive_interval = 20.0
    keepalive_payload = "ping"

    def __init__(self, symbol: str = "BTC-USDT-SWAP", stream: str = "trade") -> None:
        self.symbol = symbol.upper()
        self.stream_type = stream

    @property
    def url(self) -> str:  # type: ignore[override]
        return _URL

    def _channel(self) -> str:
        return "trades" if self.stream_type == "trade" else "bbo-tbt"

    def _subscribe_payload(self) -> List[Any]:
        return [
            {
                "op": "subscribe",
                "args": [{"channel": self._channel(), "instId": self.symbol}],
            }
        ]

    def _parse(self, msg: dict) -> Optional[Tick]:
        arg = msg.get("arg") or {}
        channel = arg.get("channel")
        data = msg.get("data")
        if not channel or not isinstance(data, list) or not data:
            return None
        last = data[-1]

        try:
            if channel == "trades":
                return Tick(
                    symbol=last.get("instId", self.symbol),
                    price=float(last["px"]),
                    quantity=float(last.get("sz", 0.0)),
                    timestamp=float(last["ts"]) / 1000.0,
                )

            if channel == "bbo-tbt":
                bids, asks = last.get("bids") or [], last.get("asks") or []
                if not bids or not asks:
                    return None
                bid, ask = float(bids[0][0]), float(asks[0][0])
                return Tick(
                    symbol=self.symbol,
                    price=(bid + ask) / 2.0,
                    bid=bid,
                    ask=ask,
                    timestamp=float(last["ts"]) / 1000.0,
                )
        except (KeyError, IndexError, ValueError, TypeError):
            return None
        return None
