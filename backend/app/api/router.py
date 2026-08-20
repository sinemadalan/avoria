from fastapi import APIRouter

from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.jobs import router as jobs_router
from backend.app.api.routes.media import router as media_router
from backend.app.realtime.routes import router as websocket_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(media_router)
api_router.include_router(jobs_router)
api_router.include_router(websocket_router)
