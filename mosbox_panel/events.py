"""事件总线:入库 + WebSocket 广播。"""
from __future__ import annotations

import asyncio
import contextlib
from collections import deque


class EventBus:
    def __init__(self, db):
        self.db = db
        self.loop: asyncio.AbstractEventLoop | None = None
        self._clients: set = set()          # asyncio.Queue
        self._recent: deque = deque(maxlen=200)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def emit(self, kind: str, message: str, meta: dict | None = None,
             to_db: bool = True) -> dict | None:
        """线程安全:任何线程可调用,广播会调度到事件循环。"""
        ev = None
        if to_db:
            ev = self.db.add_event(kind, message, meta)
            self._recent.append(ev)
        if self.loop and self.loop.is_running():
            payload = ev or {"id": None, "ts": 0, "kind": kind,
                             "message": message, "meta": meta or {}}
            with contextlib.suppress(RuntimeError):
                self.loop.call_soon_threadsafe(self._broadcast, payload)
        return ev

    def _broadcast(self, ev: dict) -> None:
        dead = []
        for q in list(self._clients):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._clients.discard(q)

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    def recent(self, limit: int = 100) -> list[dict]:
        return list(self._recent)[-limit:]
