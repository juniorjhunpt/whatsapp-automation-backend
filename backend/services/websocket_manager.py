import json
import logging
import asyncio
from typing import Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)

class WebSocketManager:
    def __init__(self):
        self._clients: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        logger.info(f"WebSocket client connected. Total: {len(self._clients)}")

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        logger.info(f"WebSocket client disconnected. Total: {len(self._clients)}")

    async def broadcast(self, event: str, data: dict) -> None:
        if not self._clients:
            return
        payload = json.dumps({"event": event, "data": data})
        dead: Set[WebSocket] = set()
        for client in list(self._clients):
            try:
                await client.send_text(payload)
            except Exception:
                dead.add(client)
        for d in dead:
            self._clients.discard(d)

ws_manager = WebSocketManager()
