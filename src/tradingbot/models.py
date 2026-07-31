"""Core data structures shared across the bot.

These are plain, immutable-ish dataclasses so they are cheap to create on
every tick and easy to test without any exchange connection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class SignalType(str, Enum):
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    EXIT = "EXIT"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Tick:
    """A single price update from the feed.

    ``price`` is the reference price used by the strategy (last trade price,
    or the mid price for book-ticker streams). ``bid``/``ask`` are populated
    when the feed carries them so the broker can model the spread realistically.
    """

    symbol: str
    price: float
    timestamp: float  # seconds since epoch (exchange event time when available)
    quantity: float = 0.0
    bid: Optional[float] = None
    ask: Optional[float] = None

    @property
    def spread(self) -> Optional[float]:
        if self.bid is not None and self.ask is not None:
            return self.ask - self.bid
        return None


@dataclass(frozen=True)
class Signal:
    """A strategy's decision for the current tick."""

    type: SignalType
    reason: str = ""


@dataclass
class Order:
    symbol: str
    side: Side
    quantity: float
    timestamp: float


@dataclass
class Fill:
    """The result of executing an order against a broker."""

    symbol: str
    side: Side
    quantity: float
    price: float          # actual fill price (after slippage)
    fee: float            # fee paid in quote currency
    timestamp: float


@dataclass
class Position:
    """An open position. ``quantity`` is always >= 0; direction lives in
    ``side``, so a short is (side=SELL, quantity=1.0), not a negative size."""

    symbol: str
    quantity: float = 0.0
    avg_entry_price: float = 0.0
    side: Optional[Side] = None

    @property
    def is_open(self) -> bool:
        return self.quantity > 0

    @property
    def direction(self) -> int:
        """+1 long, -1 short, 0 flat."""
        if not self.is_open or self.side is None:
            return 0
        return -1 if self.side is Side.SELL else 1

    def unrealized_pnl(self, price: float) -> float:
        if not self.is_open:
            return 0.0
        return self.direction * (price - self.avg_entry_price) * self.quantity


@dataclass
class Trade:
    """A completed round-trip (entry + exit), for the trade log."""

    symbol: str
    quantity: float
    entry_price: float
    exit_price: float
    entry_time: float
    exit_time: float
    fees: float
    pnl: float
    side: Side = Side.BUY   # BUY = the position was long, SELL = short

    @property
    def direction(self) -> int:
        return -1 if self.side is Side.SELL else 1

    @property
    def return_pct(self) -> float:
        """Signed by direction: a short that fell returns a positive number."""
        if self.entry_price == 0:
            return 0.0
        return self.direction * (self.exit_price - self.entry_price) / self.entry_price

    @property
    def hold_seconds(self) -> float:
        return self.exit_time - self.entry_time
