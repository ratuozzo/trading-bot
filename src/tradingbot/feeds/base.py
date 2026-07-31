"""Abstract price-feed interface.

A feed is an async iterator of :class:`~tradingbot.models.Tick` objects. Any
new data source (another exchange, a REST poller, a backtest replay) only has
to implement :meth:`stream` to work with the rest of the bot.
"""

from __future__ import annotations

import abc
from typing import AsyncIterator

from ..models import Tick


class PriceFeed(abc.ABC):
    @abc.abstractmethod
    def stream(self) -> AsyncIterator[Tick]:
        """Yield ticks as they arrive. Should reconnect on transient errors."""
        raise NotImplementedError
