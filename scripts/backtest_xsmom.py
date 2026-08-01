#!/usr/bin/env python3
"""Backtest cross-sectional momentum on a panel of daily candles.

Where time-series momentum asks "is this coin above its own history?", this
asks "is this coin beating the *other coins*?" — rank the universe by
lookback return, go long the top slice and short the bottom slice in equal
size, rebalance on a fixed schedule.

The one structural reason to expect something different after 28d/5d failed:
the long and short legs cancel, so this never has to call market direction.
It is dollar-neutral by construction. Crypto's dominant risk factor is "all
coins move together", and that factor is exactly what gets subtracted here.

Same discipline as the time-series backtester, for the same reasons:

- signals from day i's close execute at day i+1's OPEN
- the lookback warm-up is not scored
- costs are charged on realised weight *changes*, so a coin that stays in
  the top slice across a rebalance is not re-bought

Ranking needs the whole panel aligned on dates, because a coin that is
missing on a given day must be excluded from that day's ranking rather than
silently ranked against stale prices.

Usage:
    python scripts/backtest_xsmom.py klines/*_Day1.json
    python scripts/backtest_xsmom.py klines/*_Day1.json --blocks 5
    python scripts/backtest_xsmom.py klines/*_Day1.json --sweep
    python scripts/backtest_xsmom.py klines/*_Day1.json --long-only
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import statistics
import sys
import time as _time
from typing import Dict, List, Optional

TRADING_DAYS = 365


def build_panel(loaded):
    """Align every coin onto one date axis. Missing days stay None."""
    dates = sorted({c["t"] for _, candles in loaded for c in candles})
    idx = {t: i for i, t in enumerate(dates)}
    opens: Dict[str, List[Optional[float]]] = {}
    closes: Dict[str, List[Optional[float]]] = {}
    for name, candles in loaded:
        o: List[Optional[float]] = [None] * len(dates)
        c: List[Optional[float]] = [None] * len(dates)
        for row in candles:
            j = idx[row["t"]]
            if row["o"] > 0 and row["c"] > 0:
                o[j] = row["o"]
                c[j] = row["c"]
        opens[name] = o
        closes[name] = c
    return dates, opens, closes


def rank_weights(names, closes, opens, j, lookback, quantile, long_only,
                 min_universe):
    """Weights decided from closes up to day j, tradeable at open j+1.

    A coin is only eligible if it has a full lookback AND a price at both
    ends of the holding leg — ranking a coin you cannot fill is how a
    backtest quietly earns returns on positions it never held.
    """
    scored = []
    for n in names:
        c_now, c_then = closes[n][j], closes[n][j - lookback]
        if c_now is None or c_then is None or c_then <= 0:
            continue
        if opens[n][j + 1] is None:
            continue
        scored.append((c_now / c_then - 1.0, n))

    if len(scored) < min_universe:
        return {}

    scored.sort()
    k = max(1, int(len(scored) * quantile))
    winners = [n for _, n in scored[-k:]]
    losers = [n for _, n in scored[:k]]

    w: Dict[str, float] = {}
    if long_only:
        for n in winners:
            w[n] = 1.0 / len(winners)
    else:
        # Gross exposure 1.0, net 0.0: half the capital long, half short.
        for n in winners:
            w[n] = 0.5 / len(winners)
        for n in losers:
            w[n] = w.get(n, 0.0) - 0.5 / len(losers)
    return w


def run(dates, opens, closes, lookback: int, hold: int, quantile: float,
        fee_bps: float, slippage_bps: float, long_only: bool = False,
        min_universe: int = 4, start: int = 0,
        end: Optional[int] = None) -> dict:
    names = list(opens)
    n = len(dates)
    end = n if end is None else min(end, n)
    cost_per_side = (fee_bps + slippage_bps) / 10000.0

    begin = max(start, lookback + 1)
    weights: List[Dict[str, float]] = [{} for _ in range(n)]
    i = max(lookback, begin - 1)
    while i < n - 1:
        w = rank_weights(names, closes, opens, i, lookback, quantile,
                         long_only, min_universe)
        for j in range(i + 1, min(i + 1 + hold, n)):
            weights[j] = w
        i += hold

    daily, bh_daily, equity, costs, turn, gross = [], [], 1.0, 0.0, 0.0, []
    for j in range(begin, end - 1):
        w, prev = weights[j], weights[j - 1]

        # Charge on the change in weight, not on holding it.
        delta = sum(abs(w.get(k, 0.0) - prev.get(k, 0.0))
                    for k in set(w) | set(prev))
        costs += delta * cost_per_side
        turn += delta

        r = 0.0
        for name, wt in w.items():
            a, b = opens[name][j], opens[name][j + 1]
            if a is None or b is None or a <= 0:
                continue
            r += wt * (b / a - 1.0)
        net = r - delta * cost_per_side
        equity *= (1 + net)
        daily.append(net)
        gross.append(sum(abs(v) for v in w.values()))

        # Buy-and-hold the whole universe, equal weight, on the same days.
        live = [(opens[nm][j], opens[nm][j + 1]) for nm in names
                if opens[nm][j] and opens[nm][j + 1]]
        bh_daily.append(sum(b / a - 1.0 for a, b in live) / len(live)
                        if live else 0.0)

    eq_bh = 1.0
    for r in bh_daily:
        eq_bh *= (1 + r)
    days = max(1e-9, (dates[end - 1] - dates[begin]) / 86400) if end > begin else 1.0

    return {
        "return": equity - 1.0,
        "buy_hold": eq_bh - 1.0,
        "sharpe": _sharpe(daily),
        "bh_sharpe": _sharpe(bh_daily),
        "max_dd": _max_drawdown(daily),
        "turnover_yr": turn / (days / 365.0),
        "costs": costs,
        "cost_yr": costs / (days / 365.0),
        "gross": statistics.mean(gross) if gross else 0.0,
        "days": days,
        "daily": daily,
    }


def _sharpe(daily: List[float]) -> float:
    if len(daily) < 2:
        return 0.0
    sd = statistics.pstdev(daily)
    return statistics.mean(daily) / sd * math.sqrt(TRADING_DAYS) if sd else 0.0


def _max_drawdown(daily: List[float]) -> float:
    eq, peak, worst = 1.0, 1.0, 0.0
    for r in daily:
        eq *= (1 + r)
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1.0)
    return worst


def header() -> None:
    print(f"  {'':26}{'turn/yr':>9}{'gross':>7}{'return':>11}{'buy&hold':>11}"
          f"{'sharpe':>9}{'bh sh':>8}{'maxDD':>9}{'cost/yr':>9}")


def show(label: str, r: dict) -> None:
    print(f"  {label:<26}{r['turnover_yr']:>9.1f}{r['gross']:>7.2f}"
          f"{r['return']*100:>10.1f}%{r['buy_hold']*100:>10.1f}%"
          f"{r['sharpe']:>9.2f}{r['bh_sharpe']:>8.2f}"
          f"{r['max_dd']*100:>8.0f}%{r['cost_yr']*100:>8.1f}%")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="+")
    p.add_argument("--lookback", type=int, default=28)
    p.add_argument("--hold", type=int, default=7)
    p.add_argument("--quantile", type=float, default=1 / 3)
    p.add_argument("--fee-bps", type=float, default=8.0)
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--train-frac", type=float, default=0.7)
    p.add_argument("--long-only", action="store_true")
    p.add_argument("--blocks", type=int, default=0)
    p.add_argument("--sweep", action="store_true")
    args = p.parse_args()

    paths = []
    for f in args.files:
        paths.extend(sorted(glob.glob(f)) or [f])

    loaded = []
    for path in paths:
        try:
            with open(path) as fh:
                loaded.append((path.split("/")[-1].replace(".json", ""),
                               json.load(fh)))
        except (OSError, ValueError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
    if len(loaded) < 4:
        print("cross-sectional ranking needs at least 4 coins", file=sys.stderr)
        return 1

    dates, opens, closes = build_panel(loaded)
    rt = 2 * (args.fee_bps + args.slippage_bps)
    span = (dates[-1] - dates[0]) / 86400
    print(f"Cross-sectional momentum — {args.lookback}d rank / {args.hold}d hold, "
          f"top&bottom {args.quantile*100:.0f}%, round trip ~{rt:.0f}bp"
          f"{' (long only)' if args.long_only else ' (dollar-neutral)'}")
    print(f"{len(loaded)} coins, {len(dates):,} days, {span:.0f} days span\n")

    def go(**kw):
        return run(dates, opens, closes, args.lookback, args.hold,
                   args.quantile, args.fee_bps, args.slippage_bps,
                   long_only=args.long_only, **kw)

    if args.sweep:
        print("Read this for a plateau, not for a winner.\n")
        print(f"  {'rank':>6}{'hold':>6}{'quant':>7}{'turn/yr':>9}"
              f"{'train ret':>11}{'train sh':>10}{'test ret':>11}{'test sh':>10}")
        split = int(len(dates) * args.train_frac)
        for lb in (7, 14, 28, 56, 90):
            for hd in (1, 7, 14, 28):
                for q in (0.2, 1 / 3):
                    tr = run(dates, opens, closes, lb, hd, q, args.fee_bps,
                             args.slippage_bps, long_only=args.long_only,
                             end=split)
                    te = run(dates, opens, closes, lb, hd, q, args.fee_bps,
                             args.slippage_bps, long_only=args.long_only,
                             start=split)
                    print(f"  {lb:>6}{hd:>6}{q*100:>6.0f}%{tr['turnover_yr']:>9.1f}"
                          f"{tr['return']*100:>10.1f}%{tr['sharpe']:>10.2f}"
                          f"{te['return']*100:>10.1f}%{te['sharpe']:>10.2f}")
        return 0

    if args.blocks > 1:
        print(f"WALK-FORWARD — parameters held fixed, {args.blocks} blocks\n")
        print(f"  {'period':<26}{'turn/yr':>9}{'gross':>7}{'return':>11}"
              f"{'buy&hold':>11}{'sharpe':>9}{'bh sh':>8}")
        edges = [int(len(dates) * k / args.blocks) for k in range(args.blocks + 1)]
        positive = 0
        for k in range(args.blocks):
            lo, hi = edges[k], edges[k + 1]
            if hi - lo < 60:
                continue
            r = go(start=lo, end=hi)
            if r["sharpe"] > 0:
                positive += 1
            label = (f"{_time.strftime('%Y-%m', _time.gmtime(dates[lo]))} to "
                     f"{_time.strftime('%Y-%m', _time.gmtime(dates[hi - 1]))}")
            print(f"  {label:<26}{r['turnover_yr']:>9.1f}{r['gross']:>7.2f}"
                  f"{r['return']*100:>10.1f}%{r['buy_hold']*100:>10.1f}%"
                  f"{r['sharpe']:>9.2f}{r['bh_sharpe']:>8.2f}")
        print(f"\n  {positive}/{args.blocks} blocks with a positive Sharpe.")
        return 0

    header()
    split = int(len(dates) * args.train_frac)
    for label, lo, hi in (("TRAIN (first 70%)", 0, split),
                          ("TEST  (last 30%)", split, len(dates)),
                          ("FULL", 0, len(dates))):
        show(label, go(start=lo, end=hi))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
