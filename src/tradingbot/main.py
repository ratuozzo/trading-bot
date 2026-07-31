"""Entry point: `python -m tradingbot.main`.

Loads config, builds the feed / strategy / broker / risk / portfolio, and
runs the engine until you press Ctrl-C, then prints a final summary.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .brokers import build_broker
from .config import Config
from .engine import Engine
from .feeds import build_feed
from .portfolio import Portfolio
from .risk import RiskManager
from .strategy import MomentumScalper


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Real-time crypto momentum scalping bot.")
    p.add_argument("--config", default="config.yaml", help="path to config file")
    p.add_argument("--symbol", help="override symbol, e.g. btcusdt, ethusdt")
    p.add_argument(
        "--exchange", choices=["binance", "bybit", "okx"], help="which venue to stream"
    )
    p.add_argument(
        "--stream",
        choices=["trade", "aggTrade", "bookTicker"],
        help="feed stream type",
    )
    p.add_argument(
        "--market", choices=["futures", "spot"], help="perps (faster) or spot"
    )
    p.add_argument("--mode", choices=["paper", "live"], help="broker mode")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p.parse_args(argv)


def build_config(args: argparse.Namespace) -> Config:
    cfg = Config.load(args.config)
    if args.symbol:
        cfg.feed.symbol = args.symbol
    if args.exchange:
        cfg.feed.exchange = args.exchange
    if args.stream:
        cfg.feed.stream = args.stream
    if args.market:
        cfg.feed.market = args.market
    if args.mode:
        cfg.broker.mode = args.mode
    return cfg


async def run(cfg: Config) -> None:
    broker = build_broker(cfg.broker)
    feed = build_feed(cfg.feed)
    strategy = MomentumScalper(cfg.strategy)
    portfolio = Portfolio(starting_cash=cfg.broker.starting_cash)
    risk = RiskManager(cfg.risk, starting_equity=cfg.broker.starting_cash)
    engine = Engine(feed, strategy, broker, risk, portfolio)

    logging.info(
        "Mode=%s  %s %s %s/%s  starting_cash=%.2f",
        cfg.broker.mode, cfg.feed.exchange, cfg.feed.symbol,
        cfg.feed.market, cfg.feed.stream, cfg.broker.starting_cash,
    )
    if cfg.broker.mode == "paper":
        logging.info("PAPER TRADING — no real orders, no real money.")

    try:
        await engine.run()
    finally:
        logging.info("Final results: %s", portfolio.summary())


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = build_config(args)
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        logging.info("Stopped by user.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
