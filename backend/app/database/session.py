from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.database.base import Base

settings = get_settings()
engine = create_async_engine(settings.database_url, echo=settings.debug)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


def _ensure_sqlite_parent() -> None:
    database_url = make_url(settings.database_url)
    if database_url.get_backend_name() != "sqlite" or not database_url.database:
        return
    database_path = Path(database_url.database)
    if not database_path.is_absolute():
        database_path = Path.cwd() / database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)


async def create_database_schema() -> None:
    _ensure_sqlite_parent()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def get_database_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


async def dispose_database() -> None:
    await engine.dispose()

