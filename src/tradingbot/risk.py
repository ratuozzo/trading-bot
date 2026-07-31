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

    def order_notional(
        self,
        available_cash: float,
        open_count: int = 0,
        equity: float | None = None,
    ) -> float:
        """Quote-currency amount to spend on an entry (0 to skip).

        ``open_count`` caps how many coins can be held at once. Without it the
        first signal of the session would swallow all the cash and the other
        symbols would never get a turn.

        Sizing modes:

        ``equity`` (default)
            Every position is the same fraction of total equity, so three
            concurrent positions are the same size regardless of which fired
            first.
        ``cash``
            The old behaviour: a fraction of *remaining* cash, which
            compounds down. At 30% that gives 3000 / 2100 / 1470 — the coin
            that happened to move first gets twice the bet of the third, and
            nothing about the signal justifies that.
        """
        if self._halted:
            return 0.0
        if open_count >= self.cfg.max_concurrent_positions:
            return 0.0

        if self.cfg.position_sizing == "equity" and equity and equity > 0:
            target = equity * self.cfg.order_size_pct
        else:
            target = available_cash * self.cfg.order_size_pct

        # Never commit more than we actually hold — no implicit leverage.
        notional = min(target, available_cash)
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
