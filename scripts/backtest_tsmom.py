#!/usr/bin/env python3
"""Backtest time-series momentum on daily candles.

The claim being tested (Time-Series and Cross-Sectional Momentum in the
Cryptocurrency Market): a 28-day lookback with a 5-day hold reports Sharpe
1.51 against 0.84 for buy-and-hold, and weekly rebalancing beats daily
because turnover is what costs you.

Why this one is worth testing when four others have failed: it is the first
candidate whose turnover is survivable. ~73 trades a year at ~20bp round
trip is a 15% annual drag — heavy, but an argument rather than the 2993%
arithmetic impossibility of the 5-minute impulse.

Two decisions that keep this honest:

1. **Execution at opens only.** A signal computed from day i's close is
   acted on at day i+1's open, and returns are measured open-to-open. Using
   closes for both signal and fill is how a backtest invents an edge it
   cannot capture.

2. **The percentile threshold is trailing.** "Top third of its history"
   means the top third of history *available at the time*, over a trailing
   window. Ranking against the full sample would let 2026 decide what
   counted as a strong move in 2023.

Costs are charged only when the position actually changes, which is the
whole point of a 5-day hold: holding through a rebalance is free.

Usage:
    python scripts/backtest_tsmom.py klines/BTC_USDT_Day1.json
    python scripts/backtest_tsmom.py klines/*_Day1.json --sweep
    python scripts/backtest_tsmom.py klines/*_Day1.json --mode sign --long-only
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import statistics
import sys
from typing import List, Optional

TRADING_DAYS = 365          # crypto trades every day


def lookback_returns(closes: List[float], lookback: int) -> List[Optional[float]]:
    """Return over the trailing `lookback` days, or None before it exists."""
    out: List[Optional[float]] = [None] * len(closes)
    for i in range(lookback, len(closes)):
        prev = closes[i - lookback]
        if prev > 0:
            out[i] = closes[i] / prev - 1.0
    return out


def decide(rets: List[Optional[float]], i: int, mode: str, window: int,
           upper: float, lower: float, allow_shorts: bool) -> int:
    """Position to hold, decided using data up to and including day i."""
    r = rets[i]
    if r is None:
        return 0

    if mode == "sign":
        want = 1 if r > 0 else -1
    else:
        # Rank against a trailing window of past lookback-returns. Strictly
        # past: index i itself is excluded so the current observation cannot
        # help set the bar it has to clear.
        hist = [v for v in rets[max(0, i - window):i] if v is not None]
        if len(hist) < window // 2:
            return 0
        hi = _percentile(hist, upper)
        lo = _percentile(hist, lower)
        if r >= hi:
            want = 1
        elif r <= lo:
            want = -1
        else:
            want = 0

    if want < 0 and not allow_shorts:
        want = 0
    return want


def _percentile(values: List[float], pct: float) -> float:
    s = sorted(values)
    if not s:
        return float("inf")
    k = (len(s) - 1) * pct
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


def run(candles, lookback: int, hold: int, mode: str, fee_bps: float,
        slippage_bps: float, window: int = 365, upper: float = 2 / 3,
        lower: float = 1 / 3, allow_shorts: bool = True,
        start: int = 0, end: Optional[int] = None) -> dict:
    """Indicators use the whole series; P&L is scored only over [start, end).

    Splitting the *data* instead of the scoring window would hand the test
    segment a cold start — 28 days of no lookback and a year of no percentile
    history — and score a strategy that was flat for most of it.
    """
    closes = [c["c"] for c in candles]
    opens = [c["o"] for c in candles]
    n = len(candles)
    end = n if end is None else min(end, n)
    rets = lookback_returns(closes, lookback)
    cost_per_side = (fee_bps + slippage_bps) / 10000.0

    # Position held during day j, meaning from open[j] to open[j+1]. Decided
    # at the close of day j-1 at the latest, so nothing here can peek.
    pos = [0] * n
    i = max(lookback, start - 1)
    while i < n - 1:
        want = decide(rets, i, mode, window, upper, lower, allow_shorts)
        for j in range(i + 1, min(i + 1 + hold, n)):
            pos[j] = want
        i += hold

    # Do not score the warm-up. The percentile rule cannot fire until it has
    # a full trailing window, so including those days scores the strategy as
    # "flat" through a period it was never eligible to trade — which flatters
    # it in a crash and damns it in a rally, both dishonestly.
    warm = lookback + window if mode == "tercile" else lookback + 1
    begin = max(start, warm + 1)
    daily, equity, turns, costs = [], 1.0, 0, 0.0
    for j in range(begin, end - 1):
        change = abs(pos[j] - pos[j - 1])
        if change:
            turns += 1
            costs += change * cost_per_side
        gross = opens[j + 1] / opens[j] - 1.0
        net = pos[j] * gross - change * cost_per_side
        equity *= (1 + net)
        daily.append(net)

    # Buy-and-hold on the same open-to-open basis and the same window, so the
    # comparison is like for like rather than close-to-close against
    # open-to-open over a different span.
    bh_daily = [opens[j + 1] / opens[j] - 1.0 for j in range(begin, end - 1)]
    bh = opens[end - 1] / opens[begin] - 1.0 if end - 1 > begin else 0.0

    span = pos[begin:end]
    exposure = sum(1 for p in span if p != 0) / max(1, len(span))
    days = (candles[end - 1]["t"] - candles[begin]["t"]) / 86400

    return {
        "return": equity - 1.0,
        "buy_hold": bh,
        "sharpe": _sharpe(daily),
        "bh_sharpe": _sharpe(bh_daily),
        "max_dd": _max_drawdown(daily),
        "trades": turns,
        "trades_yr": turns / max(1e-9, days / 365.0),
        "costs": costs,
        "exposure": exposure,
        "days": days,
        "daily": daily,
        "bh_daily": bh_daily,
        "times": [candles[j]["t"] for j in range(begin, end - 1)],
    }


def _sharpe(daily: List[float]) -> float:
    if len(daily) < 2:
        return 0.0
    sd = statistics.pstdev(daily)
    if sd == 0:
        return 0.0
    return statistics.mean(daily) / sd * math.sqrt(TRADING_DAYS)


def _max_drawdown(daily: List[float]) -> float:
    eq, peak, worst = 1.0, 1.0, 0.0
    for r in daily:
        eq *= (1 + r)
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1.0)
    return worst


def show(label: str, r: dict) -> None:
    print(f"  {label:<20}{r['trades_yr']:>7.0f}{r['exposure']*100:>8.0f}%"
          f"{r['return']*100:>11.1f}%{r['buy_hold']*100:>11.1f}%"
          f"{r['sharpe']:>9.2f}{r['bh_sharpe']:>9.2f}"
          f"{r['max_dd']*100:>9.0f}%{r['costs']*100:>8.0f}%")


def header() -> None:
    print(f"  {'':20}{'trd/yr':>7}{'in mkt':>9}{'return':>11}{'buy&hold':>11}"
          f"{'sharpe':>9}{'bh sh':>9}{'maxDD':>10}{'cost':>8}")


def portfolio(results: List[dict]) -> dict:
    """Equal-weight the per-coin daily returns, aligned on date.

    This is the object you would actually run: one account spreading capital
    across every coin. Averaging the *per-coin returns* instead would let a
    coin that tripled paper over one that halved, which no real account does.
    """
    def blend(key: str) -> tuple:
        buckets: dict = {}
        for r in results:
            for t, v in zip(r["times"], r[key]):
                buckets.setdefault(t, []).append(v)
        times = sorted(buckets)
        return times, [sum(buckets[t]) / len(buckets[t]) for t in times]

    times, daily = blend("daily")
    _, bh_daily = blend("bh_daily")
    eq, bh_eq = 1.0, 1.0
    for r in daily:
        eq *= (1 + r)
    for r in bh_daily:
        bh_eq *= (1 + r)
    n = len(results)
    return {
        "return": eq - 1.0,
        "buy_hold": bh_eq - 1.0,
        "sharpe": _sharpe(daily),
        "bh_sharpe": _sharpe(bh_daily),
        "max_dd": _max_drawdown(daily),
        "trades": sum(r["trades"] for r in results),
        "trades_yr": sum(r["trades_yr"] for r in results) / max(1, n),
        "costs": sum(r["costs"] for r in results) / max(1, n),
        "exposure": sum(r["exposure"] for r in results) / max(1, n),
    }


def walk_forward(loaded, args) -> int:
    """Score fixed parameters over N sequential time blocks.

    A single train/test split cannot tell "this has no edge" apart from "the
    last 30% happened to be a bear market". Consecutive blocks can: an edge
    that only ever appears in one block is a regime, not an edge.

    Blocks are cut on absolute dates rather than index fractions, because the
    coins have different listing dates and index fractions would silently
    compare different calendar periods across coins.
    """
    lo = min(c[0]["t"] for _, c in loaded)
    hi = max(c[-1]["t"] for _, c in loaded)
    edges = [lo + (hi - lo) * k / args.blocks for k in range(args.blocks + 1)]

    print(f"WALK-FORWARD — {args.lookback}d/{args.hold}d held fixed, "
          f"{args.blocks} blocks across {len(loaded)} coins\n")
    print(f"  {'period':<26}{'coins':>6}{'trd/yr':>8}{'in mkt':>8}"
          f"{'return':>10}{'buy&hold':>10}{'sharpe':>9}{'bh sh':>8}")

    import time as _t
    positive = 0
    for k in range(args.blocks):
        a, b = edges[k], edges[k + 1]
        results = []
        for _, candles in loaded:
            start = next((i for i, c in enumerate(candles) if c["t"] >= a), None)
            end = next((i for i, c in enumerate(candles) if c["t"] >= b), len(candles))
            if start is None or end - start < 40:
                continue
            results.append(run(candles, args.lookback, args.hold, args.mode,
                               args.fee_bps, args.slippage_bps, args.window,
                               allow_shorts=not args.long_only,
                               start=start, end=end))
        if not results:
            continue
        r = portfolio(results)
        if r["sharpe"] > 0:
            positive += 1
        label = (f"{_t.strftime('%Y-%m', _t.gmtime(a))} to "
                 f"{_t.strftime('%Y-%m', _t.gmtime(b))}")
        print(f"  {label:<26}{len(results):>6}{r['trades_yr']:>8.0f}"
              f"{r['exposure']*100:>7.0f}%{r['return']*100:>9.1f}%"
              f"{r['buy_hold']*100:>9.1f}%{r['sharpe']:>9.2f}"
              f"{r['bh_sharpe']:>8.2f}")

    print(f"\n  {positive}/{args.blocks} blocks with a positive Sharpe.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="+")
    p.add_argument("--lookback", type=int, default=28)
    p.add_argument("--hold", type=int, default=5)
    p.add_argument("--mode", default="tercile", choices=("tercile", "sign"))
    p.add_argument("--fee-bps", type=float, default=8.0)
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--train-frac", type=float, default=0.7)
    p.add_argument("--window", type=int, default=365,
                   help="trailing window for the percentile rank")
    p.add_argument("--long-only", action="store_true")
    p.add_argument("--sweep", action="store_true",
                   help="grid over lookback and hold")
    p.add_argument("--blocks", type=int, default=0,
                   help="walk-forward: score N sequential time blocks with "
                        "fixed parameters, to separate 'no edge' from 'one "
                        "bad regime'")
    args = p.parse_args()

    paths = []
    for f in args.files:
        paths.extend(sorted(glob.glob(f)) or [f])

    rt = 2 * (args.fee_bps + args.slippage_bps)
    print(f"Time-series momentum — {args.lookback}d lookback / {args.hold}d hold, "
          f"{args.mode} rule, round trip ~{rt:.0f}bp"
          f"{' (long only)' if args.long_only else ''}\n")

    loaded = []
    for path in paths:
        try:
            with open(path) as fh:
                loaded.append((path, json.load(fh)))
        except (OSError, ValueError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)

    if args.blocks > 1:
        return walk_forward(loaded, args)

    segments: dict = {}
    sweeps: dict = {}
    for path in paths:
        try:
            with open(path) as fh:
                candles = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            continue
        if len(candles) < 400:
            print(f"{path}: too few candles", file=sys.stderr)
            continue

        name = path.split("/")[-1].replace(".json", "")
        days = (candles[-1]["t"] - candles[0]["t"]) / 86400
        print(f"{name}  ({len(candles):,} candles, {days:.0f} days)")

        if args.sweep:
            split = int(len(candles) * args.train_frac)
            for lb in (7, 14, 28, 56, 90):
                for hd in (1, 5, 7, 14, 28):
                    tr = run(candles, lb, hd, args.mode, args.fee_bps,
                             args.slippage_bps, args.window,
                             allow_shorts=not args.long_only, end=split)
                    te = run(candles, lb, hd, args.mode, args.fee_bps,
                             args.slippage_bps, args.window,
                             allow_shorts=not args.long_only, start=split)
                    sweeps.setdefault((lb, hd), ([], []))[0].append(tr)
                    sweeps[(lb, hd)][1].append(te)
            continue

        header()
        split = int(len(candles) * args.train_frac)
        for label, lo, hi in (("TRAIN (first 70%)", 0, split),
                              ("TEST  (last 30%)", split, len(candles)),
                              ("FULL", 0, len(candles))):
            r = run(candles, args.lookback, args.hold, args.mode, args.fee_bps,
                    args.slippage_bps, args.window,
                    allow_shorts=not args.long_only, start=lo, end=hi)
            show(label, r)
            segments.setdefault(label, []).append(r)
        print()

    if sweeps:
        print(f"EQUAL-WEIGHT PORTFOLIO sweep across {len(paths)} coins")
        print("Read this for a plateau, not for a winner: picking the best "
              "test cell\nis just overfitting the test set instead of the "
              "training set.\n")
        print(f"  {'lookback':>9}{'hold':>6}{'trd/yr':>9}"
              f"{'train ret':>11}{'train sh':>10}{'test ret':>11}{'test sh':>10}")
        for (lb, hd), (trs, tes) in sorted(sweeps.items()):
            tr, te = portfolio(trs), portfolio(tes)
            print(f"  {lb:>9}{hd:>6}{tr['trades_yr']:>9.0f}"
                  f"{tr['return']*100:>10.1f}%{tr['sharpe']:>10.2f}"
                  f"{te['return']*100:>10.1f}%{te['sharpe']:>10.2f}")
        print()
        return 0

    if len(paths) > 1 and segments:
        print(f"EQUAL-WEIGHT PORTFOLIO across {len(paths)} coins")
        header()
        for label, results in segments.items():
            show(label, portfolio(results))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
