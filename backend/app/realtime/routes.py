from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.app.realtime.manager import websocket_manager

router = APIRouter(tags=["realtime"])


@router.websocket("/ws/jobs/{job_id}")
async def job_events(websocket: WebSocket, job_id: str) -> None:
    await websocket_manager.connect(job_id, websocket)
    await websocket.send_json({"type": "connected", "job_id": job_id})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_manager.disconnect(job_id, websocket)

