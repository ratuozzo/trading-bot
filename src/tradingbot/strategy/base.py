"""Abstract strategy interface.

A strategy is fed one tick at a time and returns a :class:`Signal`. It is
told whether a position is currently open so it can decide between entering,
exiting, or holding. Strategies are pure decision logic — they never touch a
broker or place orders themselves; the engine does that.
"""

from __future__ import annotations

import abc

from ..models import Signal, Tick


class Strategy(abc.ABC):
    @abc.abstractmethod
    def on_tick(self, tick: Tick, *, in_position: bool, entry_price: float) -> Signal:
        """Return the decision for this tick.

        Args:
            tick: the latest price update.
            in_position: True if a long position is currently open.
            entry_price: average entry price of the open position (0 if flat).
        """
        raise NotImplementedError
