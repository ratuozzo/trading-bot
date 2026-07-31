"""HTTP + WebSocket API, and the dashboard served from the same origin.

Serving the UI from the backend avoids CORS entirely and, more importantly,
avoids the mixed-content problem: a page loaded over HTTPS cannot open a
plain ws:// or http:// connection to your server, so a GitHub-Pages-hosted
dashboard cannot talk to a bare-IP backend at all. One origin sidesteps both.

Endpoints
    GET  /api/state    current snapshot
    GET  /api/stream   WebSocket, pushes a snapshot ~2x/second
    POST /api/start    begin trading
    POST /api/stop     stop trading
    POST /api/reset    wipe the session back to starting cash
    GET  /api/config   effective strategy/risk/fee settings
    POST /api/config   update strategy/risk settings (restarts if running)
    GET  /healthz      liveness, no auth

Auth: every /api route except /healthz requires the token when one is set,
via `Authorization: Bearer <token>` or `?token=<token>` (the latter because
browser WebSocket clients cannot set headers). The server refuses to bind to
a non-loopback interface without a token — an open endpoint that can start
and stop trading is not something to leave lying around.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from typing import Any, Dict

from aiohttp import WSMsgType, web

from ..config import Config
from .service import TradingService

log = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "web")


def _authorised(request: web.Request) -> bool:
    token = request.app["cfg"].server.token
    if not token:
        return True  # loopback-only mode; see create_app()
    supplied = ""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        supplied = header[7:]
    if not supplied:
        supplied = request.query.get("token", "")
    # Constant-time compare so the token can't be recovered by timing.
    return hmac.compare_digest(supplied, token)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path.startswith("/api/") and not _authorised(request):
        return web.json_response({"error": "unauthorised"}, status=401)
    return await handler(request)


async def handle_state(request: web.Request) -> web.Response:
    return web.json_response(request.app["service"].snapshot())


async def handle_start(request: web.Request) -> web.Response:
    return web.json_response(await request.app["service"].start())


async def handle_stop(request: web.Request) -> web.Response:
    return web.json_response(await request.app["service"].stop())


async def handle_reset(request: web.Request) -> web.Response:
    return web.json_response(await request.app["service"].reset())


async def handle_get_config(request: web.Request) -> web.Response:
    cfg: Config = request.app["cfg"]
    return web.json_response({
        "symbols": request.app["service"].symbols,
        "strategy": vars(cfg.strategy),
        "risk": vars(cfg.risk),
        "broker": {
            "mode": cfg.broker.mode,
            "startingCash": cfg.broker.starting_cash,
            "feeBps": cfg.broker.fee_bps,
            "slippageBps": cfg.broker.slippage_bps,
        },
        "feed": {"exchange": cfg.feed.exchange, "stream": cfg.feed.stream},
    })


# Only these may be changed at runtime. Anything touching credentials or the
# paper/live switch stays config-file-only, deliberately.
_STRATEGY_KEYS = {
    "lookback_seconds", "allow_shorts", "entry_threshold", "take_profit",
    "stop_loss", "reversal_exit", "reversal_window", "max_hold_seconds",
    "cooldown_seconds",
}
_RISK_KEYS = {
    "max_concurrent_positions", "order_size_pct", "min_notional",
    "daily_loss_limit_pct",
}


async def handle_set_config(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        return web.json_response({"error": "invalid JSON"}, status=400)

    cfg: Config = request.app["cfg"]
    service: TradingService = request.app["service"]
    changed: Dict[str, Any] = {}

    for key, value in (body.get("strategy") or {}).items():
        if key not in _STRATEGY_KEYS:
            continue
        current = getattr(cfg.strategy, key)
        try:
            cast = bool(value) if isinstance(current, bool) else type(current)(value)
        except (TypeError, ValueError):
            return web.json_response(
                {"error": f"bad value for strategy.{key}"}, status=400)
        setattr(cfg.strategy, key, cast)
        changed[f"strategy.{key}"] = cast

    for key, value in (body.get("risk") or {}).items():
        if key not in _RISK_KEYS:
            continue
        current = getattr(cfg.risk, key)
        try:
            cast = type(current)(value)
        except (TypeError, ValueError):
            return web.json_response({"error": f"bad value for risk.{key}"}, status=400)
        setattr(cfg.risk, key, cast)
        changed[f"risk.{key}"] = cast

    symbols = body.get("symbols")
    if isinstance(symbols, list) and symbols:
        service.symbols = [str(s).upper() for s in symbols][:20]
        changed["symbols"] = service.symbols

    # New parameters only reach the strategies through a rebuild. Preserve
    # the session either way — changing a threshold must not wipe the P&L.
    if service.running:
        await service.stop()
        await service.start()
    else:
        service.rebuild(preserve=True)

    return web.json_response({"ok": True, "changed": changed})


async def handle_stream(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    service: TradingService = request.app["service"]

    async def pump():
        while not ws.closed:
            await ws.send_json(service.snapshot())
            await asyncio.sleep(0.5)

    pump_task = asyncio.create_task(pump())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
            # Clients may drive the bot over the socket too, so a phone with a
            # flaky connection isn't stuck without controls.
            if msg.type == WSMsgType.TEXT:
                try:
                    cmd = json.loads(msg.data).get("cmd")
                except (ValueError, AttributeError):
                    continue
                if cmd == "start":
                    await service.start()
                elif cmd == "stop":
                    await service.stop()
                elif cmd == "reset":
                    await service.reset()
    finally:
        pump_task.cancel()
    return ws


async def handle_health(request: web.Request) -> web.Response:
    service: TradingService = request.app["service"]
    return web.json_response({"ok": True, "running": service.running})


async def handle_index(request: web.Request) -> web.Response:
    index = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index):
        return web.Response(text="dashboard not found", status=404)
    return web.FileResponse(index)


def create_app(cfg: Config) -> web.Application:
    # An endpoint that can start and stop trading must not be reachable from
    # the network without a token. Fail loudly at boot rather than quietly
    # exposing it.
    host = cfg.server.host
    if host not in ("127.0.0.1", "localhost", "::1") and not cfg.server.token:
        raise SystemExit(
            f"Refusing to bind to {host} without an API token.\n"
            "Set TB_API_TOKEN to a long random string, or bind to 127.0.0.1 "
            "and reach it through an SSH tunnel or Cloudflare Tunnel."
        )

    app = web.Application(middlewares=[auth_middleware])
    app["cfg"] = cfg
    app["service"] = TradingService(cfg)

    app.router.add_get("/healthz", handle_health)
    app.router.add_get("/api/state", handle_state)
    app.router.add_get("/api/stream", handle_stream)
    app.router.add_post("/api/start", handle_start)
    app.router.add_post("/api/stop", handle_stop)
    app.router.add_post("/api/reset", handle_reset)
    app.router.add_get("/api/config", handle_get_config)
    app.router.add_post("/api/config", handle_set_config)

    if os.path.isdir(STATIC_DIR):
        app.router.add_get("/", handle_index)
        app.router.add_static("/", STATIC_DIR, show_index=False)

    async def _on_startup(app: web.Application) -> None:
        if cfg.server.autostart:
            await app["service"].start()

    async def _on_cleanup(app: web.Application) -> None:
        await app["service"].stop()

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app
