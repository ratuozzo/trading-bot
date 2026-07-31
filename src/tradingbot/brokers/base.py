"""Abstract broker interface.

Any broker (paper simulator, live exchange) implements the same small surface:
buy, sell, and report the current position and cash. The engine is written
against this interface only.
"""

from __future__ import annotations

import abc
from typing import Optional

from ..models import Fill, Position, Side


class Broker(abc.ABC):
    def open(
        self,
        symbol: str,
        side: Side,
        quote_amount: float,
        ref_price: float,
        timestamp: float,
    ) -> Optional[Fill]:
        """Open a directional position. Long-only brokers may route BUY to
        :meth:`buy` and reject SELL."""
        raise NotImplementedError

    def close(self, symbol: str, ref_price: float, timestamp: float) -> Optional[Fill]:
        """Close the whole open position for ``symbol``."""
        raise NotImplementedError

    @abc.abstractmethod
    def buy(self, symbol: str, quote_amount: float, ref_price: float, timestamp: float) -> Optional[Fill]:
        """Spend ``quote_amount`` (e.g. USDT) buying ``symbol`` at ~``ref_price``.

        Returns the :class:`Fill`, or ``None`` if the order could not be placed.
        """

    @abc.abstractmethod
    def sell(self, symbol: str, quantity: float, ref_price: float, timestamp: float) -> Optional[Fill]:
        """Sell ``quantity`` of the base asset at ~``ref_price``."""

    @abc.abstractmethod
    def position(self, symbol: str) -> Position:
        """Current position for ``symbol`` (quantity 0 if flat)."""

    @property
    @abc.abstractmethod
    def cash(self) -> float:
        """Available quote-currency balance."""
