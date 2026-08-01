#!/usr/bin/env python3
"""Backtest Elder's Impulse System on 5-minute klines.

The system (Alexander Elder): a 13-period EMA gives trend, the MACD
histogram gives momentum, and you only act when the two agree.

    green  (bullish): EMA rising  AND MACD histogram rising
    red    (bearish): EMA falling AND MACD histogram falling
    blue   (neutral): they disagree — no position

Two things this is careful about, because both quietly manufacture profits
that do not exist:

1. **Look-ahead.** Indicators are computed on *closed* candles only, and a
   signal from candle i is executed at candle i+1's OPEN. Entering at the
   close of the candle that generated the signal is the single most common
   way a backtest invents an edge it cannot capture.

2. **Overfitting.** Results are split into train and test. The parameters
   are Elder's published defaults rather than anything fitted here, but the
   split still shows whether behaviour holds up out of sample. The Freqtrade
   community's rule of thumb is worth repeating: a strategy showing +50% and
   a 75% win rate in backtest routinely delivers -10% and 40% live.

Buy-and-hold is reported alongside, because a strategy that makes 20% while
the asset made 50% has not made money — it has lost 30% of the alternative.

Usage:
    python scripts/backtest_impulse.py klines/BTC_USDT_Min5.json
    python scripts/backtest_impulse.py klines/*.json --invert
    python scripts/backtest_impulse.py klines/*.json --fee-bps 6.4
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from typing import List, Optional


def ema(values: List[float], period: int) -> List[Optional[float]]:
    """Standard EMA, seeded with an SMA so the head isn't distorted."""
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def macd_histogram(closes: List[float], fast=12, slow=26, signal=9):
    ef, es = ema(closes, fast), ema(closes, slow)
    macd: List[Optional[float]] = [
        (ef[i] - es[i]) if (ef[i] is not None and es[i] is not None) else None
        for i in range(len(closes))
    ]
    # The signal EMA runs over the MACD line, which only exists after `slow`.
    start = next((i for i, v in enumerate(macd) if v is not None), len(macd))
    tail = [v for v in macd[start:] if v is not None]
    sig_tail = ema(tail, signal)
    sig: List[Optional[float]] = [None] * len(closes)
    for j, v in enumerate(sig_tail):
        sig[start + j] = v
    hist: List[Optional[float]] = [
        (macd[i] - sig[i]) if (macd[i] is not None and sig[i] is not None) else None
        for i in range(len(closes))
    ]
    return hist


def impulse_colours(closes: List[float], ema_period=13) -> List[Optional[int]]:
    """+1 green, -1 red, 0 blue/neutral, None when indicators aren't ready."""
    e = ema(closes, ema_period)
    h = macd_histogram(closes)
    out: List[Optional[int]] = [None] * len(closes)
    for i in range(1, len(closes)):
        if None in (e[i], e[i - 1], h[i], h[i - 1]):
            continue
        ema_up, hist_up = e[i] > e[i - 1], h[i] > h[i - 1]
        if ema_up and hist_up:
            out[i] = 1
        elif not ema_up and not hist_up:
            out[i] = -1
        else:
            out[i] = 0
    return out


def higher_tf_colours(candles, factor: int) -> List[Optional[int]]:
    """Impulse colour on a timeframe `factor` times higher, mapped back onto
    every base candle. Elder suggests filtering with 5x the trading window."""
    groups, closes_htf, index_of = [], [], []
    for i in range(0, len(candles), factor):
        chunk = candles[i:i + factor]
        if not chunk:
            continue
        groups.append(i + len(chunk) - 1)     # index where this HTF bar closes
        closes_htf.append(chunk[-1]["c"])
    htf = impulse_colours(closes_htf)
    out: List[Optional[int]] = [None] * len(candles)
    for g, colour in zip(groups, htf):
        # Only valid from the bar AFTER the higher-timeframe bar closes.
        for j in range(g + 1, min(g + 1 + factor, len(candles))):
            out[j] = colour
    return out


