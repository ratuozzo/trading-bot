#!/usr/bin/env python3
"""Download MEXC futures klines, paginating to get months of history.

Why this exists: the tick-level work is limited to whatever you capture live,
which is minutes. At a 5-minute timeframe MEXC serves real history — 2000
candles per request, paginated — so a strategy on that timeframe can be
tested on months of data instead of a handful of trades.

Usage:
    python scripts/fetch_klines.py BTC_USDT --days 180
    python scripts/fetch_klines.py BTC_USDT ETH_USDT SOL_USDT --days 90
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

BASE = "https://contract.mexc.com/api/v1/contract/kline"
MAX_CANDLES = 2000

INTERVAL_SECONDS = {
    "Min1": 60, "Min5": 300, "Min15": 900, "Min30": 1800,
    "Min60": 3600, "Hour4": 14400, "Hour8": 28800, "Day1": 86400,
}


def fetch_window(symbol: str, interval: str, start: int, end: int) -> dict:
    url = f"{BASE}/{symbol}?interval={interval}&start={start}&end={end}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=40) as r:
                return json.load(r).get("data", {}) or {}
        except Exception as exc:  # noqa: BLE001 - transient; back off and retry
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    return {}


def fetch_all(symbol: str, interval: str, days: float) -> list:
    step = INTERVAL_SECONDS[interval]
    now = int(time.time())
    start = now - int(days * 86400)
    span = MAX_CANDLES * step

    rows, cursor, seen = [], start, set()
    while cursor < now:
        chunk_end = min(cursor + span, now)
        data = fetch_window(symbol, interval, cursor, chunk_end)
        times = data.get("time") or []
        if not times:
            # A gap with no data still has to advance, or this loops forever.
            cursor = chunk_end
            continue
        for i, t in enumerate(times):
            if t in seen:
                continue
            seen.add(t)
            rows.append({
                "t": t,
                "o": float(data["open"][i]),
                "h": float(data["high"][i]),
                "l": float(data["low"][i]),
                "c": float(data["close"][i]),
                "v": float(data["vol"][i]),
            })
        cursor = max(times) + step
        print(f"\r  {symbol}: {len(rows):,} candles…", end="", file=sys.stderr)
        time.sleep(0.25)          # be polite; MEXC rate-limits bursts
    print(file=sys.stderr)
    rows.sort(key=lambda r: r["t"])
    return rows


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="+")
    p.add_argument("--interval", default="Min5", choices=sorted(INTERVAL_SECONDS))
    p.add_argument("--days", type=float, default=180.0)
    p.add_argument("--out", default="klines")
    args = p.parse_args()

    os.makedirs(args.out, exist_ok=True)
    for sym in args.symbols:
        rows = fetch_all(sym, args.interval, args.days)
        if not rows:
            print(f"{sym}: no data", file=sys.stderr)
            continue
        path = os.path.join(args.out, f"{sym}_{args.interval}.json")
        with open(path, "w") as fh:
            json.dump(rows, fh)
        span = (rows[-1]["t"] - rows[0]["t"]) / 86400
        print(f"{sym}: {len(rows):,} candles, {span:.1f} days -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
