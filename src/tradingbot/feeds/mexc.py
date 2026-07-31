"""MEXC futures (contract) public WebSocket feed — free, no key.

MEXC's USDT-margined perpetuals are reachable where Binance is not: Binance
geo-blocks a number of jurisdictions outright (its API answers HTTP 451,
"restricted location"), which is why a Binance row can sit blank forever.

Endpoint: ``wss://contract.mexc.com/edge``
Subscribe: ``{"method": "sub.deal", "param": {"symbol": "BTC_USDT"}}``
Push:      ``{"channel": "push.deal", "data": {"p", "v", "T", "t"}, "symbol"}``
Keepalive: ``{"method": "ping"}`` every 10-20s or the server hangs up at 60s.

Docs: https://mexcdevelop.github.io/apidocs/contract_v1_en/

Note on fees: orders placed through the MEXC *API* are billed on a separate
schedule from the app (0.06%/0.08% maker/taker as of June 2026), and API
accounts are excluded from the zero-fee promotions. Set ``broker.fee_bps``
accordingly or the simulation will flatter the strategy.
"""

from __future__ import annotations

from typing import Any, List, Optional

from ..models import Tick
from .ws_base import SubscribeWebSocketFeed

_URL = "wss://contract.mexc.com/edge"


class MEXCFuturesFeed(SubscribeWebSocketFeed):
    name = "mexc-futures"
    url = _URL
    keepalive_interval = 15.0
    keepalive_payload = {"method": "ping"}

    def __init__(self, symbol="BTC_USDT", stream: str = "trade") -> None:
        """``symbol`` may be one contract or a list of them.

        One connection carries every contract, so watching ten coins costs one
        socket rather than ten. Ticks are tagged with their symbol, and the
        engine routes on that.
        """
        if isinstance(symbol, str):
            symbols = [symbol]
        else:
            symbols = list(symbol)
        if not symbols:
            raise ValueError("MEXCFuturesFeed needs at least one symbol")

        # Accept "BTCUSDT" or "btc_usdt" and normalise to MEXC's BTC_USDT.
        self.symbols = [_normalise(s) for s in symbols]
        self.symbol = self.symbols[0]   # primary, for single-symbol callers
        self.stream_type = stream

    def _subscribe_payload(self) -> List[Any]:
        return [
            {"method": "sub.deal", "param": {"symbol": s}} for s in self.symbols
        ]

    def _parse(self, msg: dict) -> Optional[Tick]:
        if msg.get("channel") != "push.deal":
            return None

        data = msg.get("data")
        # The live feed batches deals in a list; the docs show a bare object.
        # Accept both, and take the most recent deal in a batch.
        if isinstance(data, list):
            data = data[-1] if data else None
        if not isinstance(data, dict):
            return None

        try:
            price = float(data["p"])
        except (KeyError, ValueError, TypeError):
            return None
        # A non-finite price would poison every downstream calculation.
        if price <= 0 or price != price:
            return None

        return Tick(
            symbol=msg.get("symbol", self.symbol),
            price=price,
            quantity=_as_float(data.get("v")),
            timestamp=_as_float(data.get("t") or msg.get("ts")) / 1000.0,
        )


def _normalise(symbol: str) -> str:
    s = symbol.upper()
    if "_" in s:
        return s
    for quote in ("USDT", "USDC", "USD"):
        if s.endswith(quote):
            return f"{s[: -len(quote)]}_{quote}"
    return s


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
