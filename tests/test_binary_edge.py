"""Invariants for the Polymarket binary-edge measurement.

The claim this script makes is "a forecast must be right X% of the time to
break even", so the fee formula and the break-even arithmetic have to be
exactly right — an error there moves the bar the whole analysis is judged
against. The feature builder must also stay strictly backward-looking.
"""

import importlib.util
import math
import os

import pytest

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts",
                     "binary_edge.py")
_spec = importlib.util.spec_from_file_location("binary_edge", _PATH)
be = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(be)


def candles(prices, start_t=0):
    return [{"t": start_t + i * 300, "o": p, "h": p * 1.001, "l": p * 0.999,
             "c": p, "v": 100.0} for i, p in enumerate(prices)]


def test_taker_fee_matches_polymarkets_published_formula():
    # shares x 0.07 x price x (1 - price); $1.75 per 100 shares at 50c.
    assert be.taker_fee(0.50) * 100 == pytest.approx(1.75)
    assert be.taker_fee(0.50) == pytest.approx(0.07 * 0.5 * 0.5)


def test_taker_fee_peaks_at_the_midpoint():
    mid = be.taker_fee(0.50)
    assert mid > be.taker_fee(0.30)
    assert mid > be.taker_fee(0.70)
    assert be.taker_fee(0.0) == pytest.approx(0.0)
    assert be.taker_fee(1.0) == pytest.approx(0.0)


def test_fee_is_symmetric_around_the_midpoint():
    assert be.taker_fee(0.30) == pytest.approx(be.taker_fee(0.70))


def test_break_even_accuracy_at_a_50_cent_entry():
    # 1c spread -> buy at 0.51; fee at 0.51 is 0.07*0.51*0.49 = 0.017493.
    got = be.required_accuracy(0.50, 0.01)
    assert got == pytest.approx(0.51 + 0.07 * 0.51 * 0.49)
    assert got == pytest.approx(0.5275, abs=5e-4)


def test_required_accuracy_always_exceeds_the_price_paid():
    for p in (0.2, 0.4, 0.5, 0.6, 0.85):
        assert be.required_accuracy(p, 0.005) > p


def test_zero_cost_break_even_is_just_the_price():
    assert be.required_accuracy(0.5, 0.0, fee_rate=0.0) == pytest.approx(0.5)


def test_features_never_read_the_candle_being_predicted():
    """Mutating candle i+1 must not change the features used to predict it."""
    base = candles([100.0 * (1.001 ** i) for i in range(60)])
    cols = be.columns(base)
    before = be.features(cols, 40)

    bent = [dict(c) for c in base]
    for j in range(41, len(bent)):
        for k in "ohlc":
            bent[j][k] *= 5.0
    after = be.features(be.columns(bent), 40)
    assert before == after


def test_features_are_finite_even_on_a_flat_series():
    flat = candles([100.0] * 60)
    f = be.features(be.columns(flat), 40)
    assert all(math.isfinite(x) for x in f)


def test_build_labels_up_when_the_window_closes_above_its_open():
    rows = candles([100.0] * 40)
    rows.append({"t": 40 * 300, "o": 100.0, "h": 101.0, "l": 100.0,
                 "c": 101.0, "v": 1.0})
    rows.append({"t": 41 * 300, "o": 101.0, "h": 101.0, "l": 99.0,
                 "c": 99.0, "v": 1.0})
    X, y = be.build(rows)
    assert y[-2:] == [1, 0]
    assert len(X) == len(y)


def test_test_set_is_standardised_with_training_statistics():
    """Re-deriving mean and sd on the test set leaks its distribution."""
    Xtr = [[0.0], [2.0]]
    Xte = [[10.0]]
    _, mu, sd = be.standardise(Xtr)
    Zte, _, _ = be.standardise(Xte, mu, sd)
    assert mu == [1.0] and sd == [1.0]
    assert Zte == [[9.0]]


def test_logistic_learns_a_separable_signal():
    X = [[1.0], [1.2], [0.9], [-1.0], [-1.1], [-0.8]] * 20
    y = [1, 1, 1, 0, 0, 0] * 20
    w, b = be.fit_logistic(X, y, epochs=120, lr=1.0)
    probs = be.predict(w, b, [[1.0], [-1.0]])
    assert probs[0] > 0.5 > probs[1]


def test_predict_returns_probabilities():
    w, b = [0.5], 0.0
    for p in be.predict(w, b, [[-50.0], [0.0], [50.0]]):
        assert 0.0 <= p <= 1.0
    assert be.predict(w, b, [[0.0]])[0] == pytest.approx(0.5)
