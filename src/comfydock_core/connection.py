# Add to imports
from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict

from .logging import get_logger

logger = get_logger(__name__)


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[id(websocket)] = websocket

    def disconnect(self, websocket: WebSocket):
        self.active_connections.pop(id(websocket), None)

    async def broadcast(self, message: dict):
        for connection in self.active_connections.values():
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error("Error broadcasting message: %s", e)
