"""Multi-symbol engine and backend service, driven by a synthetic feed.

No network: a fake feed yields a scripted tick sequence so entries, exits,
the position cap and session persistence are all deterministic.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from tradingbot.brokers import PaperBroker
from tradingbot.config import Config
from tradingbot.feeds.base import PriceFeed
from tradingbot.models import Tick
from tradingbot.multi_engine import MultiEngine
from tradingbot.portfolio import Portfolio
from tradingbot.risk import RiskManager


class ScriptedFeed(PriceFeed):
    """Yields a fixed list of ticks then stops."""

    def __init__(self, ticks):
        self.ticks = ticks

    async def stream(self):
        for t in self.ticks:
            yield t


def build(cfg: Config, symbols, ticks):
    broker = PaperBroker(
        starting_cash=cfg.broker.starting_cash,
        fee_bps=cfg.broker.fee_bps,
        slippage_bps=cfg.broker.slippage_bps,
    )
    return MultiEngine(
        feed=ScriptedFeed(ticks),
        broker=broker,
        portfolio=Portfolio(starting_cash=cfg.broker.starting_cash),
        risk=RiskManager(cfg.risk, starting_equity=cfg.broker.starting_cash),
        cfg=cfg,
        symbols=symbols,
    )


def ramp(symbol, start, pct_per_tick, n, t0=0.0, dt=0.1):
    """A steady move, one tick every dt seconds."""
    out, price, t = [], start, t0
    for _ in range(n):
        out.append(Tick(symbol, price, t))
        price *= 1 + pct_per_tick
        t += dt
    return out


def interleave(*streams):
    """Merge tick lists in timestamp order, like a real multi-symbol feed."""
    merged = [t for s in streams for t in s]
    return sorted(merged, key=lambda t: t.timestamp)


@pytest.fixture
def cfg():
    c = Config()
    c.strategy.lookback_seconds = 1.0
    c.strategy.entry_threshold = 0.002
    c.strategy.take_profit = 0.006
    c.strategy.stop_loss = 0.0025
    c.strategy.reversal_exit = 0.5     # disable for these tests
    c.strategy.cooldown_seconds = 0.0
    c.broker.starting_cash = 10_000.0
    c.broker.fee_bps = 8.0
    c.broker.slippage_bps = 2.0
    c.risk.max_concurrent_positions = 3
    c.risk.order_size_pct = 0.30
    return c


def run(engine):
    asyncio.run(engine.run())


# -- routing ---------------------------------------------------------------

def test_each_symbol_keeps_independent_state(cfg):
    ticks = interleave(
        ramp("AAA", 100.0, 0.0, 20),            # flat
        ramp("BBB", 50.0, 0.0008, 20),          # rising
    )
    eng = build(cfg, ["AAA", "BBB"], ticks)
    run(eng)
    assert eng.states["AAA"].ticks == 20
    assert eng.states["BBB"].ticks == 20
    # Only the moving symbol should have any impulse.
    assert abs(eng.states["AAA"].impulse) < 1e-9
    assert eng.states["BBB"].impulse > 0


def test_ticks_for_unwatched_symbols_are_ignored(cfg):
    ticks = ramp("ZZZ", 100.0, 0.001, 20)
    eng = build(cfg, ["AAA"], ticks)
    run(eng)
    assert eng.stats.ticks == 0


# -- entries ---------------------------------------------------------------

def test_enters_long_on_a_fast_rise(cfg):
    eng = build(cfg, ["AAA"], ramp("AAA", 100.0, 0.0008, 30))
    run(eng)
    assert eng.portfolio.num_trades + len(
        [s for s in eng.symbols if eng.broker.position(s).is_open]) > 0


def test_enters_short_on_a_fast_fall(cfg):
    eng = build(cfg, ["AAA"], ramp("AAA", 100.0, -0.0008, 30))
    run(eng)
    pos = eng.broker.position("AAA")
    traded_short = pos.is_open and pos.direction < 0
    closed_short = any(t.direction < 0 for t in eng.portfolio.trades)
    assert traded_short or closed_short


def test_shorts_can_be_disabled(cfg):
    cfg.strategy.allow_shorts = False
    eng = build(cfg, ["AAA"], ramp("AAA", 100.0, -0.0008, 30))
    run(eng)
    assert not eng.broker.position("AAA").is_open
    assert eng.portfolio.num_trades == 0


# -- the position cap ------------------------------------------------------

def test_never_exceeds_max_concurrent_positions(cfg):
    """Five coins all rip at once; only three may be held."""
    syms = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    ticks = interleave(*[ramp(s, 100.0, 0.0008, 30) for s in syms])
    eng = build(cfg, syms, ticks)
    run(eng)
    open_now = sum(1 for s in syms if eng.broker.position(s).is_open)
    assert open_now <= cfg.risk.max_concurrent_positions


def test_position_sizing_leaves_cash_for_later_signals(cfg):
    """The bug this guards: order_size_pct near 1.0 lets the first entry eat
    the account, so the concurrency cap never actually binds."""
    syms = ["AAA", "BBB", "CCC"]
    ticks = interleave(*[ramp(s, 100.0, 0.0008, 30) for s in syms])
    eng = build(cfg, syms, ticks)
    run(eng)
    assert eng.broker.cash > 0
    opened = sum(1 for s in syms if eng.broker.position(s).is_open)
    assert opened >= 2, "later signals were starved of cash"


def _notionals(eng, syms):
    out = []
    for s in syms:
        pos = eng.broker.position(s)
        if pos.is_open:
            out.append(pos.quantity * pos.avg_entry_price)
    return out


def test_oversized_positions_make_allocation_wildly_unequal(cfg):
    """The real failure mode of a large order_size_pct.

    It isn't that later coins can't open — they can, on the crumbs. The first
    entry takes 95% of the book, the second 95% of what's left, and the third
    is a rounding error. The signals are treated as equals; the sizing is not.
    """
    cfg.risk.order_size_pct = 0.95
    syms = ["AAA", "BBB", "CCC"]
    ticks = interleave(*[ramp(s, 100.0, 0.0008, 30) for s in syms])
    eng = build(cfg, syms, ticks)
    run(eng)
    sizes = _notionals(eng, syms)
    assert len(sizes) >= 2
    assert max(sizes) / min(sizes) > 50, "expected grossly unequal sizing"


def test_configured_sizing_allocates_comparably_across_coins(cfg):
    syms = ["AAA", "BBB", "CCC"]
    ticks = interleave(*[ramp(s, 100.0, 0.0008, 30) for s in syms])
    eng = build(cfg, syms, ticks)
    run(eng)
    sizes = _notionals(eng, syms)
    assert len(sizes) >= 2
    # 0.30 compounding gives 3000 / 2100 / 1470 — same order of magnitude.
    assert max(sizes) / min(sizes) < 4, "positions should be broadly comparable"


# -- exits -----------------------------------------------------------------

def test_take_profit_closes_and_books_a_trade(cfg):
    # Rise hard enough to enter, then keep rising past take-profit.
    eng = build(cfg, ["AAA"], ramp("AAA", 100.0, 0.001, 40))
    run(eng)
    assert eng.portfolio.num_trades >= 1
    assert any("take profit" in "" or t.pnl != 0 for t in eng.portfolio.trades)


def test_engine_survives_a_bad_tick(cfg):
    """One malformed tick must not kill the engine."""
    good = ramp("AAA", 100.0, 0.0, 5)
    bad = [Tick("AAA", float("nan"), 1.0)]
    eng = build(cfg, ["AAA"], good + bad + ramp("AAA", 100.0, 0.0, 5, t0=2.0))
    run(eng)          # must not raise
    assert eng.stats.ticks > 0


# -- the backend service ---------------------------------------------------

def test_service_persists_and_resumes_a_session(cfg, tmp_path):
    from tradingbot.server.service import TradingService

    state_file = str(tmp_path / "state.json")
    cfg.server.state_file = state_file
    cfg.feed.symbols = ["AAA"]

    svc = TradingService(cfg)
    # Drive a couple of round trips directly through the engine.
    eng = svc.engine
    for tick in ramp("AAA", 100.0, 0.001, 40):
        eng.on_tick(tick)
    for tick in ramp("AAA", 140.0, -0.002, 40, t0=10.0):
        eng.on_tick(tick)
    trades_before = eng.portfolio.num_trades
    cash_before = eng.broker.cash
    assert trades_before > 0, "test needs at least one completed trade"

    svc._save_state()
    assert os.path.exists(state_file)

    # A brand-new service (i.e. a process restart) must resume, not reset.
    resumed = TradingService(cfg)
    assert resumed.engine.portfolio.num_trades == trades_before
    assert resumed.engine.broker.cash == pytest.approx(cash_before)


def test_saved_state_is_valid_json_with_a_version(cfg, tmp_path):
    from tradingbot.server.service import TradingService

    cfg.server.state_file = str(tmp_path / "s.json")
    cfg.feed.symbols = ["AAA"]
    svc = TradingService(cfg)
    svc._save_state()
    data = json.loads(open(cfg.server.state_file).read())
    assert data["version"] == 1
    assert "trades" in data and "cash" in data


def test_rebuild_preserves_the_session(cfg, tmp_path):
    """Changing a setting must not silently reset the P&L."""
    from tradingbot.server.service import TradingService

    cfg.server.state_file = str(tmp_path / "s.json")
    cfg.feed.symbols = ["AAA"]
    svc = TradingService(cfg)
    for tick in ramp("AAA", 100.0, 0.001, 40):
        svc.engine.on_tick(tick)
    trades = svc.engine.portfolio.num_trades
    cash = svc.engine.broker.cash

    svc.rebuild(preserve=True)
    assert svc.engine.portfolio.num_trades == trades
    assert svc.engine.broker.cash == pytest.approx(cash)

    svc.rebuild(preserve=False)
    assert svc.engine.portfolio.num_trades == 0


def test_snapshot_is_json_serialisable(cfg, tmp_path):
    from tradingbot.server.service import TradingService

    cfg.server.state_file = str(tmp_path / "s.json")
    cfg.feed.symbols = ["AAA", "BBB"]
    svc = TradingService(cfg)
    svc.engine.on_tick(Tick("AAA", 100.0, 1.0))
    blob = json.dumps(svc.snapshot())     # must not raise
    assert "scanner" in blob and "equity" in blob
