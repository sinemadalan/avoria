from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.router import api_router
from backend.app.core.config import get_settings
from backend.app.core.errors import register_exception_handlers
from backend.app.core.logging import configure_logging, get_logger
from backend.app.database.session import create_database_schema, dispose_database
from backend.app.infrastructure.redis import close_redis_connections
from backend.app.infrastructure.storage import get_storage_service


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    logger = get_logger(__name__)

    get_storage_service().initialize()
    await create_database_schema()
    logger.info("Avoria API started", extra={"environment": settings.environment})

    yield

    await close_redis_connections()
    await dispose_database()
    logger.info("Avoria API stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router, prefix=settings.api_prefix)
    register_exception_handlers(application)
    return application


app = create_app()

