#!/usr/bin/env python3
"""Replay captured ticks through the real engine.

This deliberately drives :class:`MultiEngine` rather than reimplementing the
strategy, so what is measured is the code that actually trades — including
its fee and slippage model — not a parallel version that might disagree.

On data: MEXC publishes no historical tick stream, and its finest kline is
one minute, which is 30-120x coarser than the windows this strategy trades.
A 1-minute candle cannot tell you whether a 2-second impulse followed
through. So the only honest input is ticks you captured live, which means
samples are short and cover whatever regime happened to be running.

Read the output accordingly: a few minutes of ticks is an existence check,
not evidence of an edge. What it *can* do reliably is show whether costs
swamp the strategy, because that shows up immediately.

Usage:
    python scripts/backtest.py ticks.json
    python scripts/backtest.py ticks.json --sweep
    python scripts/backtest.py ticks.json --fee-bps 6.4 --lookback 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from typing import Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tradingbot.brokers import PaperBroker            # noqa: E402
from tradingbot.config import Config                  # noqa: E402
from tradingbot.feeds.base import PriceFeed           # noqa: E402
from tradingbot.models import Tick                    # noqa: E402
from tradingbot.multi_engine import MultiEngine       # noqa: E402
from tradingbot.portfolio import Portfolio            # noqa: E402
from tradingbot.risk import RiskManager               # noqa: E402


class ReplayFeed(PriceFeed):
    def __init__(self, ticks: List[Tick]):
        self.ticks = ticks

    async def stream(self):
        for t in self.ticks:
            yield t


def load_ticks(path: str) -> List[Tick]:
    with open(path) as fh:
        raw = json.load(fh)
    ticks = []
    for row in raw:
        sym, ts, price = row[0], float(row[1]) / 1000.0, float(row[2])
        qty = float(row[3]) if len(row) > 3 else 0.0
        if price > 0:
            ticks.append(Tick(sym, price, ts, qty))
    ticks.sort(key=lambda t: t.timestamp)
    return ticks


def run_once(ticks: List[Tick], cfg: Config, symbols: List[str]) -> dict:
    broker = PaperBroker(
        starting_cash=cfg.broker.starting_cash,
        fee_bps=cfg.broker.fee_bps,
        slippage_bps=cfg.broker.slippage_bps,
    )
    portfolio = Portfolio(starting_cash=cfg.broker.starting_cash)
    risk = RiskManager(cfg.risk, starting_equity=cfg.broker.starting_cash)
    eng = MultiEngine(ReplayFeed(ticks), broker, portfolio, risk, cfg, symbols)
    asyncio.run(eng.run())

    # Mark any still-open position to its last seen price, so a losing trade
    # left open at the end cannot flatter the result by simply not counting.
    equity = eng.equity()
    gross_fees = sum(t.fees for t in portfolio.trades)
    wins = [t for t in portfolio.trades if t.pnl > 0]
    losses = [t for t in portfolio.trades if t.pnl <= 0]
    return {
        "trades": portfolio.num_trades,
        "win_rate": portfolio.win_rate,
        "realized": portfolio.realized_pnl,
        "equity": equity,
        "return_pct": (equity - cfg.broker.starting_cash) / cfg.broker.starting_cash,
        "fees": gross_fees,
        "avg_win": sum(t.pnl for t in wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(t.pnl for t in losses) / len(losses) if losses else 0.0,
        "open_at_end": eng.open_count,
        "by_symbol": portfolio.by_symbol(),
        "reasons": _reason_counts(portfolio.trades),
    }


def _reason_counts(trades) -> Dict[str, int]:
    # The engine does not persist the exit reason on the Trade, so classify
    # by outcome shape instead: which side of the target it landed on.
    out = defaultdict(int)
    for t in trades:
        out["win" if t.pnl > 0 else "loss"] += 1
    return dict(out)


def break_even(cfg: Config) -> float:
    cost = 2 * cfg.broker.fee_bps + cfg.broker.slippage_bps
    win = cfg.strategy.take_profit * 10000 - cost
    loss = cfg.strategy.stop_loss * 10000 + cost
    return loss / (win + loss) if win > 0 else float("inf")


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("ticks", help="captured tick file (JSON)")
    p.add_argument("--fee-bps", type=float)
    p.add_argument("--slippage-bps", type=float)
    p.add_argument("--lookback", type=float)
    p.add_argument("--entry", type=float, help="entry threshold, e.g. 0.0025")
    p.add_argument("--tp", type=float)
    p.add_argument("--sl", type=float)
    p.add_argument("--sweep", action="store_true",
                   help="grid over lookback and entry threshold")
    args = p.parse_args()

    ticks = load_ticks(args.ticks)
    if not ticks:
        print("no usable ticks in that file", file=sys.stderr)
        return 1
    symbols = sorted({t.symbol for t in ticks})
    span = ticks[-1].timestamp - ticks[0].timestamp

    print(f"{len(ticks):,} ticks over {span/60:.1f} min across {len(symbols)} coins")
    print(f"  {', '.join(s.replace('_USDT','') for s in symbols)}\n")

    cfg_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")

    def make_cfg(**over) -> Config:
        # Load the SAME config the bot runs, so the backtest cannot quietly
        # measure a different strategy than the one deployed.
        c = Config.load(cfg_path) if os.path.exists(cfg_path) else Config()
        if args.fee_bps is not None:
            c.broker.fee_bps = args.fee_bps
        if args.slippage_bps is not None:
            c.broker.slippage_bps = args.slippage_bps
        for k, v in over.items():
            setattr(c.strategy, k, v)
        if args.lookback is not None and "lookback_seconds" not in over:
            c.strategy.lookback_seconds = args.lookback
        if args.entry is not None and "entry_threshold" not in over:
            c.strategy.entry_threshold = args.entry
        if args.tp is not None:
            c.strategy.take_profit = args.tp
        if args.sl is not None:
            c.strategy.stop_loss = args.sl
        return c

    if not args.sweep:
        cfg = make_cfg()
        r = run_once(ticks, cfg, symbols)
        cost = 2 * cfg.broker.fee_bps + cfg.broker.slippage_bps
        print(f"lookback {cfg.strategy.lookback_seconds:g}s  "
              f"entry ±{cfg.strategy.entry_threshold*100:.2f}%  "
              f"TP {cfg.strategy.take_profit*100:.2f}%  "
              f"SL {cfg.strategy.stop_loss*100:.2f}%  "
              f"cost {cost:.0f}bp\n")
        print(f"  trades      {r['trades']}")
        if r["trades"]:
            print(f"  win rate    {r['win_rate']*100:.0f}%  "
                  f"(needs {break_even(cfg)*100:.0f}% to break even)")
            print(f"  avg win     {r['avg_win']:+.2f}")
            print(f"  avg loss    {r['avg_loss']:+.2f}")
            print(f"  fees paid   {r['fees']:.2f}")
        print(f"  realized    {r['realized']:+.2f}")
        print(f"  equity      {r['equity']:,.2f}  ({r['return_pct']*100:+.3f}%)")
        if r["open_at_end"]:
            print(f"  ({r['open_at_end']} position(s) still open, marked to last price)")
        if r["by_symbol"]:
            print("\n  per coin:")
            for b in r["by_symbol"]:
                print(f"    {b['symbol']:<12} {b['trades']:>3} trades  "
                      f"{b['wins']:>3} wins  {b['pnl']:+8.2f}")
        return 0

    print(f"{'lookback':>9}{'entry':>8}{'trades':>8}{'win%':>7}"
          f"{'need%':>7}{'realized':>11}{'return':>10}")
    print("-" * 60)
    best = None
    for look in (0.5, 1.0, 2.0, 5.0, 10.0, 30.0):
        for entry in (0.0010, 0.0015, 0.0025, 0.0040, 0.0060):
            cfg = make_cfg(lookback_seconds=look, entry_threshold=entry)
            r = run_once(ticks, cfg, symbols)
            if r["trades"] == 0:
                continue
            need = break_even(cfg)
            print(f"{look:>8}s{entry*100:>7.2f}%{r['trades']:>8}"
                  f"{r['win_rate']*100:>7.0f}{need*100:>7.0f}"
                  f"{r['realized']:>11.2f}{r['return_pct']*100:>9.3f}%")
            if best is None or r["realized"] > best[0]:
                best = (r["realized"], look, entry, r)
    if best:
        pnl, look, entry, r = best
        print(f"\nbest: lookback {look:g}s entry {entry*100:.2f}% -> "
              f"{pnl:+.2f} on {r['trades']} trades")
        if pnl <= 0:
            print("...which is still a loss. No setting in the grid made money "
                  "on this sample.")
    else:
        print("\nNo configuration produced a single trade on this sample.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
