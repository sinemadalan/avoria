from fastapi import WebSocket


class WebSocketConnectionManager:
    """Tracks API-client sockets only; worker communication will use Redis."""

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, channel: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(channel, set()).add(websocket)

    def disconnect(self, channel: str, websocket: WebSocket) -> None:
        connections = self._connections.get(channel)
        if connections is None:
            return
        connections.discard(websocket)
        if not connections:
            self._connections.pop(channel, None)


websocket_manager = WebSocketConnectionManager()

