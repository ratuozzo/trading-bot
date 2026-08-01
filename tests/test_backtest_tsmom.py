"""Invariants for the time-series momentum backtester.

A backtest that lies is worse than no backtest, so the properties tested here
are the ones whose failure would manufacture a fake edge: look-ahead, free
trading, and scoring the warm-up period.
"""

import importlib.util
import math
import os

import pytest

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts",
                     "backtest_tsmom.py")
_spec = importlib.util.spec_from_file_location("backtest_tsmom", _PATH)
tsmom = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tsmom)


def make(prices, start_t=0):
    """Candles where open == close, so execution price is unambiguous."""
    return [{"t": start_t + i * 86400, "o": p, "h": p, "l": p, "c": p, "v": 1.0}
            for i, p in enumerate(prices)]


def ramp(n, rate=0.01, base=100.0):
    return [base * (1 + rate) ** i for i in range(n)]


def test_percentile_matches_linear_interpolation():
    vals = [1.0, 2.0, 3.0, 4.0]
    assert tsmom._percentile(vals, 0.0) == 1.0
    assert tsmom._percentile(vals, 1.0) == 4.0
    assert tsmom._percentile(vals, 0.5) == pytest.approx(2.5)


def test_percentile_of_empty_never_admits_a_trade():
    # inf, not 0.0: a missing threshold must block entries, not wave them in.
    assert tsmom._percentile([], 0.5) == float("inf")


def test_lookback_returns_align_to_the_right_day():
    closes = [100.0, 110.0, 121.0]
    r = tsmom.lookback_returns(closes, 1)
    assert r[0] is None
    assert r[1] == pytest.approx(0.10)
    assert r[2] == pytest.approx(0.10)


def test_lookback_returns_are_none_before_the_window_exists():
    r = tsmom.lookback_returns([1.0] * 10, 4)
    assert all(v is None for v in r[:4])
    assert all(v is not None for v in r[4:])


def test_no_lookahead_truncating_the_future_changes_nothing():
    """The strongest practical check: results over [start, end) must not move
    when the data after `end` is deleted. If any future price leaked into a
    decision, these two runs would disagree."""
    prices = ramp(700, 0.004)
    # Bend the tail hard, so a leak would be unmissable rather than subtle.
    prices += [prices[-1] * (0.9 ** i) for i in range(1, 120)]
    full = make(prices)
    cut = 700

    a = tsmom.run(full, 28, 5, "tercile", 8.0, 2.0, window=200,
                  start=250, end=cut)
    b = tsmom.run(full[:cut], 28, 5, "tercile", 8.0, 2.0, window=200,
                  start=250, end=cut)
    assert a["return"] == pytest.approx(b["return"])
    assert a["trades"] == b["trades"]


def test_signal_is_executed_at_the_next_open_not_the_signal_close():
    """A single up-day should never be captured by the bar that produced it."""
    prices = [100.0] * 60 + [100.0, 200.0, 200.0] + [200.0] * 10
    r = tsmom.run(make(prices), 5, 1, "sign", 0.0, 0.0, window=20, start=40)
    # The 100 -> 200 jump happens between two opens the strategy could only
    # have entered after seeing it. Capturing it would mean a 100% return.
    assert r["return"] < 0.5


def test_holding_through_a_rebalance_costs_nothing():
    """The entire premise of a multi-day hold: unchanged position, no fee."""
    prices = ramp(600, 0.01)
    held = tsmom.run(make(prices), 28, 5, "sign", 50.0, 50.0, window=100,
                     start=200, allow_shorts=False)
    # A persistent uptrend under the sign rule is one entry and no exits.
    assert held["trades"] <= 2
    assert held["costs"] < 0.03


def test_costs_are_charged_and_always_reduce_return():
    prices = ramp(400, 0.02)
    prices += ramp(400, -0.02, prices[-1])
    candles = make(prices)
    free = tsmom.run(candles, 28, 5, "sign", 0.0, 0.0, window=200, start=300)
    paid = tsmom.run(candles, 28, 5, "sign", 8.0, 2.0, window=200, start=300)
    assert paid["trades"] == free["trades"]
    assert free["costs"] == 0.0
    assert paid["costs"] > 0.0
    assert paid["return"] < free["return"]


def test_warmup_is_excluded_from_scoring():
    """Scoring days the percentile rule cannot trade would report it as flat
    through periods it was never eligible for — flattering in a crash."""
    candles = make(ramp(900, 0.005))
    r = tsmom.run(candles, 28, 5, "tercile", 8.0, 2.0, window=365)
    assert r["times"][0] >= candles[28 + 365]["t"]


def test_sign_rule_needs_no_percentile_warmup():
    candles = make(ramp(200, 0.005))
    r = tsmom.run(candles, 28, 5, "sign", 8.0, 2.0, window=365)
    assert r["times"], "sign mode must not inherit the tercile warm-up"


def test_long_only_never_takes_a_short():
    prices = ramp(600, -0.01)
    r = tsmom.run(make(prices), 28, 5, "sign", 0.0, 0.0, window=100,
                  start=200, allow_shorts=False)
    assert r["exposure"] == 0.0
    assert r["return"] == pytest.approx(0.0)


def test_shorts_profit_in_a_downtrend():
    prices = ramp(600, -0.01)
    r = tsmom.run(make(prices), 28, 5, "sign", 0.0, 0.0, window=100,
                  start=200, allow_shorts=True)
    assert r["return"] > 0.0


def test_buy_and_hold_is_measured_over_the_same_window():
    """Comparing a scored slice against a buy-and-hold over the whole file is
    the easiest way to accidentally beat the market."""
    candles = make(ramp(800, 0.01))
    r = tsmom.run(candles, 28, 5, "sign", 0.0, 0.0, window=200,
                  start=400, end=700)
    expected = candles[699]["o"] / candles[400]["o"] - 1.0
    assert r["buy_hold"] == pytest.approx(expected, rel=1e-6)


def test_portfolio_equal_weights_by_date_not_by_coin_return():
    a = tsmom.run(make(ramp(600, 0.01)), 28, 5, "sign", 0.0, 0.0,
                  window=100, start=200)
    b = tsmom.run(make(ramp(600, -0.01)), 28, 5, "sign", 0.0, 0.0,
                  window=100, start=200)
    p = tsmom.portfolio([a, b])
    blended = [(x + y) / 2 for x, y in zip(a["daily"], b["daily"])]
    eq = 1.0
    for v in blended:
        eq *= (1 + v)
    assert p["return"] == pytest.approx(eq - 1.0)


def test_sharpe_is_zero_for_a_flat_curve():
    assert tsmom._sharpe([0.0] * 50) == 0.0


def test_sharpe_annualises_on_365_days():
    daily = [0.01, -0.01] * 100
    expected = (sum(daily) / len(daily)) / 0.01 * math.sqrt(365)
    assert tsmom._sharpe(daily) == pytest.approx(expected, rel=1e-6)


def test_max_drawdown_is_negative_and_finds_the_worst_trough():
    assert tsmom._max_drawdown([0.5, -0.5, 0.5]) == pytest.approx(-0.5)
    assert tsmom._max_drawdown([0.1, 0.1]) == 0.0
