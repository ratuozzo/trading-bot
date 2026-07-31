#!/usr/bin/env python3
"""Assign coins to timeframe tiers — or report that none of them qualify.

A coin is tradeable on a given lookback only if TWO independent things hold,
and picking on either one alone gives a badly wrong answer:

1. **Enough prints to fill the window.** Below ~2 trades per window the
   impulse reads 0.000% forever and the coin can never fire.

2. **Moves big enough to pay the round trip.** This is the one that gets
   missed. Measured over a real sample, BTC's 1-in-200 move over 60 seconds
   was 10.5bp against an 18bp round trip — a *rare* BTC minute did not cover
   the fees. Liquidity does not imply tradeable volatility, and the most
   liquid coins are often the calmest.

The tier ladder is geometric because thresholds must scale with the window.
A 0.25% move is a 1.4-sigma event over half a second and ordinary drift over
a minute; holding the threshold fixed would make long tiers fire constantly
on noise. The exponent is measured here rather than assumed — theory says
sqrt(T) for a random walk, but tick data shows roughly T^0.3, because prices
mean-revert at short horizons instead of random-walking.

Usage:
    python scripts/pick_tiers.py                      # scan, 5 min sample
    python scripts/pick_tiers.py --seconds 600        # longer = better tails
    python scripts/pick_tiers.py --fee-bps 6.4        # with the MX discount
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import urllib.request
from collections import defaultdict

import websockets

TICKER_URL = "https://contract.mexc.com/api/v1/contract/ticker"
WS_URL = "wss://contract.mexc.com/edge"

#: (name, lookback seconds). Geometric, so each tier is meaningfully distinct.
TIERS = [("flash", 0.5), ("fast", 2.0), ("mid", 8.0), ("slow", 30.0)]

NON_CRYPTO_HINTS = (
    "STOCK", "XAU", "XAG", "SILVER", "OIL", "SPX", "NAS", "SOXL", "KORU",
    "GOLD", "US30", "DAX", "NIKKEI",
)


def is_non_crypto(symbol: str) -> bool:
    return any(h in symbol.upper() for h in NON_CRYPTO_HINTS)


def fetch_turnover() -> dict:
    with urllib.request.urlopen(TICKER_URL, timeout=30) as r:
        data = json.load(r)
    out = {}
    for row in data.get("data", []):
        sym = row.get("symbol", "")
        if sym.endswith("_USDT"):
            try:
                out[sym] = float(row.get("amount24") or 0)
            except (TypeError, ValueError):
                out[sym] = 0.0
    return out


async def capture(symbols, seconds: float) -> dict:
    """Collect (timestamp, price) per contract from one connection."""
    series = defaultdict(list)
    async with websockets.connect(WS_URL, ping_interval=20) as ws:
        for sym in symbols:
            await ws.send(json.dumps({"method": "sub.deal", "param": {"symbol": sym}}))
            await asyncio.sleep(0.02)          # MEXC 403s on bursts

        async def pinger():
            while True:
                await asyncio.sleep(15)
                await ws.send(json.dumps({"method": "ping"}))

        ping = asyncio.create_task(pinger())
        loop = asyncio.get_event_loop()
        end = loop.time() + seconds
        try:
            while loop.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(end - loop.time(), 0.1))
                except asyncio.TimeoutError:
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("channel") != "push.deal" or not msg.get("symbol"):
                    continue
                data = msg.get("data")
                for d in (data if isinstance(data, list) else [data]):
                    try:
                        price = float(d["p"])
                        ts = float(d.get("t") or 0) / 1000.0
                    except (KeyError, TypeError, ValueError):
                        continue
                    if price > 0:
                        series[msg["symbol"]].append((ts, price))
        finally:
            ping.cancel()
    for s in series:
        series[s].sort()
    return series


def move_quantile(series, horizon: float, quant: float = 0.995):
    """|return| in bp at the given quantile, over `horizon` seconds."""
    out, j = [], 0
    for t, p in series:
        while j < len(series) and series[j][0] < t - horizon:
            j += 1
        if j == 0 or j >= len(series):
            continue
        ref = series[j - 1][1]
        if ref > 0:
            out.append(abs(p - ref) / ref)
    if len(out) < 30:
        return None
    out.sort()
    return out[min(int(len(out) * quant), len(out) - 1)] * 10000


def fit_exponent(series, horizons) -> float | None:
    """Slope of log(move) vs log(horizon). sqrt(T) would give 0.5."""
    pts = []
    for h in horizons:
        v = move_quantile(series, h)
        if v and v > 0:
            pts.append((math.log(h), math.log(v)))
    if len(pts) < 4:
        return None
    n = len(pts)
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    den = sum((x - mx) ** 2 for x, _ in pts)
    if den <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in pts) / den


async def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=300.0)
    p.add_argument("--candidates", type=int, default=40)
    p.add_argument("--fee-bps", type=float, default=8.0, help="taker fee, one way")
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--min-edge", type=float, default=2.0,
                   help="rare move must be at least this multiple of the round trip")
    p.add_argument("--include-nonc", action="store_true")
    args = p.parse_args()

    cost = 2 * args.fee_bps + args.slippage_bps

    print("Fetching turnover…", file=sys.stderr)
    turnover = fetch_turnover()
    ranked = sorted(turnover.items(), key=lambda kv: kv[1], reverse=True)
    if not args.include_nonc:
        ranked = [(s, v) for s, v in ranked if not is_non_crypto(s)]
    cands = [s for s, _ in ranked[: args.candidates]]

    print(f"Sampling {len(cands)} contracts for {args.seconds:.0f}s…", file=sys.stderr)
    series = await capture(cands, args.seconds)

    horizons = [t for _, t in TIERS] + [60.0]
    print(f"\nRound trip = {cost:.1f}bp "
          f"({args.fee_bps}bp fee x2 + {args.slippage_bps}bp slippage x2)\n")
    header = f"{'symbol':<14}{'trades/s':>9}" + "".join(
        f"{n+' '+str(t)+'s':>13}" for n, t in TIERS)
    print(header)
    print(" " * 23 + "".join(f"{'move / cost':>13}" for _ in TIERS))
    print("-" * len(header))

    assignments, exponents = {}, {}
    for sym in cands:
        s = series.get(sym, [])
        rate = len(s) / args.seconds
        if len(s) < 40:
            continue
        cells, chosen = [], None
        for name, look in TIERS:
            mv = move_quantile(s, look)
            ratio = (mv / cost) if mv else None
            cells.append(ratio)
            # Both tests: enough prints AND a move worth taking.
            if chosen is None and ratio and ratio >= args.min_edge and rate * look >= 2.0:
                chosen = (name, look, ratio, mv)
        print(f"{sym:<14}{rate:>9.2f}" + "".join(
            (f"{c:>13.2f}" if c else f"{'—':>13}") for c in cells))
        if chosen:
            assignments[sym] = chosen
        e = fit_exponent(s, horizons)
        if e is not None:
            exponents[sym] = e

    if exponents:
        # Weight by sample size: sparse coins bias the fit downward, because a
        # "0.5s return" measured across a 5s gap is not really a 0.5s return.
        best = sorted(exponents.items(), key=lambda kv: -len(series[kv[0]]))[:5]
        avg = sum(e for _, e in best) / len(best)
        print(f"\nScaling exponent from the 5 best-sampled coins: T^{avg:.2f} "
              f"(sqrt(T) would be 0.50)")
        print("Thresholds should scale by that, not by sqrt(T), or long tiers "
              "become far too strict to ever fire.")

    print(f"\n\nTier assignments (rare move >= {args.min_edge:g}x the round trip):\n")
    if not assignments:
        print("  NONE of the sampled contracts qualify at any tier.")
        print(f"  Their moves are too small relative to a {cost:.0f}bp round trip.")
        print("  Options: cut fees, sample a volatile period, or widen "
              "--candidates to reach more volatile contracts.")
        return 1

    by_tier = defaultdict(list)
    for sym, (name, look, ratio, mv) in assignments.items():
        by_tier[name].append((sym, look, ratio, mv))
    for name, look in TIERS:
        rows = by_tier.get(name, [])
        if not rows:
            continue
        print(f"  {name} ({look:g}s lookback):")
        for sym, _, ratio, mv in sorted(rows, key=lambda r: -r[2]):
            print(f"      {sym:<14} rare move {mv:>6.1f}bp = {ratio:>4.1f}x cost")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
