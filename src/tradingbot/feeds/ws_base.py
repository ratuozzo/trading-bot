"""Shared WebSocket plumbing for subscribe-style exchange feeds.

Binance encodes the subscription in the URL; Bybit, OKX and others connect to
a generic endpoint and then send a subscribe frame. This base class handles
the connect / subscribe / reconnect loop so each venue only implements
``_subscribe_payload`` and ``_parse``.
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
from typing import Any, AsyncIterator, List, Optional

import websockets

from ..models import Tick
from .base import PriceFeed

log = logging.getLogger(__name__)


class SubscribeWebSocketFeed(PriceFeed):
    url: str = ""
    name: str = "feed"

    #: seconds between application-level pings (None = rely on protocol pings)
    keepalive_interval: Optional[float] = None
    keepalive_payload: Any = None

    @abc.abstractmethod
    def _subscribe_payload(self) -> List[Any]:
        """Frames to send immediately after connecting."""

    @abc.abstractmethod
    def _parse(self, msg: dict) -> Optional[Tick]:
        """Turn a decoded message into a Tick, or None to ignore it."""

    async def stream(self) -> AsyncIterator[Tick]:
        backoff = 1.0
        while True:
            keepalive_task = None
            try:
                async with websockets.connect(
                    self.url, ping_interval=20, ping_timeout=20
                ) as ws:
                    for frame in self._subscribe_payload():
                        await ws.send(json.dumps(frame))
                    log.info("[%s] connected to %s", self.name, self.url)
                    backoff = 1.0

                    if self.keepalive_interval:
                        keepalive_task = asyncio.create_task(self._keepalive(ws))

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except (ValueError, TypeError):
                            continue
                        tick = self._parse(msg)
                        if tick is not None:
                            yield tick
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - always reconnect
                log.warning(
                    "[%s] feed error (%s); reconnecting in %.0fs", self.name, exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                if keepalive_task is not None:
                    keepalive_task.cancel()

    async def _keepalive(self, ws) -> None:
        while True:
            await asyncio.sleep(self.keepalive_interval)
            try:
                payload = self.keepalive_payload
                await ws.send(payload if isinstance(payload, str) else json.dumps(payload))
            except Exception:  # noqa: BLE001 - the read loop handles reconnects
                return
