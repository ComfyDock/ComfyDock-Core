# Add to imports
from fastapi import WebSocket
from typing import Dict

import logging

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket):
        logger.info("Connecting websocket")
        await websocket.accept()
        self.active_connections[id(websocket)] = websocket

    def disconnect(self, websocket: WebSocket):
        logger.info("Disconnecting websocket")
        self.active_connections.pop(id(websocket), None)

    async def broadcast(self, message: dict):
        logger.info("Broadcasting message: %s", message)
        for connection in self.active_connections.values():
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error("Error broadcasting message: %s", e)
