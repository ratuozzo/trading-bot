from tradingbot.config import StrategyConfig
from tradingbot.models import SignalType, Tick
from tradingbot.strategy import MomentumScalper


def feed(strat, prices, start=0.0, dt=1.0, dir=0, entry_price=0.0):
    """Push a list of prices and return the last signal."""
    sig = None
    ts = start
    for p in prices:
        sig = strat.on_tick(
            Tick("BTCUSDT", p, ts), dir=dir, entry_price=entry_price
        )
        ts += dt
    return sig


# -- entries ---------------------------------------------------------------

def test_enters_long_on_upward_impulse():
    cfg = StrategyConfig(lookback_seconds=5.0, entry_threshold=0.0015)
    s = MomentumScalper(cfg)
    # ~+0.5% over 5 seconds — well above the 0.15% threshold.
    sig = feed(s, [100.0, 100.1, 100.2, 100.3, 100.4, 100.5])
    assert sig.type == SignalType.ENTER_LONG


def test_enters_short_on_downward_impulse():
    cfg = StrategyConfig(lookback_seconds=5.0, entry_threshold=0.0015)
    s = MomentumScalper(cfg)
    sig = feed(s, [100.0, 99.9, 99.8, 99.7, 99.6, 99.5])
    assert sig.type == SignalType.ENTER_SHORT


def test_downward_impulse_ignored_when_shorts_disabled():
    cfg = StrategyConfig(
        lookback_seconds=5.0, entry_threshold=0.0015, allow_shorts=False
    )
    s = MomentumScalper(cfg)
    sig = feed(s, [100.0, 99.9, 99.8, 99.7, 99.6, 99.5])
    assert sig.type == SignalType.HOLD


def test_no_entry_when_flat():
    cfg = StrategyConfig(lookback_seconds=5.0, entry_threshold=0.0015)
    s = MomentumScalper(cfg)
    sig = feed(s, [100.0] * 6)
    assert sig.type == SignalType.HOLD


# -- long exits ------------------------------------------------------------

def test_take_profit_exit():
    cfg = StrategyConfig(take_profit=0.002, stop_loss=0.5, reversal_exit=0.5)
    s = MomentumScalper(cfg)
    # Long from 100; price now 100.3 (+0.3% > +0.2% TP).
    sig = s.on_tick(Tick("BTCUSDT", 100.3, 10.0), dir=1, entry_price=100.0)
    assert sig.type == SignalType.EXIT
    assert "take profit" in sig.reason


def test_stop_loss_exit():
    cfg = StrategyConfig(take_profit=0.5, stop_loss=0.0015, reversal_exit=0.5)
    s = MomentumScalper(cfg)
    sig = s.on_tick(Tick("BTCUSDT", 99.7, 10.0), dir=1, entry_price=100.0)
    assert sig.type == SignalType.EXIT
    assert "stop loss" in sig.reason


def test_max_hold_exit():
    cfg = StrategyConfig(
        take_profit=0.5, stop_loss=0.5, reversal_exit=0.5, max_hold_seconds=30.0
    )
    s = MomentumScalper(cfg)
    s.note_entry(0.0)
    sig = s.on_tick(Tick("BTCUSDT", 100.05, 40.0), dir=1, entry_price=100.0)
    assert sig.type == SignalType.EXIT
    assert "max hold" in sig.reason


def test_cooldown_blocks_immediate_reentry():
    cfg = StrategyConfig(
        lookback_seconds=5.0, entry_threshold=0.0015, cooldown_seconds=10.0
    )
    s = MomentumScalper(cfg)
    feed(s, [100.0, 100.1, 100.2, 100.3, 100.4])
    s.note_exit(4.0)
    # strong impulse right after the exit, still inside cooldown
    sig = s.on_tick(Tick("BTCUSDT", 101.0, 5.0), dir=0, entry_price=0.0)
    assert sig.type == SignalType.HOLD
    assert sig.reason == "cooldown"


# -- short exits are mirror images -----------------------------------------

def test_short_take_profit_when_price_falls():
    cfg = StrategyConfig(take_profit=0.002, stop_loss=0.5, reversal_exit=0.5)
    s = MomentumScalper(cfg)
    # Short from 100; price down to 99.7 is +0.3% in our favour.
    sig = s.on_tick(Tick("BTCUSDT", 99.7, 10.0), dir=-1, entry_price=100.0)
    assert sig.type == SignalType.EXIT
    assert "take profit" in sig.reason


