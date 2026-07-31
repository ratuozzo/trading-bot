"""Multi-symbol engine — the thing the backend actually runs.

The single-symbol :class:`~tradingbot.engine.Engine` is kept for the simple
CLI case. This one watches many coins over one feed connection, giving each
its own independent strategy state while they share a single book of cash.

It is deliberately synchronous per tick: a tick arrives, it is routed to that
symbol's strategy, and any resulting order is placed immediately. No queues,
no cross-symbol coordination beyond the shared cash and the position cap.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

from .brokers.base import Broker
from .config import Config
from .feeds.base import PriceFeed
from .models import Side, SignalType, Tick
from .portfolio import Portfolio
from .risk import RiskManager
from .strategy.momentum import MomentumScalper

log = logging.getLogger(__name__)


#: Window used to report a coin's trade rate, in seconds.
RATE_WINDOW = 20.0


@dataclass
class SymbolState:
    """Everything the engine tracks for one coin."""

    symbol: str
    strategy: MomentumScalper
    last_price: float = 0.0
    last_tick_ts: float = 0.0
    impulse: float = 0.0
    ticks: int = 0
    #: Recent tick timestamps, so we can report trades/sec. A coin printing
    #: less often than the lookback window needs will show an impulse of
    #: exactly 0.000% forever — indistinguishable from "the market is calm"
    #: unless we surface the rate alongside it.
    recent: Deque[float] = field(default_factory=deque)

    def note_tick(self, ts: float) -> None:
        self.recent.append(ts)
        cutoff = ts - RATE_WINDOW
        while self.recent and self.recent[0] < cutoff:
            self.recent.popleft()

    @property
    def trade_rate(self) -> float:
        """Trades per second over the recent window."""
        if len(self.recent) < 2:
            return 0.0
        span = self.recent[-1] - self.recent[0]
        return len(self.recent) / span if span > 0 else 0.0

    def can_fire(self, lookback_seconds: float) -> bool:
        """A window needs ~2 prints to measure anything at all."""
        return self.trade_rate * lookback_seconds >= 2.0


@dataclass
class EngineStats:
    started_at: float = 0.0
    ticks: int = 0
    last_tick_at: float = 0.0
    feed_errors: int = 0
    last_error: str = ""


class MultiEngine:
    def __init__(
        self,
        feed: PriceFeed,
        broker: Broker,
        portfolio: Portfolio,
        risk: RiskManager,
        cfg: Config,
        symbols: List[str],
    ) -> None:
        self.feed = feed
        self.broker = broker
        self.portfolio = portfolio
        self.risk = risk
        self.cfg = cfg
        self.symbols = list(symbols)
        self.states: Dict[str, SymbolState] = {
            s: SymbolState(symbol=s, strategy=MomentumScalper(cfg.strategy))
            for s in self.symbols
        }
        self.stats = EngineStats()
        self._stopping = False

    # -- lifecycle ---------------------------------------------------------

    async def run(self) -> None:
        self._stopping = False
        self.stats.started_at = time.time()
        log.info("Engine running over %d symbols", len(self.symbols))
        try:
            async for tick in self.feed.stream():
                if self._stopping:
                    break
                try:
                    self.on_tick(tick)
                except Exception as exc:  # noqa: BLE001 - one bad tick must not
                    # take the whole engine down; record it and carry on.
                    self.stats.feed_errors += 1
                    self.stats.last_error = f"{type(exc).__name__}: {exc}"
                    log.exception("tick handling failed")
        finally:
            log.info("Engine stopped. %s", self.portfolio.summary())

    def stop(self) -> None:
        self._stopping = True

    # -- per tick ----------------------------------------------------------

    def on_tick(self, tick: Tick) -> None:
        state = self.states.get(tick.symbol)
        if state is None:
            return  # a symbol we are not trading

        self.stats.ticks += 1
        self.stats.last_tick_at = time.time()
        state.ticks += 1
        state.last_price = tick.price
        state.last_tick_ts = tick.timestamp
        state.note_tick(tick.timestamp)

        pos = self.broker.position(tick.symbol)
        signal = state.strategy.on_tick(
            tick, dir=pos.direction, entry_price=pos.avg_entry_price
        )
        state.impulse = state.strategy._return_over(
            self.cfg.strategy.lookback_seconds, tick.timestamp, tick.price
        )

        if signal.type is SignalType.ENTER_LONG and not pos.is_open:
            self._enter(tick, Side.BUY, signal.reason)
        elif signal.type is SignalType.ENTER_SHORT and not pos.is_open:
            self._enter(tick, Side.SELL, signal.reason)
        elif signal.type is SignalType.EXIT and pos.is_open:
            self._exit(tick, signal.reason)

    def _enter(self, tick: Tick, side: Side, reason: str) -> None:
        # A taker lifts the ask to buy and hits the bid to sell.
        if side is Side.BUY:
            ref = tick.ask if tick.ask is not None else tick.price
        else:
            ref = tick.bid if tick.bid is not None else tick.price

        notional = self.risk.order_notional(
            self.broker.cash, self.open_count, self.equity()
        )
        if notional <= 0:
            return
        fill = self.broker.open(tick.symbol, side, notional, ref, tick.timestamp)
        if fill is None:
            return
        self.portfolio.record_fill(fill, opening=True)
        self.states[tick.symbol].strategy.note_entry(tick.timestamp)
        log.info(
            "OPEN  %-5s %-10s qty=%.6f @ %.6f  (%s)",
            "LONG" if side is Side.BUY else "SHORT",
            fill.symbol, fill.quantity, fill.price, reason,
        )

    def _exit(self, tick: Tick, reason: str) -> None:
        pos = self.broker.position(tick.symbol)
        was_long = pos.direction > 0
        if was_long:
            ref = tick.bid if tick.bid is not None else tick.price
        else:
            ref = tick.ask if tick.ask is not None else tick.price

        fill = self.broker.close(tick.symbol, ref, tick.timestamp)
        if fill is None:
            return
        trade = self.portfolio.record_fill(fill, opening=False)
        self.states[tick.symbol].strategy.note_exit(tick.timestamp)
        self.risk.update_equity(self.equity())
        log.info(
            "CLOSE %-5s %-10s qty=%.6f @ %.6f  pnl=%+.4f  (%s)",
            "LONG" if was_long else "SHORT",
            fill.symbol, fill.quantity, fill.price,
            trade.pnl if trade else 0.0, reason,
        )
        if self.risk.halted:
            log.warning("Daily loss limit hit — halting entries. %s",
                        self.portfolio.summary())

    # -- views -------------------------------------------------------------

    @property
    def open_count(self) -> int:
        return sum(1 for s in self.symbols if self.broker.position(s).is_open)

    def marks(self) -> Dict[str, float]:
        return {
            s: st.last_price for s, st in self.states.items() if st.last_price > 0
        }

    def equity(self) -> float:
        total = self.broker.cash
        for symbol, state in self.states.items():
            pos = self.broker.position(symbol)
            if not pos.is_open:
                continue
            total += pos.quantity * pos.avg_entry_price
            if state.last_price > 0:
                total += pos.unrealized_pnl(state.last_price)
        return total
