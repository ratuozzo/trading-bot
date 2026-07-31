"""Backend service: runs the engine independently of any browser."""

from .app import create_app
from .service import TradingService

__all__ = ["create_app", "TradingService"]
