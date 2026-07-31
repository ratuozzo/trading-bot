from tradingbot.config import StrategyConfig
from tradingbot.models import SignalType, Tick
from tradingbot.strategy import MomentumScalper


def feed(strat, prices, start=0.0, dt=1.0, in_position=False, entry_price=0.0):
    """Push a list of prices and return the last signal."""
    sig = None
    ts = start
    for p in prices:
        sig = strat.on_tick(
            Tick("BTCUSDT", p, ts), in_position=in_position, entry_price=entry_price
        )
        ts += dt
    return sig


def test_enters_long_on_upward_impulse():
    cfg = StrategyConfig(lookback_seconds=5.0, entry_threshold=0.0015)
    s = MomentumScalper(cfg)
    # ~+0.5% over 5 seconds — well above the 0.15% threshold.
    sig = feed(s, [100.0, 100.1, 100.2, 100.3, 100.4, 100.5])
    assert sig.type == SignalType.ENTER_LONG


def test_no_entry_when_flat():
    cfg = StrategyConfig(lookback_seconds=5.0, entry_threshold=0.0015)
    s = MomentumScalper(cfg)
    sig = feed(s, [100.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    assert sig.type == SignalType.HOLD


def test_take_profit_exit():
    cfg = StrategyConfig(take_profit=0.002, stop_loss=0.5, reversal_exit=0.5)
    s = MomentumScalper(cfg)
    # In a position entered at 100; price now 100.3 (+0.3% > +0.2% TP).
    sig = s.on_tick(Tick("BTCUSDT", 100.3, 10.0), in_position=True, entry_price=100.0)
    assert sig.type == SignalType.EXIT_LONG
    assert "take profit" in sig.reason


def test_stop_loss_exit():
    cfg = StrategyConfig(take_profit=0.5, stop_loss=0.0015, reversal_exit=0.5)
    s = MomentumScalper(cfg)
    sig = s.on_tick(Tick("BTCUSDT", 99.7, 10.0), in_position=True, entry_price=100.0)
    assert sig.type == SignalType.EXIT_LONG
    assert "stop loss" in sig.reason


def test_max_hold_exit():
    cfg = StrategyConfig(
        take_profit=0.5, stop_loss=0.5, reversal_exit=0.5, max_hold_seconds=30.0
    )
    s = MomentumScalper(cfg)
    s.note_entry(0.0)
    # Small drift, no TP/SL, but held longer than max_hold_seconds.
    sig = s.on_tick(Tick("BTCUSDT", 100.05, 40.0), in_position=True, entry_price=100.0)
    assert sig.type == SignalType.EXIT_LONG
    assert "max hold" in sig.reason


def test_cooldown_blocks_immediate_reentry():
    cfg = StrategyConfig(
        lookback_seconds=5.0, entry_threshold=0.0015, cooldown_seconds=10.0
    )
    s = MomentumScalper(cfg)
    # warm up + impulse
    feed(s, [100.0, 100.1, 100.2, 100.3, 100.4])
    s.note_exit(4.0)
    # strong impulse right after the exit, still inside cooldown
    sig = s.on_tick(Tick("BTCUSDT", 101.0, 5.0), in_position=False, entry_price=0.0)
    assert sig.type == SignalType.HOLD
    assert sig.reason == "cooldown"
