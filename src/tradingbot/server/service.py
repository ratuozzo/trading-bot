"""The trading service: owns the engine lifecycle independently of any client.

This is the piece that makes the bot survive the browser closing. The engine
runs as a background asyncio task inside this process; HTTP clients only ask
it to start or stop and read its state. Nothing about the engine depends on a
client being connected.

Session state is written to disk on every completed trade, so a process
restart resumes the same session rather than silently starting a fresh one
with a fresh (and flattering) P&L.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from ..brokers import build_broker
from ..config import Config
from ..feeds import build_feed
from ..models import Side, Trade
from ..multi_engine import MultiEngine
from ..portfolio import Portfolio
from ..risk import RiskManager

log = logging.getLogger(__name__)


class TradingService:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.symbols: List[str] = list(cfg.feed.symbols) or [cfg.feed.symbol]
        self.engine: Optional[MultiEngine] = None
        self.task: Optional[asyncio.Task] = None
        self.started_at: float = 0.0
        self.stopped_reason: str = ""
        self._listeners: List[asyncio.Queue] = []
        self._build()
        self._load_state()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        broker = build_broker(self.cfg.broker)
        portfolio = Portfolio(starting_cash=self.cfg.broker.starting_cash)
        risk = RiskManager(self.cfg.risk, starting_equity=self.cfg.broker.starting_cash)

        feed_cfg = self.cfg.feed
        # The MEXC adapter takes a list; others stay single-symbol.
        if feed_cfg.exchange.lower() in ("mexc", "mexc-futures"):
            from ..feeds.mexc import MEXCFuturesFeed

            feed = MEXCFuturesFeed(symbol=self.symbols, stream=feed_cfg.stream)
        else:
            feed = build_feed(feed_cfg)

        self.engine = MultiEngine(
            feed=feed,
            broker=broker,
            portfolio=portfolio,
            risk=risk,
            cfg=self.cfg,
            symbols=self.symbols,
        )

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def start(self) -> Dict[str, Any]:
        if self.running:
            return {"ok": True, "already": True}
        # Rebuild the feed so a restart gets a clean socket, but keep the
        # portfolio and cash so "stop, look, start again" continues the
        # session rather than quietly resetting the scoreboard.
        self.rebuild(preserve=True)

        self.started_at = time.time()
        self.stopped_reason = ""
        self.task = asyncio.create_task(self._run())
        log.info("started: %d symbols on %s", len(self.symbols), self.cfg.feed.exchange)
        return {"ok": True}

    async def _run(self) -> None:
        try:
            await self.engine.run()
        except asyncio.CancelledError:
            self.stopped_reason = "stopped by request"
            raise
        except Exception as exc:  # noqa: BLE001 - report, don't vanish
            self.stopped_reason = f"{type(exc).__name__}: {exc}"
            log.exception("engine crashed")
        finally:
            self._save_state()

    async def stop(self) -> Dict[str, Any]:
        if not self.running:
            return {"ok": True, "already": True}
        self.engine.stop()
        self.task.cancel()
        try:
            await self.task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self.task = None
        self.stopped_reason = self.stopped_reason or "stopped by request"
        self._save_state()
        log.info("stopped")
        return {"ok": True}

    async def reset(self) -> Dict[str, Any]:
        """Wipe the session: flat, starting cash, empty trade log."""
        was_running = self.running
        await self.stop()
        self._build()
        self._save_state()
        if was_running:
            await self.start()
        return {"ok": True}

    def rebuild(self, *, preserve: bool) -> None:
        """Recreate the engine so new settings take effect.

        ``preserve`` keeps cash, positions and the trade log. Rebuilding
        without it silently resets the scoreboard, which would make a config
        tweak look like a fresh, flattering session.
        """
        prev = self.engine
        self._build()
        if preserve and prev is not None:
            self.engine.broker = prev.broker
            self.engine.portfolio = prev.portfolio
            self.engine.risk = prev.risk
            # New risk limits must still apply to the preserved manager.
            self.engine.risk.cfg = self.cfg.risk

    # -- state -------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        eng = self.engine
        marks = eng.marks()
        positions = []
        for symbol in self.symbols:
            pos = eng.broker.position(symbol)
            if not pos.is_open:
                continue
            mark = marks.get(symbol, pos.avg_entry_price)
            positions.append({
                "symbol": symbol,
                "side": "SHORT" if pos.direction < 0 else "LONG",
                "quantity": pos.quantity,
                "entryPrice": pos.avg_entry_price,
                "markPrice": mark,
                "unrealized": pos.unrealized_pnl(mark),
            })

        scanner = [{
            "symbol": s,
            "price": st.last_price,
            "impulse": st.impulse,
            "ticks": st.ticks,
        } for s, st in eng.states.items()]

        equity = eng.equity()
        return {
            "running": self.running,
            "mode": self.cfg.broker.mode,
            "exchange": self.cfg.feed.exchange,
            "startedAt": self.started_at,
            "serverTime": time.time(),
            "stoppedReason": self.stopped_reason,
            "symbols": self.symbols,
            "scanner": scanner,
            "positions": positions,
            "cash": eng.broker.cash,
            "equity": equity,
            "startingCash": self.cfg.broker.starting_cash,
            "realizedPnl": eng.portfolio.realized_pnl,
            "trades": eng.portfolio.num_trades,
            "winRate": eng.portfolio.win_rate,
            "halted": eng.risk.halted,
            "bySymbol": eng.portfolio.by_symbol(),
            "recentTrades": [_trade_json(t) for t in eng.portfolio.trades[-40:]][::-1],
            "stats": asdict(eng.stats),
            "strategy": asdict(self.cfg.strategy),
            "risk": asdict(self.cfg.risk),
            "fees": {
                "feeBps": self.cfg.broker.fee_bps,
                "slippageBps": self.cfg.broker.slippage_bps,
            },
        }

    # -- persistence -------------------------------------------------------

    def _state_path(self) -> str:
        return self.cfg.server.state_file

    def _save_state(self) -> None:
        eng = self.engine
        if eng is None:
            return
        payload = {
            "version": 1,
            "savedAt": time.time(),
            "startingCash": self.cfg.broker.starting_cash,
            "cash": eng.broker.cash,
            "halted": eng.risk.halted,
            "trades": [_trade_json(t) for t in eng.portfolio.trades],
        }
        path = self._state_path()
        try:
            # Write via a temp file so a crash mid-write can't corrupt the
            # session log we are trying to protect.
            d = os.path.dirname(os.path.abspath(path)) or "."
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("could not save state: %s", exc)

    def _load_state(self) -> None:
        path = self._state_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            log.warning("could not read state file: %s", exc)
            return
        if data.get("version") != 1:
            return

        eng = self.engine
        eng.broker._cash = float(data.get("cash", eng.broker.cash))
        eng.risk._halted = bool(data.get("halted", False))
        for raw in data.get("trades", []):
            try:
                eng.portfolio.trades.append(_trade_from_json(raw))
            except (KeyError, ValueError, TypeError):
                continue
        log.info(
            "resumed session: %d trades, cash %.2f",
            len(eng.portfolio.trades), eng.broker.cash,
        )

    def note_trade_committed(self) -> None:
        self._save_state()


def _trade_json(t: Trade) -> Dict[str, Any]:
    return {
        "symbol": t.symbol,
        "side": t.side.value if hasattr(t.side, "value") else str(t.side),
        "quantity": t.quantity,
        "entryPrice": t.entry_price,
        "exitPrice": t.exit_price,
        "entryTime": t.entry_time,
        "exitTime": t.exit_time,
        "fees": t.fees,
        "pnl": t.pnl,
        "returnPct": t.return_pct,
    }


def _trade_from_json(raw: Dict[str, Any]) -> Trade:
    return Trade(
        symbol=raw["symbol"],
        quantity=float(raw["quantity"]),
        entry_price=float(raw["entryPrice"]),
        exit_price=float(raw["exitPrice"]),
        entry_time=float(raw["entryTime"]),
        exit_time=float(raw["exitTime"]),
        fees=float(raw["fees"]),
        pnl=float(raw["pnl"]),
        side=Side(raw.get("side", "BUY")),
    )
