"""Risk management.

Decides how big each entry should be and whether trading should be halted.
Simple but important: it caps the per-trade size and stops the bot for the
day once losses breach a limit, so a bad streak can't drain the account.
"""

from __future__ import annotations

from .config import RiskConfig


class RiskManager:
    def __init__(self, cfg: RiskConfig, starting_equity: float) -> None:
        self.cfg = cfg
        self.starting_equity = starting_equity
        self._halted = False

    @property
    def halted(self) -> bool:
        return self._halted

    def order_notional(self, available_cash: float) -> float:
        """Quote-currency amount to spend on an entry (0 to skip)."""
        if self._halted:
            return 0.0
        notional = available_cash * self.cfg.order_size_pct
        if notional < self.cfg.min_notional:
            return 0.0
        return notional

    def update_equity(self, equity: float) -> None:
        """Check the daily loss limit against current equity; halt if breached."""
        if self.starting_equity <= 0:
            return
        drawdown = (self.starting_equity - equity) / self.starting_equity
        if drawdown >= self.cfg.daily_loss_limit_pct:
            self._halted = True
