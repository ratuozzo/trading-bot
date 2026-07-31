"""Paper broker: simulates order execution with no real money.

Positions are directional: a long profits when price rises, a short when it
falls. Cash accounting is uniform across both — on open the notional is
deducted (an outright purchase for a long, posted margin for a short), and on
close the notional returns adjusted by the position's P&L. One code path
instead of two subtly different ones.

Fills happen instantly at the reference price adjusted for slippage, which
always works against us, and a taker fee is charged on every fill. Because it
implements the same :class:`Broker` interface as a live broker, the strategy
and engine cannot tell the difference.
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

    # -- opening / closing -------------------------------------------------

    def open(
        self,
        symbol: str,
        side: Side,
        quote_amount: float,
        ref_price: float,
        timestamp: float,
    ) -> Optional[Fill]:
        """Open a position worth roughly ``quote_amount`` in quote currency."""
        if quote_amount <= 0 or ref_price <= 0:
            return None
        if self.position(symbol).is_open:
            log.warning("open rejected: %s already has a position", symbol)
            return None
        spend = min(quote_amount, self._cash)
        if spend <= 0:
            log.warning("open rejected: no cash")
            return None

        direction = -1 if side is Side.SELL else 1
        price = ref_price * (1 + direction * self.slippage_rate)
        quantity = spend / (price * (1 + self.fee_rate))
        notional = quantity * price
        fee = notional * self.fee_rate

        self._cash -= notional + fee
        self._positions[symbol] = Position(
            symbol=symbol, quantity=quantity, avg_entry_price=price, side=side
        )
        return Fill(symbol, side, quantity, price, fee, timestamp)

    def close(
        self, symbol: str, ref_price: float, timestamp: float
    ) -> Optional[Fill]:
        """Close the whole position at ~``ref_price``."""
        pos = self._positions.get(symbol)
        if pos is None or not pos.is_open or ref_price <= 0:
            return None

        direction = pos.direction
        # Closing reverses the trade, so slippage flips sign too.
        price = ref_price * (1 - direction * self.slippage_rate)
        quantity = pos.quantity
        notional = quantity * price
        fee = notional * self.fee_rate
        pnl = direction * (price - pos.avg_entry_price) * quantity

        # Return the notional put up at entry, adjusted by P&L, less exit fee.
        self._cash += quantity * pos.avg_entry_price + pnl - fee

        closing_side = Side.BUY if direction < 0 else Side.SELL
        self._positions[symbol] = Position(symbol=symbol)
        return Fill(symbol, closing_side, quantity, price, fee, timestamp)

    # -- Broker interface --------------------------------------------------

    def buy(
        self, symbol: str, quote_amount: float, ref_price: float, timestamp: float
    ) -> Optional[Fill]:
        """Open a long (kept for the base interface)."""
        return self.open(symbol, Side.BUY, quote_amount, ref_price, timestamp)

    def sell(
        self, symbol: str, quantity: float, ref_price: float, timestamp: float
    ) -> Optional[Fill]:
        """Close an open long. ``quantity`` is accepted for interface
        compatibility; the whole position is closed."""
        return self.close(symbol, ref_price, timestamp)

    def equity(self, mark_price: float, symbol: str) -> float:
        """Total account value = cash + the open position marked to market."""
        pos = self.position(symbol)
        if not pos.is_open:
            return self._cash
        return (
            self._cash
            + pos.quantity * pos.avg_entry_price
            + pos.unrealized_pnl(mark_price)
        )
