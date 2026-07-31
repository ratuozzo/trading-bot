"""Portfolio / performance tracking.

Records completed round-trip trades and exposes running statistics so you can
judge whether the strategy is actually working. Kept independent of the broker
so it can be tested in isolation.

Open legs are tracked per symbol: watching ten coins means up to ten positions
in flight, and a single open-leg field would cross their fills over.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import Fill, Side, Trade


@dataclass
class _OpenLeg:
    side: Side
    quantity: float
    price: float
    timestamp: float
    fee: float


@dataclass
class Portfolio:
    starting_cash: float
    trades: List[Trade] = field(default_factory=list)
    _open: Dict[str, _OpenLeg] = field(default_factory=dict)

    def record_fill(self, fill: Fill, opening: Optional[bool] = None) -> Optional[Trade]:
        """Update state with a fill; returns a completed Trade on the close.

        ``opening`` says whether this fill opens or closes a position. When
        omitted it is inferred: no open leg for that symbol means the fill
        opens one. That inference matters now that a position can be opened
        with a SELL (a short), so side alone no longer says which way we are
        travelling.
        """
        symbol = fill.symbol
        if opening is None:
            opening = symbol not in self._open

        if opening:
            self._open[symbol] = _OpenLeg(
                side=fill.side,
                quantity=fill.quantity,
                price=fill.price,
                timestamp=fill.timestamp,
                fee=fill.fee,
            )
            return None

        leg = self._open.get(symbol)
        if leg is None:
            return None

        qty = min(fill.quantity, leg.quantity)
        direction = -1 if leg.side is Side.SELL else 1
        gross = direction * (fill.price - leg.price) * qty
        entry_fee_share = leg.fee * (qty / leg.quantity) if leg.quantity else 0.0
        fees = entry_fee_share + fill.fee

        trade = Trade(
            symbol=symbol,
            quantity=qty,
            entry_price=leg.price,
            exit_price=fill.price,
            entry_time=leg.timestamp,
            exit_time=fill.timestamp,
            fees=fees,
            pnl=gross - fees,
            side=leg.side,
        )
        self.trades.append(trade)

        leg.quantity -= qty
        leg.fee -= entry_fee_share
        if leg.quantity <= 1e-12:
            del self._open[symbol]
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

    def by_symbol(self) -> List[dict]:
        """Per-symbol results, so you can see which coins actually work."""
        out: Dict[str, dict] = {}
        for t in self.trades:
            e = out.setdefault(
                t.symbol, {"symbol": t.symbol, "trades": 0, "wins": 0, "pnl": 0.0}
            )
            e["trades"] += 1
            e["wins"] += 1 if t.pnl > 0 else 0
            e["pnl"] += t.pnl
        return sorted(out.values(), key=lambda e: e["pnl"], reverse=True)

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
