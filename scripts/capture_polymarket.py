#!/usr/bin/env python3
"""Record whether Polymarket's 5-minute book already knows what we know.

The whole binary-edge case rests on one untested assumption: that at window
open the book sits near 50/51 *regardless of recent price action*, so a
forecast built from lagged returns is information the quote does not have.

If instead the market makers skew their quote using the same short-horizon
momentum our model uses, there is no edge at all — we would be paying 3.5%
to agree with the price.

Nothing here places an order. It records, per 5-minute window:

    our forecast   P(up) from the same model as scripts/binary_edge.py
    the quote      best bid/ask on UP at window open
    the outcome    did the underlying close above its open

With enough windows, three things become answerable:

    1. Does the quote move with our forecast?  (corr(forecast, mid))
       If yes, the market already has the signal and the edge is imaginary.
    2. Is our forecast right more often than the quote implies?
    3. What did the fill actually cost, at real depth?

Run it for a day before believing anything. A handful of windows is a
pipeline test, not evidence.

Usage:
    python scripts/capture_polymarket.py --minutes 60
    python scripts/capture_polymarket.py --minutes 1440 --out capture.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
MEXC = "https://contract.mexc.com/api/v1/contract/kline"

SYMBOLS = {"btc": "BTC_USDT", "eth": "ETH_USDT", "sol": "SOL_USDT"}


def get(url: str, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def book_for(slug: str):
    """Best bid/ask on the UP side, plus the dollar depth behind them."""
    ms = get(f"{GAMMA}/markets?slug={slug}")
    if not ms:
        return None
    m = ms[0]
    toks = m.get("clobTokenIds")
    if isinstance(toks, str):
        toks = json.loads(toks)
    if not toks:
        return None
    t0 = time.perf_counter()
    bk = get(f"{CLOB}/book?token_id={toks[0]}")
    rtt = (time.perf_counter() - t0) * 1000
    bids = sorted(((float(b["price"]), float(b["size"]))
                   for b in bk.get("bids") or []), reverse=True)
    asks = sorted((float(a["price"]), float(a["size"]))
                  for a in bk.get("asks") or [])
    if not bids or not asks:
        return None
    return {
        "bid": bids[0][0], "ask": asks[0][0],
        "bid_usd": bids[0][0] * bids[0][1],
        "ask_usd": asks[0][0] * asks[0][1],
        "depth_2c_usd": sum(p * s for p, s in asks if p <= asks[0][0] + 0.02),
        "liquidity": m.get("liquidity"),
        "rtt_ms": round(rtt, 1),
    }


def recent_candles(symbol: str, n: int = 40):
    end = int(time.time())
    start = end - (n + 5) * 300
    d = get(f"{MEXC}/{symbol}?interval=Min5&start={start}&end={end}").get("data") or {}
    times = d.get("time") or []
    return [{"t": times[i], "o": float(d["open"][i]), "h": float(d["high"][i]),
             "l": float(d["low"][i]), "c": float(d["close"][i]),
             "v": float(d["vol"][i])} for i in range(len(times))]


def forecast(model, candles) -> float:
    """P(up) for the window that is opening right now."""
    import binary_edge as be
    w, b, mu, sd = model
    cols = be.columns(candles)
    f = be.features(cols, len(candles) - 1)
    z = [(f[k] - mu[k]) / sd[k] for k in range(len(f))]
    return be.predict(w, b, [z])[0]


def train_model(path: str):
    import binary_edge as be
    with open(path) as fh:
        candles = json.load(fh)
    candles.sort(key=lambda c: c["t"])
    X, y = be.build(candles)
    Z, mu, sd = be.standardise(X)
    w, b = be.fit_logistic(Z, y)
    return (w, b, mu, sd)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--minutes", type=float, default=60.0)
    p.add_argument("--coin", default="btc", choices=sorted(SYMBOLS))
    p.add_argument("--train", default="klines/m5/BTC_USDT_Min5.json")
    p.add_argument("--out", default="polymarket_capture.jsonl")
    p.add_argument("--lead-ms", type=float, default=300.0,
                   help="poll this long after the window opens, to imitate "
                        "a realistic round trip rather than a perfect one")
    args = p.parse_args()

    print(f"training on {args.train} ...", file=sys.stderr)
    model = train_model(args.train)
    symbol = SYMBOLS[args.coin]
    deadline = time.time() + args.minutes * 60
    rows = []

    print(f"capturing {args.coin.upper()} for {args.minutes:.0f} min "
          f"-> {args.out}\n", file=sys.stderr)
    print(f"  {'window':>8}{'P(up)':>8}{'bid':>7}{'ask':>7}{'mid':>7}"
          f"{'$@ask':>8}{'rtt':>8}{'result':>9}")

    while time.time() < deadline:
        nxt = (int(time.time()) // 300 + 1) * 300
        sleep = nxt - time.time() + args.lead_ms / 1000.0
        if sleep > 0:
            time.sleep(sleep)
        ws = (int(time.time()) // 300) * 300

        try:
            candles = recent_candles(symbol)
            # The candle that just closed is the last completed one; the
            # window now opening is the next. Anything else peeks.
            closed = [c for c in candles if c["t"] + 300 <= ws]
            if len(closed) < 30:
                continue
            q = forecast(model, closed)
            bk = book_for(f"{args.coin}-updown-5m-{ws}")
            if not bk:
                continue
        except Exception as exc:  # noqa: BLE001 - keep the capture running
            print(f"  {time.strftime('%H:%M', time.gmtime(ws))}  {exc}",
                  file=sys.stderr)
            continue

        row = {"window": ws, "coin": args.coin, "forecast": round(q, 4),
               "open_price": closed[-1]["c"], **bk}
        rows.append(row)
        mid = (bk["bid"] + bk["ask"]) / 2
        print(f"  {time.strftime('%H:%M', time.gmtime(ws)):>8}{q:>8.3f}"
              f"{bk['bid']:>7.2f}{bk['ask']:>7.2f}{mid:>7.3f}"
              f"{bk['ask_usd']:>8.0f}{bk['rtt_ms']:>7.0f}ms{'':>9}")

        with open(args.out, "a") as fh:
            fh.write(json.dumps(row) + "\n")

    if len(rows) >= 3:
        summarise(rows)
    return 0


def summarise(rows) -> None:
    fs = [r["forecast"] for r in rows]
    mids = [(r["bid"] + r["ask"]) / 2 for r in rows]
    n = len(fs)
    mf, mm = sum(fs) / n, sum(mids) / n
    num = sum((fs[i] - mf) * (mids[i] - mm) for i in range(n))
    d1 = math.sqrt(sum((v - mf) ** 2 for v in fs))
    d2 = math.sqrt(sum((v - mm) ** 2 for v in mids))
    corr = num / (d1 * d2) if d1 and d2 else 0.0

    print(f"\n  windows captured   {n}")
    print(f"  mean forecast      {mf:.4f}")
    print(f"  mean quoted mid    {mm:.4f}")
    print(f"  corr(forecast,mid) {corr:+.3f}")
    print(f"  median $ at ask    {sorted(r['ask_usd'] for r in rows)[n//2]:,.0f}")
    print(f"  median rtt         {sorted(r['rtt_ms'] for r in rows)[n//2]:,.0f} ms")
    print("\n  A correlation near zero means the quote does NOT contain our")
    print("  signal, and the edge is real. Near +1 means it already does,")
    print("  and there was never an edge to collect.")


if __name__ == "__main__":
    sys.exit(main())
