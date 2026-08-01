"""Invariants for the cross-sectional momentum backtester.

Cross-sectional ranking has failure modes the time-series version does not:
a coin can be ranked on a day it cannot be traded, the long and short legs
can drift away from neutral, and a missing price can silently become a
zero return instead of an exclusion. Each of those manufactures profit.
"""

import importlib.util
import os

import pytest

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts",
                     "backtest_xsmom.py")
_spec = importlib.util.spec_from_file_location("backtest_xsmom", _PATH)
xs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(xs)


def series(prices, start_t=0):
    return [{"t": start_t + i * 86400, "o": p, "h": p, "l": p, "c": p,
             "v": 1.0} for i, p in enumerate(prices)]


def ramp(n, rate, base=100.0):
    return [base * (1 + rate) ** i for i in range(n)]


def panel(rates, n=400):
    """One coin per rate, each a clean exponential ramp."""
    loaded = [(f"C{i}", series(ramp(n, r))) for i, r in enumerate(rates)]
    return xs.build_panel(loaded)


def test_build_panel_aligns_coins_on_a_shared_date_axis():
    a = series([1.0, 2.0, 3.0], start_t=0)
    b = series([9.0, 8.0], start_t=86400)
    dates, opens, _ = xs.build_panel([("A", a), ("B", b)])
    assert dates == [0, 86400, 172800]
    assert opens["B"][0] is None
    assert opens["B"][1] == 9.0


def test_build_panel_rejects_nonpositive_prices():
    bad = series([1.0, 0.0, 3.0])
    _, opens, closes = xs.build_panel([("A", bad)])
    assert opens["A"][1] is None and closes["A"][1] is None


def test_dollar_neutral_weights_sum_to_zero_and_gross_to_one():
    dates, opens, closes = panel([0.03, 0.02, 0.01, 0.0, -0.01, -0.02])
    w = xs.rank_weights(list(opens), closes, opens, 100, 28, 1 / 3,
                        False, 4)
    assert w
    assert sum(w.values()) == pytest.approx(0.0)
    assert sum(abs(v) for v in w.values()) == pytest.approx(1.0)


def test_ranking_puts_the_strongest_long_and_the_weakest_short():
    rates = [0.03, 0.02, 0.01, 0.0, -0.01, -0.03]
    dates, opens, closes = panel(rates)
    w = xs.rank_weights(list(opens), closes, opens, 100, 28, 1 / 3,
                        False, 4)
    assert w["C0"] > 0, "fastest riser must be long"
    assert w["C5"] < 0, "fastest faller must be short"


def test_long_only_weights_are_positive_and_sum_to_one():
    dates, opens, closes = panel([0.03, 0.02, 0.01, 0.0, -0.01, -0.02])
    w = xs.rank_weights(list(opens), closes, opens, 100, 28, 1 / 3,
                        True, 4)
    assert all(v > 0 for v in w.values())
    assert sum(w.values()) == pytest.approx(1.0)


def test_a_coin_with_no_execution_price_is_never_ranked():
    """Ranking a coin you cannot fill earns returns on a position never held."""
    loaded = [(f"C{i}", series(ramp(200, r))) for i, r in
              enumerate([0.03, 0.02, 0.01, 0.0, -0.01, -0.02])]
    dates, opens, closes = xs.build_panel(loaded)
    opens["C0"][101] = None            # top-ranked coin cannot be traded
    w = xs.rank_weights(list(opens), closes, opens, 100, 28, 1 / 3, False, 4)
    assert "C0" not in w


def test_short_universe_produces_no_position_at_all():
    dates, opens, closes = panel([0.02, 0.01, -0.01])
    assert xs.rank_weights(list(opens), closes, opens, 100, 28, 1 / 3,
                           False, min_universe=4) == {}