def test_short_stop_loss_when_price_rises():
    cfg = StrategyConfig(take_profit=0.5, stop_loss=0.0015, reversal_exit=0.5)
    s = MomentumScalper(cfg)
    sig = s.on_tick(Tick("BTCUSDT", 100.3, 10.0), dir=-1, entry_price=100.0)
    assert sig.type == SignalType.EXIT
    assert "stop loss" in sig.reason


def test_short_holds_while_price_falls_slowly():
    cfg = StrategyConfig(take_profit=0.002, stop_loss=0.0015, reversal_exit=0.5)
    s = MomentumScalper(cfg)
    sig = s.on_tick(Tick("BTCUSDT", 99.95, 10.0), dir=-1, entry_price=100.0)
    assert sig.type == SignalType.HOLD


def test_short_reversal_exit_on_upward_flip():
    cfg = StrategyConfig(
        take_profit=0.5, stop_loss=0.5, reversal_exit=0.0008, reversal_window=1.5
    )
    s = MomentumScalper(cfg)
    # Price ticking up against a short must trigger the reversal exit.
    s.on_tick(Tick("BTCUSDT", 100.0, 10.0), dir=-1, entry_price=100.0)
    sig = s.on_tick(Tick("BTCUSDT", 100.2, 11.0), dir=-1, entry_price=100.0)
    assert sig.type == SignalType.EXIT
    assert "reversal" in sig.reason


# -- signal / execution split (lead-lag setups) ----------------------------

def test_exec_price_drives_take_profit_not_signal_price():
    """With a fast signal feed and a different execution venue, TP/SL must be
    measured on the exec venue — otherwise basis between the two books would
    trigger phantom exits."""
    cfg = StrategyConfig(take_profit=0.002, stop_loss=0.5, reversal_exit=0.5)
    s = MomentumScalper(cfg)

    # Signal venue (a perp) trades $500 above the spot book we entered on.
    # The perp is up over 0.2% but spot has not moved -> must NOT take profit.
    sig = s.on_tick(
        Tick("BTCUSDT", 100_500.0, 10.0),
        dir=1, entry_price=100_000.0, exec_price=100_000.0,
    )
    assert sig.type == SignalType.HOLD

    # Now spot itself clears the target -> take profit.
    sig = s.on_tick(
        Tick("BTCUSDT", 100_500.0, 11.0),
        dir=1, entry_price=100_000.0, exec_price=100_250.0,
    )
    assert sig.type == SignalType.EXIT
    assert "take profit" in sig.reason


def test_exec_price_defaults_to_signal_price():
    """Single-venue behaviour is unchanged when exec_price is omitted."""
    cfg = StrategyConfig(take_profit=0.002, stop_loss=0.5, reversal_exit=0.5)
    a = MomentumScalper(cfg)
    b = MomentumScalper(cfg)
    t = Tick("BTCUSDT", 100.3, 10.0)
    assert (
        a.on_tick(t, dir=1, entry_price=100.0).type
        == b.on_tick(t, dir=1, entry_price=100.0, exec_price=100.3).type
    )


def test_stop_loss_uses_exec_price():
    cfg = StrategyConfig(take_profit=0.5, stop_loss=0.0015, reversal_exit=0.5)
    s = MomentumScalper(cfg)
    # Signal venue flat, exec venue down 0.3% -> stop out on the exec venue.
    sig = s.on_tick(
        Tick("BTCUSDT", 100_000.0, 10.0),
        dir=1, entry_price=100_000.0, exec_price=99_700.0,
    )
    assert sig.type == SignalType.EXIT
    assert "stop loss" in sig.reason


# -- the lookback is a velocity filter -------------------------------------

def test_shorter_lookback_demands_a_faster_move():
    """Same +0.2% move: over 1s it is a violent impulse and triggers; spread
    across 10s it is a drift and must not."""
    prices = [100.0, 100.05, 100.1, 100.15, 100.2]

    fast = MomentumScalper(
        StrategyConfig(lookback_seconds=1.0, entry_threshold=0.0015)
    )
    assert feed(fast, prices, dt=0.25).type == SignalType.ENTER_LONG

    slow = MomentumScalper(
        StrategyConfig(lookback_seconds=10.0, entry_threshold=0.0015)
    )
    # 2.5s of a 10s window is not enough history yet -> still warming up.
    assert feed(slow, prices, dt=0.25).type == SignalType.HOLD
