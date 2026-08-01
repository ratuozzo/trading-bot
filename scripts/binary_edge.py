#!/usr/bin/env python3
"""How accurate must a 5-minute direction call be to profit on Polymarket?

Prediction markets look free because there is no percentage fee on a
notional. There is still a cost, and for short-horizon crypto contracts it is
large — it is just denominated in probability instead of basis points.

Buying one share at price p pays $1 if right and $0 if wrong, so the
break-even probability IS the price. Every cost therefore converts directly
into required forecasting accuracy:

    required accuracy = p + taker_fee(p) + half_spread

Polymarket's crypto taker fee is  shares x 0.07 x p x (1 - p), which peaks
at exactly p = 0.50 — the price at which a fresh 5-minute market opens. A
1.75c fee plus a 1c half-spread on a 50c contract means a coin-flip entry
must be right **52.75%** of the time simply to break even.

This script measures the other half: how accurate a 5-minute direction call
can actually be made from price history. It fits a logistic regression on
lagged returns, volatility, volume and time-of-day, then reports accuracy on
a held-out period — including accuracy restricted to the most confident
predictions, since the plan is to bet only when the signal is "clear".

Everything is pure Python: no numpy on this box, and the model is small
enough not to need it.

Usage:
    python scripts/binary_edge.py klines/m5/BTC_USDT_Min5.json
    python scripts/binary_edge.py klines/m5/BTC_USDT_Min5.json --spread-cents 1
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from typing import List, Sequence, Tuple

FEE_RATE = 0.07          # Polymarket crypto up/down taker fee coefficient


def taker_fee(price: float, fee_rate: float = FEE_RATE) -> float:
    """Fee per share, in dollars. Peaks at p = 0.5."""
    return fee_rate * price * (1.0 - price)


def required_accuracy(price: float, half_spread: float,
                      fee_rate: float = FEE_RATE) -> float:
    """Win rate needed to break even buying at `price` plus costs."""
    return price + half_spread + taker_fee(price + half_spread, fee_rate)


def features(cols, i: int) -> List[float]:
    """Everything knowable at the close of candle i, to predict candle i+1.

    Strictly backward-looking: index i+1 is never touched, because the whole
    question is whether the future is predictable from the past.

    `cols` is the pre-extracted column arrays. Rebuilding them per call turns
    this into a quadratic scan of the whole file.
    """
    candles, c, o, v, h, lo = cols

    def ret(lag: int) -> float:
        prev = c[i - lag]
        return (c[i] / prev - 1.0) if prev > 0 else 0.0

    rets = [(c[j] / c[j - 1] - 1.0) if c[j - 1] > 0 else 0.0
            for j in range(i - 11, i + 1)]
    vol = statistics.pstdev(rets) or 1e-9
    vmean = statistics.mean(v[i - 11:i + 1]) or 1e-9
    rng = (h[i] - lo[i]) / c[i] if c[i] > 0 else 0.0

    # Returns are scaled by realised volatility: a 10bp move means something
    # different in a calm hour than in a violent one, and the raw number
    # would let the model learn the volatility regime instead of direction.
    f = [
        ret(1) / vol, ret(2) / vol, ret(3) / vol,
        ret(6) / vol, ret(12) / vol, ret(24) / vol,
        rng / vol * 0.01,
        v[i] / vmean - 1.0,
        (c[i] - o[i]) / c[i] / vol if c[i] > 0 else 0.0,
        math.sin(2 * math.pi * (candles[i] % 86400) / 86400),
        math.cos(2 * math.pi * (candles[i] % 86400) / 86400),
    ]
    return [x if math.isfinite(x) else 0.0 for x in f]


def columns(candles):
    return ([x["t"] for x in candles], [x["c"] for x in candles],
            [x["o"] for x in candles], [x["v"] for x in candles],
            [x["h"] for x in candles], [x["l"] for x in candles])


def build(candles) -> Tuple[List[List[float]], List[int]]:
    cols = columns(candles)
    X, y = [], []
    for i in range(24, len(candles) - 1):
        nxt = candles[i + 1]
        if nxt["o"] <= 0 or nxt["c"] <= 0:
            continue
        X.append(features(cols, i))
        # The contract asks: is the close above the open of that window?
        y.append(1 if nxt["c"] > nxt["o"] else 0)
    return X, y


def standardise(X: List[List[float]], mu=None, sd=None):
    n = len(X[0])
    if mu is None:
        mu = [statistics.mean(col) for col in zip(*X)]
        sd = [statistics.pstdev(col) or 1.0 for col in zip(*X)]
    Z = [[(row[k] - mu[k]) / sd[k] for k in range(n)] for row in X]
    return Z, mu, sd


def fit_logistic(X: Sequence[Sequence[float]], y: Sequence[int],
                 epochs: int = 60, lr: float = 0.5, l2: float = 1e-3):
    n = len(X[0])
    w = [0.0] * n
    b = 0.0
    m = len(X)
    for _ in range(epochs):
        gw = [0.0] * n
        gb = 0.0
        for xi, yi in zip(X, y):
            z = b + sum(w[k] * xi[k] for k in range(n))
            p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            d = p - yi
            gb += d
            for k in range(n):
                gw[k] += d * xi[k]
        b -= lr * gb / m
        for k in range(n):
            w[k] -= lr * (gw[k] / m + l2 * w[k])
    return w, b


def predict(w, b, X) -> List[float]:
    out = []
    for xi in X:
        z = b + sum(w[k] * xi[k] for k in range(len(w)))
        out.append(1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z)))))
    return out


def selective(probs: List[float], y: List[int], bar: float) -> None:
    """Accuracy when betting only on the most confident predictions.

    The proposal is to bet only when direction is "clear", so overall
    accuracy is the wrong statistic — this is the right one.
    """
    print(f"  {'bet on top':>12}{'n bets':>9}{'accuracy':>11}"
          f"{'needed':>9}{'edge':>9}")
    order = sorted(range(len(probs)), key=lambda i: -abs(probs[i] - 0.5))
    for frac in (1.0, 0.5, 0.25, 0.10, 0.05, 0.01):
        k = max(1, int(len(order) * frac))
        sel = order[:k]
        hits = sum(1 for i in sel
                   if (probs[i] > 0.5) == (y[i] == 1))
        acc = hits / k
        edge = (acc - bar) * 100
        flag = "" if edge <= 0 else "   <-- clears the bar"
        print(f"  {frac*100:>11.0f}%{k:>9,}{acc*100:>10.2f}%"
              f"{bar*100:>8.2f}%{edge:>+8.2f}pp{flag}")


def walk_forward(candles, blocks: int, bar: float, top: float) -> None:
    """Expanding-window retrain: each block is predicted by a model that has
    only ever seen earlier data. A single split has flattered every strategy
    in this repo at least once; consecutive blocks are harder to fool."""
    X, y = build(candles)
    edge = len(X) // (blocks + 1)
    print(f"\nWALK-FORWARD — retrained per block, betting the top "
          f"{top*100:.0f}% most confident")
    print(f"  {'block':>7}{'train n':>10}{'bets':>8}{'accuracy':>11}"
          f"{'needed':>9}{'edge':>10}")
    wins = 0
    for k in range(1, blocks + 1):
        cut = edge * k
        Xtr, ytr = X[:cut], y[:cut]
        Xte, yte = X[cut:cut + edge], y[cut:cut + edge]
        if len(Xte) < 500:
            continue
        Ztr, mu, sd = standardise(Xtr)
        Zte, _, _ = standardise(Xte, mu, sd)
        w, b = fit_logistic(Ztr, ytr)
        pte = predict(w, b, Zte)
        order = sorted(range(len(pte)), key=lambda i: -abs(pte[i] - 0.5))
        n = max(1, int(len(order) * top))
        sel = order[:n]
        acc = sum(1 for i in sel if (pte[i] > 0.5) == (yte[i] == 1)) / n
        if acc > bar:
            wins += 1
        print(f"  {k:>7}{len(Xtr):>10,}{n:>8,}{acc*100:>10.2f}%"
              f"{bar*100:>8.2f}%{(acc-bar)*100:>+9.2f}pp")
    print(f"\n  {wins}/{blocks} blocks cleared the break-even bar.")


def latency_budget(candles, edge_pp: float) -> None:
    """How much of an edge over the opening quote survives a slow fill.

    A 5-minute up/down contract is a digital option struck at spot, so at
    window open the fair probability sits on the steepest part of the curve:
    dP/dS = phi(0)/sigma. Crypto's 5-minute sigma is ~14bp, so the fair price
    moves ~2.8 percentage points for every basis point the underlying ticks.

    Latency costs money through *adverse selection*, not through noise. A
    resting quote you are shooting at gets pulled when it moves in your
    favour and stays put when it moves against you, so conditional on being
    filled you paid too much. For a normal move of standard deviation s the
    expected damage is E[|Z|]/2 = 0.3989 * s.

    Note this is a decay, not a cliff. Comparing the edge to a one-standard-
    deviation move (as an earlier version of this did) understates the
    tolerable latency by roughly 6x.
    """
    r = [(b["c"] / a["c"] - 1.0) for a, b in zip(candles, candles[1:])
         if a["c"] > 0]
    sig = statistics.pstdev(r) * 10000
    dP = 0.3989 / sig * 100                # pp of probability per bp of spot

    print(f"\nLATENCY BUDGET  (5-min sigma {sig:.2f}bp)")
    print(f"  fair price moves {dP:.2f}pp per 1bp of underlying")
    print(f"  measured edge    {edge_pp:+.2f}pp over the all-in break-even\n")
    print(f"  {'flight':>9}{'price sd':>12}{'adverse sel':>14}{'edge left':>12}")
    for t in (0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 5.0, 10.0):
        s = dP * sig * math.sqrt(t / 300.0)
        adv = 0.3989 * s
        left = edge_pp - adv
        mark = "" if left > 0 else "   <-- gone"
        print(f"  {t:>7.2f}s{s:>10.2f}pp{adv:>13.2f}pp{left:>11.2f}pp{mark}")

    secs = 300 * ((edge_pp / 0.3989) / (dP * sig)) ** 2
    print(f"\n  break-even flight time: {secs:.2f}s")
    print("  (sigma cancels, so this depends on the edge alone, not the coin)")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file")
    p.add_argument("--blocks", type=int, default=0,
                   help="expanding-window walk-forward with this many blocks")
    p.add_argument("--top", type=float, default=0.10,
                   help="fraction of most-confident calls to bet")
    p.add_argument("--edge-pp", type=float, default=2.35,
                   help="measured edge in percentage points, for the "
                        "latency budget")
    p.add_argument("--spread-cents", type=float, default=2.0,
                   help="full bid-ask spread in cents on a $1 contract")
    p.add_argument("--train-frac", type=float, default=0.7)
    args = p.parse_args()

    with open(args.file) as fh:
        candles = json.load(fh)
    candles.sort(key=lambda c: c["t"])
    days = (candles[-1]["t"] - candles[0]["t"]) / 86400

    half = args.spread_cents / 200.0        # cents -> dollars, then halved
    bar = required_accuracy(0.50, half)

    print("Polymarket 5-minute BTC up/down — what the bet actually costs\n")
    print(f"  taker fee at 50c   {taker_fee(0.50)*100:.2f}c per share "
          f"= {taker_fee(0.50)/0.50*100:.2f}% of stake")
    print(f"  half spread        {half*100:.2f}c "
          f"= {half/0.50*100:.2f}% of stake")
    print(f"  break-even accuracy {bar*100:.2f}%  "
          f"(a coin flip priced at 50c must be right this often)\n")

    print("  Required accuracy by entry price:")
    print(f"  {'price':>8}{'fee/share':>12}{'fee % stake':>13}{'needed':>10}")
    for price in (0.50, 0.60, 0.70, 0.80, 0.90):
        f = taker_fee(price + half)
        print(f"  {price*100:>7.0f}c{f*100:>11.2f}c"
              f"{f/price*100:>12.2f}%"
              f"{required_accuracy(price, half)*100:>9.2f}%")

    if args.blocks > 1:
        walk_forward(candles, args.blocks, bar, args.top)
        latency_budget(candles, args.edge_pp)
        return 0

    X, y = build(candles)
    split = int(len(X) * args.train_frac)
    Xtr, ytr = X[:split], y[:split]
    Xte, yte = X[split:], y[split:]

    Ztr, mu, sd = standardise(Xtr)
    Zte, _, _ = standardise(Xte, mu, sd)

    print(f"\n{len(candles):,} five-minute candles, {days:.0f} days")
    print(f"  train {len(Xtr):,} windows / test {len(Xte):,} windows")
    base = sum(yte) / len(yte)
    print(f"  base rate up (test)  {base*100:.2f}%  "
          f"— always-up scores this and needs {bar*100:.2f}%\n")

    w, b = fit_logistic(Ztr, ytr)
    ptr = predict(w, b, Ztr)
    pte = predict(w, b, Zte)
    acc_tr = sum(1 for i in range(len(ytr))
                 if (ptr[i] > 0.5) == (ytr[i] == 1)) / len(ytr)
    acc_te = sum(1 for i in range(len(yte))
                 if (pte[i] > 0.5) == (yte[i] == 1)) / len(yte)
    print(f"  logistic regression   train {acc_tr*100:.2f}%   "
          f"test {acc_te*100:.2f}%\n")

    print("Test-set accuracy when betting only on the most confident calls:")
    selective(pte, yte, bar)

    print("\n(In-sample, for comparison — this is what overfitting looks like:)")
    selective(ptr, ytr, bar)
    return 0


if __name__ == "__main__":
    sys.exit(main())
