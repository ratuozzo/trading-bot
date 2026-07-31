"""Brokers: execute orders and hold the account state.

The engine only ever talks to the :class:`Broker` interface, so switching
from paper to live trading is a one-line config change.
"""

from .base import Broker
from .paper import PaperBroker
from .factory import build_broker

__all__ = ["Broker", "PaperBroker", "build_broker"]
