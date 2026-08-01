#!/usr/bin/env python3
"""Measure whether funding-rate carry actually pays on MEXC.

The trade: long spot, short the perpetual, in equal size. Direction cancels,
so the position earns the 8-hourly funding payment and nothing else. When
funding is positive, longs pay shorts and the short perp leg collects.

Why this is worth checking before building anything: carry only beats its
costs if the *realised* funding rate is high enough for long enough. Public
guides quote a 0.01% per 8h baseline (~11%/yr), but that is a claim about
some market at some time, not about MEXC now.

Costs are two-legged and therefore roughly double a directional trade:

    entry: buy spot (5bp taker) + sell perp (8bp API taker)   = 13bp
    exit:  sell spot            + buy perp                    = 13bp
                                                        total = 26bp

So a position must be held long enough for accumulated funding to clear
26bp before it earns anything at all.

Usage:
    python scripts/funding_analysis.py BTC_USDT ETH_USDT SOL_USDT
    python scripts/funding_analysis.py BTC_USDT --pages 20
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request

HIST = "https://contract.mexc.com/api/v1/contract/funding_rate/history"
PERIODS_PER_YEAR = 3 * 365          # 8-hourly settlement


def fetch_history(symbol: str, pages: int) -> list:
    rows = []
    for page in range(1, pages + 1):
        url = f"{HIST}?symbol={symbol}&page_num={page}&page_size=100"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = json.load(r).get("data", {})
        except Exception as exc:  # noqa: BLE001
            print(f"  {symbol} page {page}: {exc}", file=sys.stderr)
            break
        chunk = data.get("resultList") or []
        if not chunk:
            break
        rows.extend(chunk)
        if page >= data.get("totalPage", 0):
            break
        time.sleep(0.2)
    rows.sort(key=lambda r: r["settleTime"])
    return rows


def analyse(symbol: str, rows: list, entry_exit_bp: float) -> None:
    if len(rows) < 30:
        print(f"{symbol}: only {len(rows)} settlements — not enough")
        return

    rates = [float(r["fundingRate"]) for r in rows]
    days = (rows[-1]["settleTime"] - rows[0]["settleTime"]) / 86400_000
    mean = statistics.mean(rates)
    med = statistics.median(rates)
    pos = sum(1 for r in rates if r > 0) / len(rates)

    # A short-perp carry earns +rate when funding is positive and pays when
    # it is negative, so the naive always-on return is just the mean.
    annual = mean * PERIODS_PER_YEAR

    # Break-even holding period: how many settlements to clear entry+exit.
    need = (entry_exit_bp / 10000.0) / mean if mean > 0 else float("inf")

    print(f"\n{symbol}  ({len(rates)} settlements, {days:.0f} days)")
    print(f"  mean funding      {mean*100:+.4f}% per 8h   "
          f"-> {annual*100:+.1f}%/yr gross")
    print(f"  median            {med*100:+.4f}% per 8h")
    print(f"  positive          {pos*100:.0f}% of settlements")
    print(f"  min / max         {min(rates)*100:+.4f}% / {max(rates)*100:+.4f}%")

    if mean > 0:
        print(f"  break-even hold   {need:.1f} settlements = {need*8/24:.1f} days "
              f"(to clear {entry_exit_bp:.0f}bp of entry+exit)")
    else:
        print("  break-even hold   never — mean funding is negative")

    # Net return if held continuously for a year, paying entry+exit once.
    net_annual = annual - entry_exit_bp / 10000.0
    print(f"  net if held 1yr   {net_annual*100:+.1f}%/yr "
          f"(one entry, one exit)")

    # Worst stretch: the deepest run of negative carry, which is what a live
    # position actually has to sit through.
    worst, cur = 0.0, 0.0
    for r in rates:
        cur = min(0.0, cur + r)
        worst = min(worst, cur)
    print(f"  worst drawdown    {worst*100:.3f}% of notional "
          f"(deepest run of negative funding)")

    # Rolling 30-day windows, so we can see whether it is ever reliable.
    win = 90       # 90 settlements = 30 days
    if len(rates) > win:
        wins = [sum(rates[i:i + win]) for i in range(len(rates) - win)]
        good = sum(1 for w in wins if w * 10000 > entry_exit_bp) / len(wins)
        print(f"  30-day windows    {good*100:.0f}% cleared the {entry_exit_bp:.0f}bp "
              f"cost;  median window {statistics.median(wins)*100:+.3f}%")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="+")
    p.add_argument("--pages", type=int, default=20, help="100 settlements each")
    p.add_argument("--spot-fee-bp", type=float, default=5.0)
    p.add_argument("--perp-fee-bp", type=float, default=8.0)
    args = p.parse_args()

    entry_exit = 2 * (args.spot_fee_bp + args.perp_fee_bp)
    print(f"Funding carry on MEXC — long spot / short perp")
    print(f"Entry+exit cost: 2 x ({args.spot_fee_bp:g}bp spot + "
          f"{args.perp_fee_bp:g}bp perp) = {entry_exit:g}bp")

    for sym in args.symbols:
        print(f"\nfetching {sym}…", file=sys.stderr)
        rows = fetch_history(sym, args.pages)
        analyse(sym, rows, entry_exit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
