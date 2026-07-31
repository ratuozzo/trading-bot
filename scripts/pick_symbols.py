#!/usr/bin/env python3
"""Pick a watchlist MEXC can actually feed you.

Two things have to be true for a contract to be worth watching, and they are
not the same thing:

1. **Trade frequency.** The strategy measures an impulse over a lookback
   window. If a coin prints less than about twice per second, a 1-second
   window holds a single sample and its impulse reads exactly 0.000% forever
   — it can never fire, however violently it moves.

2. **Turnover.** Frequency alone will happily recommend a meme contract
   printing hundreds of $5 trades against a paper-thin book, where the
   simulator's flat slippage assumption is badly optimistic.

These disagree more than you would expect. XRP does more 24h turnover than
HYPE but roughly a twentieth of the prints — large blocks, few of them.

Usage:
    python scripts/pick_symbols.py                 # 60s sample, top 8
    python scripts/pick_symbols.py --seconds 120 --top 12
    python scripts/pick_symbols.py --lookback 3    # what a 3s window allows
    python scripts/pick_symbols.py --include-nonc  # keep stocks/commodities
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from collections import Counter

import websockets

TICKER_URL = "https://contract.mexc.com/api/v1/contract/ticker"
WS_URL = "wss://contract.mexc.com/edge"

# MEXC lists tokenised equities, metals and oil alongside crypto. They trade on
# session hours and behave nothing like a perp, so they are excluded by default.
NON_CRYPTO_HINTS = (
    "STOCK", "XAU", "XAG", "SILVER", "OIL", "SPX", "NAS", "SOXL", "KORU",
    "GOLD", "US30", "DAX", "NIKKEI",
)


def is_non_crypto(symbol: str) -> bool:
    return any(h in symbol.upper() for h in NON_CRYPTO_HINTS)


def fetch_turnover() -> dict:
    """24h turnover per contract, in quote currency."""
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


async def measure_rates(symbols, seconds: float) -> Counter:
    """Trades per second per contract, over one connection."""
    counts: Counter = Counter()
    async with websockets.connect(WS_URL, ping_interval=20) as ws:
        for sym in symbols:
            await ws.send(json.dumps({"method": "sub.deal", "param": {"symbol": sym}}))
            await asyncio.sleep(0.02)   # gentle: MEXC 403s on bursts

        async def pinger():
            while True:
                await asyncio.sleep(15)
                await ws.send(json.dumps({"method": "ping"}))

        ping_task = asyncio.create_task(pinger())
        try:
            end = asyncio.get_event_loop().time() + seconds
            while asyncio.get_event_loop().time() < end:
                remaining = end - asyncio.get_event_loop().time()
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(remaining, 0.1))
                except asyncio.TimeoutError:
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("channel") == "push.deal" and msg.get("symbol"):
                    data = msg.get("data")
                    counts[msg["symbol"]] += len(data) if isinstance(data, list) else 1
        finally:
            ping_task.cancel()
    for sym in counts:
        counts[sym] = counts[sym] / seconds
    return counts


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=60.0, help="sample length")
    p.add_argument("--top", type=int, default=8, help="how many to recommend")
    p.add_argument("--candidates", type=int, default=60,
                   help="how many top-turnover contracts to sample")
    p.add_argument("--lookback", type=float, default=1.0,
                   help="the strategy's lookback window, in seconds")
    p.add_argument("--include-nonc", action="store_true",
                   help="keep tokenised stocks, metals and oil")
    args = p.parse_args()

    # A window needs ~2 prints to measure anything at all.
    min_rate = 2.0 / args.lookback

    print(f"Fetching 24h turnover…", file=sys.stderr)
    turnover = fetch_turnover()
    if not turnover:
        print("no ticker data (is MEXC reachable from here?)", file=sys.stderr)
        return 1

    ranked = sorted(turnover.items(), key=lambda kv: kv[1], reverse=True)
    if not args.include_nonc:
        ranked = [(s, v) for s, v in ranked if not is_non_crypto(s)]
    candidates = [s for s, _ in ranked[: args.candidates]]

    print(f"Sampling {len(candidates)} contracts for {args.seconds:.0f}s…",
          file=sys.stderr)
    rates = await measure_rates(candidates, args.seconds)

    rows = []
    for sym in candidates:
        rate = rates.get(sym, 0.0)
        rows.append((sym, rate, turnover.get(sym, 0.0)))
    rows.sort(key=lambda r: r[2], reverse=True)

    print(f"\n{'symbol':<18}{'trades/s':>10}{'24h turnover':>18}   verdict")
    print("-" * 62)
    usable = []
    for sym, rate, vol in rows[:30]:
        if rate >= min_rate:
            verdict = "OK"
            usable.append((sym, rate, vol))
        elif rate >= min_rate / 2:
            verdict = "marginal"
        else:
            verdict = "cannot fire"
        print(f"{sym:<18}{rate:>10.2f}{vol:>18,.0f}   {verdict}")

    print(f"\nNeeds >= {min_rate:.1f} trades/s for a {args.lookback:g}s lookback.")
    if not usable:
        print("\nNothing clears the bar. Lengthen --lookback so sparser coins "
              "can fill a window.")
        return 1

    picks = [s for s, _, _ in usable[: args.top]]
    print(f"\nRecommended watchlist ({len(picks)}), ranked by turnover:\n")
    print("  config.yaml:")
    print("  symbols:")
    for s in picks:
        print(f"    - {s}")
    print("\n  dashboard (web/js/config.js):")
    print("  symbols: " + json.dumps([s.replace("_USDT", "") for s in picks]))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
