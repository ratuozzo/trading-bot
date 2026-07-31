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
    _open_side: Side = Side.BUY   # BUY = long, SELL = short

    def record_fill(self, fill: Fill, opening: Optional[bool] = None) -> Optional[Trade]:
        """Update state with a fill; returns a completed Trade on the close.

        ``opening`` says whether this fill opens or closes a position. When
        omitted it is inferred: flat means the fill opens, otherwise it closes.
        That inference matters now that a position can be opened with a SELL
        (a short), so side alone no longer tells us the direction of travel.
        """
        if opening is None:
            opening = self._open_qty <= 0

        if opening:
            self._open_side = fill.side
            self._open_qty = fill.quantity
            self._open_price = fill.price
            self._open_time = fill.timestamp
            self._open_fees = fill.fee
            return None

        if self._open_qty <= 0:
            return None
        qty = min(fill.quantity, self._open_qty)
        direction = -1 if self._open_side is Side.SELL else 1
        gross = direction * (fill.price - self._open_price) * qty
        entry_fee_share = self._open_fees * (qty / self._open_qty)
        fees = entry_fee_share + fill.fee

        trade = Trade(
            symbol=fill.symbol,
            quantity=qty,
            entry_price=self._open_price,
            exit_price=fill.price,
            entry_time=self._open_time,
            exit_time=fill.timestamp,
            fees=fees,
            pnl=gross - fees,
            side=self._open_side,
        )
        self.trades.append(trade)

        self._open_qty -= qty
        self._open_fees -= entry_fee_share
        if self._open_qty <= 1e-12:
            self._open_qty = 0.0
            self._open_price = 0.0
            self._open_time = 0.0
            self._open_fees = 0.0
            self._open_side = Side.BUY
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