def test_no_lookahead_truncating_the_future_changes_nothing():
    rates = [0.02, 0.01, 0.005, 0.0, -0.005, -0.02]
    tail = 260
    full = [(f"C{i}", series(ramp(320, r))) for i, r in enumerate(rates)]
    # Reverse every coin's trend after the cut, so a leak cannot hide.
    bent = []
    for name, rows in full:
        rows = [dict(r) for r in rows]
        for j in range(tail, len(rows)):
            for k in "ohlc":
                rows[j][k] = rows[tail - 1][k] * (0.95 ** (j - tail + 1))
        bent.append((name, rows))

    d1, o1, c1 = xs.build_panel(bent)
    d2, o2, c2 = xs.build_panel([(n, r[:tail]) for n, r in bent])
    a = xs.run(d1, o1, c1, 28, 7, 1 / 3, 8.0, 2.0, start=60, end=tail)
    b = xs.run(d2, o2, c2, 28, 7, 1 / 3, 8.0, 2.0, start=60, end=tail)
    assert a["return"] == pytest.approx(b["return"])


def test_holding_the_same_ranking_across_a_rebalance_is_free():
    """Steady ramps never re-rank, so after the first entry there is nothing
    to pay for. If costs kept accruing, turnover would be double-counted."""
    dates, opens, closes = panel([0.02, 0.015, 0.01, 0.0, -0.01, -0.02], n=500)
    r = xs.run(dates, opens, closes, 28, 7, 1 / 3, 50.0, 50.0, start=100)
    assert r["turnover_yr"] < 2.0
    assert r["cost_yr"] < 0.02


def test_costs_reduce_return_and_scale_with_turnover():
    dates, opens, closes = panel([0.02, -0.01, 0.015, -0.02, 0.005, 0.0])
    free = xs.run(dates, opens, closes, 28, 7, 1 / 3, 0.0, 0.0, start=60)
    paid = xs.run(dates, opens, closes, 28, 7, 1 / 3, 8.0, 2.0, start=60)
    assert free["costs"] == 0.0
    assert paid["costs"] > 0.0
    assert paid["return"] < free["return"]


def test_dollar_neutral_book_is_flat_when_every_coin_moves_together():
    """The whole premise: a common factor must cancel between the legs."""
    dates, opens, closes = panel([0.01] * 6, n=400)
    r = xs.run(dates, opens, closes, 28, 7, 1 / 3, 0.0, 0.0, start=60)
    assert abs(r["return"]) < 0.02


def test_dispersion_is_what_the_strategy_actually_earns():
    dates, opens, closes = panel([0.02, 0.015, 0.01, 0.0, -0.01, -0.02])
    r = xs.run(dates, opens, closes, 28, 7, 1 / 3, 0.0, 0.0, start=60)
    assert r["return"] > 0.0


def test_warmup_is_not_scored():
    dates, opens, closes = panel([0.02, 0.01, 0.0, -0.01, -0.02, 0.005])
    r = xs.run(dates, opens, closes, 28, 7, 1 / 3, 8.0, 2.0)
    assert len(r["daily"]) == len(dates) - 1 - (28 + 1)


def test_buy_and_hold_baseline_equal_weights_the_whole_universe():
    dates, opens, closes = panel([0.01, 0.01, 0.01, 0.01, 0.01, 0.01], n=200)
    start, end = 40, 150
    r = xs.run(dates, opens, closes, 28, 7, 1 / 3, 0.0, 0.0,
               start=start, end=end)
    # Scored days run from `start` to end-2 inclusive, so end-1-start returns.
    expected = 1.01 ** (end - 1 - start) - 1.0
    assert r["buy_hold"] == pytest.approx(expected, rel=1e-6)
    assert len(r["daily"]) == end - 1 - start


def test_sharpe_and_drawdown_helpers():
    assert xs._sharpe([0.0] * 30) == 0.0
    assert xs._max_drawdown([0.5, -0.5, 0.5]) == pytest.approx(-0.5)
