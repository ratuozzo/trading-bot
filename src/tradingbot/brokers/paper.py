"""Paper broker: simulates order execution with no real money.

Fills happen instantly at the reference price adjusted for slippage, and a
taker fee is charged on every fill. It tracks cash and a single position per
symbol, which is all the momentum scalper needs. Because it implements the
same :class:`Broker` interface as a live broker, the strategy and engine
cannot tell the difference.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from ..models import Fill, Position, Side
from .base import Broker

log = logging.getLogger(__name__)


class PaperBroker(Broker):
    def __init__(
        self,
        starting_cash: float = 10_000.0,
        fee_bps: float = 10.0,
        slippage_bps: float = 2.0,
    ) -> None:
        self._cash = float(starting_cash)
        self.starting_cash = float(starting_cash)
        self.fee_rate = fee_bps / 10_000.0
        self.slippage_rate = slippage_bps / 10_000.0
        self._positions: Dict[str, Position] = {}

    @property
    def cash(self) -> float:
        return self._cash

    def position(self, symbol: str) -> Position:
        return self._positions.get(symbol, Position(symbol=symbol))

    def buy(
        self, symbol: str, quote_amount: float, ref_price: float, timestamp: float
    ) -> Optional[Fill]:
        if quote_amount <= 0 or ref_price <= 0:
            return None
        quote_amount = min(quote_amount, self._cash)
        if quote_amount <= 0:
            log.warning("buy rejected: no cash")
            return None

        fill_price = ref_price * (1 + self.slippage_rate)  # pay a touch more
        # quote_amount covers notional + fee, so solve for quantity.
        quantity = quote_amount / (fill_price * (1 + self.fee_rate))
        notional = quantity * fill_price
        fee = notional * self.fee_rate

        self._cash -= notional + fee
        pos = self._positions.get(symbol, Position(symbol=symbol))
        new_qty = pos.quantity + quantity
        pos.avg_entry_price = (
            (pos.avg_entry_price * pos.quantity + fill_price * quantity) / new_qty
            if new_qty > 0
            else 0.0
        )
        pos.quantity = new_qty
        self._positions[symbol] = pos

        return Fill(symbol, Side.BUY, quantity, fill_price, fee, timestamp)

    def sell(
        self, symbol: str, quantity: float, ref_price: float, timestamp: float
    ) -> Optional[Fill]:
        pos = self._positions.get(symbol)
        if pos is None or pos.quantity <= 0 or ref_price <= 0:
            return None
        quantity = min(quantity, pos.quantity)

        fill_price = ref_price * (1 - self.slippage_rate)  # receive a touch less
        notional = quantity * fill_price
        fee = notional * self.fee_rate

        self._cash += notional - fee
        pos.quantity -= quantity
        if pos.quantity <= 1e-12:
            pos.quantity = 0.0
            pos.avg_entry_price = 0.0
        self._positions[symbol] = pos

        return Fill(symbol, Side.SELL, quantity, fill_price, fee, timestamp)

    def equity(self, mark_price: float, symbol: str) -> float:
        """Total account value = cash + position marked at ``mark_price``."""
        pos = self.position(symbol)
        return self._cash + pos.quantity * mark_price
