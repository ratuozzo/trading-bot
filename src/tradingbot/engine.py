"""The engine wires everything together.

For each incoming tick it:
  1. asks the strategy for a signal,
  2. runs it past the risk manager,
  3. places the order through the broker,
  4. records the result in the portfolio,
  5. prints periodic status.

It knows nothing about *how* prices arrive or *where* orders go — only the
feed / strategy / broker interfaces — so any piece can be swapped out.
"""

from __future__ import annotations

import logging

from .brokers.base import Broker
from .feeds.base import PriceFeed
from .models import Side, SignalType, Tick
from .portfolio import Portfolio
from .risk import RiskManager
from .strategy.momentum import MomentumScalper

log = logging.getLogger(__name__)


class Engine:
    def __init__(
        self,
        feed: PriceFeed,
        strategy: MomentumScalper,
        broker: Broker,
        risk: RiskManager,
        portfolio: Portfolio,
        *,
        status_every: float = 10.0,
    ) -> None:
        self.feed = feed
        self.strategy = strategy
        self.broker = broker
        self.risk = risk
        self.portfolio = portfolio
        self.status_every = status_every
        self._last_status = 0.0
        self._ticks = 0

    async def run(self) -> None:
        log.info("Engine starting. Waiting for ticks...")
        async for tick in self.feed.stream():
            self._on_tick(tick)

    def _on_tick(self, tick: Tick) -> None:
        self._ticks += 1
        pos = self.broker.position(tick.symbol)

        signal = self.strategy.on_tick(
            tick, dir=pos.direction, entry_price=pos.avg_entry_price
        )

        if signal.type == SignalType.ENTER_LONG and not pos.is_open:
            self._enter(tick, Side.BUY, signal.reason)
        elif signal.type == SignalType.ENTER_SHORT and not pos.is_open:
            self._enter(tick, Side.SELL, signal.reason)
        elif signal.type == SignalType.EXIT and pos.is_open:
            self._exit(tick, signal.reason)

        self._maybe_status(tick)

    def _enter(self, tick: Tick, side: Side, reason: str) -> None:
        # A taker lifts the ask to buy and hits the bid to sell.
        if side is Side.BUY:
            ref = tick.ask if tick.ask is not None else tick.price
        else:
            ref = tick.bid if tick.bid is not None else tick.price

        notional = self.risk.order_notional(self.broker.cash)
        if notional <= 0:
            return
        fill = self.broker.open(tick.symbol, side, notional, ref, tick.timestamp)
        if fill is None:
            return
        self.portfolio.record_fill(fill, opening=True)
        self.strategy.note_entry(tick.timestamp)
        log.info(
            "OPEN  %-5s %s qty=%.6f @ %.2f  (%s)",
            "LONG" if side is Side.BUY else "SHORT",
            fill.symbol, fill.quantity, fill.price, reason,
        )

    def _exit(self, tick: Tick, reason: str) -> None:
        pos = self.broker.position(tick.symbol)
        was_long = pos.direction > 0
        # Closing a long sells into the bid; closing a short lifts the ask.
        if was_long:
            ref = tick.bid if tick.bid is not None else tick.price
        else:
            ref = tick.ask if tick.ask is not None else tick.price

        fill = self.broker.close(tick.symbol, ref, tick.timestamp)
        if fill is None:
            return
        trade = self.portfolio.record_fill(fill, opening=False)
        self.strategy.note_exit(tick.timestamp)
        self.risk.update_equity(self._equity(tick))
        pnl = trade.pnl if trade else 0.0
        log.info(
            "CLOSE %-5s %s qty=%.6f @ %.2f  pnl=%+.2f  (%s)",
            "LONG" if was_long else "SHORT",
            fill.symbol, fill.quantity, fill.price, pnl, reason,
        )
        if self.risk.halted:
            log.warning(
                "Daily loss limit hit — halting new entries. %s",
                self.portfolio.summary(),
            )

    def _equity(self, tick: Tick) -> float:
        pos = self.broker.position(tick.symbol)
        if not pos.is_open:
            return self.broker.cash
        return (
            self.broker.cash
            + pos.quantity * pos.avg_entry_price
            + pos.unrealized_pnl(tick.price)
        )

    def _maybe_status(self, tick: Tick) -> None:
        if tick.timestamp - self._last_status < self.status_every:
            return
        self._last_status = tick.timestamp
        pos = self.broker.position(tick.symbol)
        if pos.is_open:
            label = "LONG" if pos.direction > 0 else "SHORT"
            state = f"{label} {pos.quantity:.6f}@{pos.avg_entry_price:.2f}"
        else:
            state = "flat"
        log.info(
            "[%s] price=%.2f equity=%.2f %s | %s",
            tick.symbol, tick.price, self._equity(tick), state,
            self.portfolio.summary(),
        )
