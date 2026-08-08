from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class SignalBus:
    """In-memory per-user signal fan-out for dashboard SSE clients."""

    def __init__(self) -> None:
        self._channels: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)

    def subscribe(self, user_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._channels[user_id].append(queue)
        return queue

    def unsubscribe(self, user_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        channels = self._channels.get(user_id)
        if not channels:
            return
        try:
            channels.remove(queue)
        except ValueError:
            return
        if not channels:
            self._channels.pop(user_id, None)

    async def publish(self, user_id: str, payload: dict[str, Any]) -> None:
        for queue in list(self._channels.get(user_id, [])):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                continue


signal_bus = SignalBus()
