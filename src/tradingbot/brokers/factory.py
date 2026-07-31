"""Build a broker from config."""

from __future__ import annotations

from ..config import BrokerConfig
from .base import Broker
from .paper import PaperBroker


def build_broker(cfg: BrokerConfig) -> Broker:
    mode = cfg.mode.lower()
    if mode == "paper":
        return PaperBroker(
            starting_cash=cfg.starting_cash,
            fee_bps=cfg.fee_bps,
            slippage_bps=cfg.slippage_bps,
        )
    if mode == "live":
        # Imported lazily so paper users don't need live dependencies.
        from .binance_live import BinanceLiveBroker

        return BinanceLiveBroker(api_key=cfg.api_key, api_secret=cfg.api_secret)
    raise ValueError(f"Unknown broker mode {cfg.mode!r}. Use 'paper' or 'live'.")