def run(candles, fee_bps: float, slippage_bps: float, invert: bool,
        allow_shorts: bool = True, hold_neutral: bool = True,
        htf_factor: int = 0) -> dict:
    closes = [c["c"] for c in candles]
    opens = [c["o"] for c in candles]
    colours = impulse_colours(closes)
    htf = higher_tf_colours(candles, htf_factor) if htf_factor else None

    fee = fee_bps / 10000.0
    slip = slippage_bps / 10000.0

    equity = 1.0
    pos = 0                      # +1 long, -1 short, 0 flat
    entry = 0.0
    trades, wins, gross_fees = [], 0, 0.0

    for i in range(len(candles) - 1):
        c = colours[i]
        if c is None:
            continue
        want = c if not invert else -c

        # Elder's system censors rather than commands: neutral means "no new
        # signal", not "get out". Exiting on every neutral bar round-trips
        # every 2-3 candles and pays the spread for nothing.
        if want == 0 and hold_neutral:
            continue

        # Trade only in the direction the higher timeframe permits.
        if htf is not None and want != 0:
            h = htf[i]
            if h is None or (h != 0 and h != want):
                want = 0

        if not allow_shorts and want < 0:
            want = 0

        if want == pos:
            continue

        # A signal on a closed candle can only be acted on at the next open.
        px = opens[i + 1]

        if pos != 0:
            exit_px = px * (1 - pos * slip)
            ret = pos * (exit_px - entry) / entry
            ret -= 2 * fee                  # entry and exit fee
            equity *= (1 + ret)
            trades.append(ret)
            gross_fees += 2 * fee
            if ret > 0:
                wins += 1
            pos = 0

        if want != 0:
            pos = want
            entry = px * (1 + want * slip)

    # Close anything still open at the final price, so a losing open position
    # cannot flatter the result by simply not being counted.
    if pos != 0:
        exit_px = closes[-1] * (1 - pos * slip)
        ret = pos * (exit_px - entry) / entry - 2 * fee
        equity *= (1 + ret)
        trades.append(ret)
        if ret > 0:
            wins += 1

    hold = closes[-1] / closes[0] - 1.0
    return {
        "trades": len(trades),
        "win_rate": wins / len(trades) if trades else 0.0,
        "return": equity - 1.0,
        "buy_hold": hold,
        "avg_trade": sum(trades) / len(trades) if trades else 0.0,
        "best": max(trades) if trades else 0.0,
        "worst": min(trades) if trades else 0.0,
        "fees_paid": gross_fees,
    }


def show(label: str, r: dict) -> None:
    print(f"  {label:<22}{r['trades']:>7}{r['win_rate']*100:>8.1f}%"
          f"{r['return']*100:>11.1f}%{r['buy_hold']*100:>12.1f}%"
          f"{r['avg_trade']*10000:>11.1f}bp")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="+")
    p.add_argument("--fee-bps", type=float, default=8.0)
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--train-frac", type=float, default=0.7)
    p.add_argument("--long-only", action="store_true")
    p.add_argument("--exit-on-neutral", action="store_true",
                   help="flatten on neutral bars (mechanical reading; trades a lot)")
    p.add_argument("--htf", type=int, default=0,
                   help="higher-timeframe filter factor, e.g. 5 for 25m")
    args = p.parse_args()

    paths = []
    for f in args.files:
        paths.extend(sorted(glob.glob(f)) or [f])

    cost = 2 * args.fee_bps + 2 * args.slippage_bps
    print(f"Elder Impulse — round trip ~{cost:.0f}bp"
          f"{' (long only)' if args.long_only else ''}\n")

    for path in paths:
        try:
            with open(path) as fh:
                candles = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            continue
        if len(candles) < 500:
            print(f"{path}: too few candles", file=sys.stderr)
            continue

        base = path.split("/")[-1].replace(".json", "")
        name = base
        split = int(len(candles) * args.train_frac)
        days = (candles[-1]["t"] - candles[0]["t"]) / 86400
        print(f"{name}  ({len(candles):,} candles, {days:.0f} days)")
        print(f"  {'':22}{'trades':>7}{'win%':>9}{'return':>11}"
              f"{'buy&hold':>12}{'avg trade':>13}")

        for seg_name, seg in (("TRAIN (first 70%)", candles[:split]),
                              ("TEST  (last 30%)", candles[split:])):
            for mode, inv in (("impulse", False), ("inverted (fade)", True)):
                r = run(seg, args.fee_bps, args.slippage_bps, inv,
                        allow_shorts=not args.long_only,
                        hold_neutral=not args.exit_on_neutral,
                        htf_factor=args.htf)
                show(f"{seg_name} {mode}" if mode == "impulse"
                     else f"{'':18}{mode}", r)
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
