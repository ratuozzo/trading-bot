"""Momentum scalping strategy.

The idea: small, fast price impulses tend to have a little follow-through.
We measure the return over a short lookback window and go long when the
market pushes up hard enough. Once in, we take profit quickly, cut losses
fast, and bail if the impulse reverses or the position stalls.

This is intentionally simple and transparent so you can reason about every
entry and exit. It is a starting point, not a guaranteed edge — always test
in paper mode and measure the results before risking real money.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Tuple

from ..config import StrategyConfig
from ..models import Signal, SignalType, Tick


class MomentumScalper:
    def __init__(self, cfg: StrategyConfig) -> None:
        self.cfg = cfg
        # (timestamp, price) samples, newest at the right.
        self._window: Deque[Tuple[float, float]] = deque()
        self._last_exit_time: float = float("-inf")
        self._entry_time: float | None = None

    def on_tick(self, tick: Tick, *, in_position: bool, entry_price: float) -> Signal:
        self._push(tick)

        if in_position:
            return self._exit_decision(tick, entry_price)
        return self._entry_decision(tick)

    # -- internals ---------------------------------------------------------

    def _push(self, tick: Tick) -> None:
        self._window.append((tick.timestamp, tick.price))
        horizon = self.cfg.lookback_seconds
        cutoff = tick.timestamp - horizon
        while len(self._window) > 1 and self._window[0][0] < cutoff:
            self._window.popleft()

    def _return_over(self, seconds: float, now_ts: float, now_price: float) -> float:
        """Return over roughly the last ``seconds`` of samples (0 if not enough)."""
        cutoff = now_ts - seconds
        ref_price = None
        for ts, price in self._window:
            if ts >= cutoff:
                ref_price = price
                break
        if ref_price is None or ref_price == 0:
            return 0.0
        return (now_price - ref_price) / ref_price

    def _entry_decision(self, tick: Tick) -> Signal:
        if tick.timestamp - self._last_exit_time < self.cfg.cooldown_seconds:
            return Signal(SignalType.HOLD, "cooldown")

        # Need a full window of data before trusting the signal.
        oldest_ts = self._window[0][0]
        if tick.timestamp - oldest_ts < self.cfg.lookback_seconds * 0.5:
            return Signal(SignalType.HOLD, "warming up")

        ret = self._return_over(self.cfg.lookback_seconds, tick.timestamp, tick.price)
        if ret >= self.cfg.entry_threshold:
            return Signal(
                SignalType.ENTER_LONG,
                f"impulse +{ret * 100:.3f}% over {self.cfg.lookback_seconds:.0f}s",
            )
        return Signal(SignalType.HOLD, f"no impulse ({ret * 100:+.3f}%)")

    def _exit_decision(self, tick: Tick, entry_price: float) -> Signal:
        if entry_price <= 0:
            return Signal(SignalType.HOLD, "no entry price")

        change = (tick.price - entry_price) / entry_price

        if change >= self.cfg.take_profit:
            return Signal(SignalType.EXIT_LONG, f"take profit +{change * 100:.3f}%")
        if change <= -self.cfg.stop_loss:
            return Signal(SignalType.EXIT_LONG, f"stop loss {change * 100:.3f}%")

        recent = self._return_over(self.cfg.reversal_window, tick.timestamp, tick.price)
        if recent <= -self.cfg.reversal_exit:
            return Signal(
                SignalType.EXIT_LONG, f"momentum reversal {recent * 100:.3f}%"
            )

        if self._entry_time is not None:
            held = tick.timestamp - self._entry_time
            if held >= self.cfg.max_hold_seconds:
                return Signal(SignalType.EXIT_LONG, "max hold time reached")

        return Signal(SignalType.HOLD, f"holding ({change * 100:+.3f}%)")

    def note_entry(self, timestamp: float) -> None:
        """Called by the engine after a position is opened (starts the hold clock)."""
        self._entry_time = timestamp

    def note_exit(self, timestamp: float) -> None:
        """Called by the engine after a position is closed (starts cooldown)."""
        self._last_exit_time = timestamp
        self._entry_time = None
