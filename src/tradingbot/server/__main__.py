"""Run the backend:  python -m tradingbot.server

The engine lives in this process, so trading continues whether or not any
browser is connected. Closing the dashboard does not stop the bot; only
POST /api/stop, a reset, or killing the process does.
"""

from __future__ import annotations

import argparse
import logging
import sys

from aiohttp import web

from ..config import Config
from .app import create_app


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Trading bot backend.")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--host", help="bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, help="bind port (default 8787)")
    p.add_argument("--autostart", action="store_true",
                   help="begin trading as soon as the process boots")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = Config.load(args.config)
    if args.host:
        cfg.server.host = args.host
    if args.port:
        cfg.server.port = args.port
    if args.autostart:
        cfg.server.autostart = True

    app = create_app(cfg)
    logging.info(
        "Backend on http://%s:%d  (mode=%s, token=%s)",
        cfg.server.host, cfg.server.port, cfg.broker.mode,
        "set" if cfg.server.token else "NONE — loopback only",
    )
    web.run_app(app, host=cfg.server.host, port=cfg.server.port, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
