"""Trading strategies."""

from .base import Strategy
from .momentum import MomentumScalper

__all__ = ["Strategy", "MomentumScalper"]
