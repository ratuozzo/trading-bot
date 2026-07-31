"""Portfolio / performance tracking.

Records completed round-trip trades and exposes running statistics so you can
judge whether the strategy is actually working. Kept independent of the broker
so it can be tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .models import Fill, Side, Trade


@dataclass
class Portfolio:
    starting_cash: float
    trades: List[Trade] = field(default_factory=list)
    # Details of the currently open entry, if any.
    _open_qty: float = 0.0
    _open_price: float = 0.0
    _open_time: float = 0.0
    _open_fees: float = 0.0

    def record_fill(self, fill: Fill) -> Optional[Trade]:
        """Update state with a fill. Returns a completed Trade on a closing sell."""
        if fill.side == Side.BUY:
            # Weighted-average entry across adds (the scalper only adds flat->long).
            total_qty = self._open_qty + fill.quantity
            if total_qty > 0:
                self._open_price = (
                    self._open_price * self._open_qty + fill.price * fill.quantity
                ) / total_qty
            self._open_qty = total_qty
            if self._open_time == 0.0:
                self._open_time = fill.timestamp
            self._open_fees += fill.fee
            return None

        # SELL closes (all or part of) the position.
        if self._open_qty <= 0:
            return None
        qty = min(fill.quantity, self._open_qty)
        gross = (fill.price - self._open_price) * qty
        entry_fee_share = self._open_fees * (qty / self._open_qty) if self._open_qty else 0.0
        fees = entry_fee_share + fill.fee
        pnl = gross - fees

        trade = Trade(
            symbol=fill.symbol,
            quantity=qty,
            entry_price=self._open_price,
            exit_price=fill.price,
            entry_time=self._open_time,
            exit_time=fill.timestamp,
            fees=fees,
            pnl=pnl,
        )
        self.trades.append(trade)

        self._open_qty -= qty
        self._open_fees -= entry_fee_share
        if self._open_qty <= 1e-12:
            self._open_qty = 0.0
            self._open_price = 0.0
            self._open_time = 0.0
            self._open_fees = 0.0
        return trade

    # -- stats -------------------------------------------------------------

    @property
    def realized_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def num_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def win_rate(self) -> float:
        return self.wins / self.num_trades if self.trades else 0.0

    def summary(self) -> str:
        if not self.trades:
            return "No completed trades yet."
        total = self.realized_pnl
        pct = total / self.starting_cash * 100 if self.starting_cash else 0.0
        avg = total / self.num_trades
        return (
            f"trades={self.num_trades} win_rate={self.win_rate * 100:.1f}% "
            f"realized_pnl={total:+.2f} ({pct:+.3f}%) avg_per_trade={avg:+.4f}"
        )
