"""Live Binance broker — stub.

This is deliberately NOT implemented yet. It exists so you can see exactly
where real order execution plugs in: fill out ``buy``/``sell`` with signed
REST calls to Binance (POST /api/v3/order), and the strategy, engine, risk
manager, and portfolio all keep working unchanged.

Before enabling live trading:
  * Create API keys on Binance with *trading* permission but NOT withdrawal.
  * Put them in environment variables (TB_API_KEY / TB_API_SECRET), never in git.
  * Start with tiny sizes and test against the Binance Spot Testnet first
    (https://testnet.binance.vision/).
  * Understand that real trading can lose real money.
"""

from __future__ import annotations

from typing import Optional

from ..models import Fill, Position
from .base import Broker


class BinanceLiveBroker(Broker):
    def __init__(self, api_key: str, api_secret: str) -> None:
        if not api_key or not api_secret:
            raise ValueError(
                "Live trading needs TB_API_KEY and TB_API_SECRET set in the "
                "environment. Refusing to start without them."
            )
        self.api_key = api_key
        self.api_secret = api_secret
        raise NotImplementedError(
            "Live trading is not implemented yet. Run in paper mode "
            "(broker.mode: paper) until you have tested your strategy and are "
            "ready to wire up real order execution here."
        )

    def buy(self, symbol, quote_amount, ref_price, timestamp) -> Optional[Fill]:
        raise NotImplementedError

    def sell(self, symbol, quantity, ref_price, timestamp) -> Optional[Fill]:
        raise NotImplementedError

    def position(self, symbol: str) -> Position:
        raise NotImplementedError

    @property
    def cash(self) -> float:
        raise NotImplementedError
