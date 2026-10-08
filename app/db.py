from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings

settings = get_settings()


def _prepare_url(raw: str) -> tuple[str, dict]:
    """Normalize the DB URL for the asyncpg driver and extract SSL.

    - `postgresql://` → `postgresql+asyncpg://` (so a plain Neon/Supabase URL works).
    - Strip libpq's `sslmode` (asyncpg doesn't understand it) and instead pass
      `ssl=True` via connect_args when SSL is requested. Managed hosts (Neon,
      Supabase) require SSL; local Postgres doesn't.
    """
    parts = urlsplit(raw)
    scheme = parts.scheme
    if scheme == "postgresql" or scheme == "postgres":
        scheme = "postgresql+asyncpg"

    query = dict(parse_qsl(parts.query))
    want_ssl = settings.db_ssl
    if query.pop("sslmode", None) not in (None, "disable"):
        want_ssl = True
    # Drop libpq-only params asyncpg doesn't accept as connect kwargs.
    query.pop("ssl", None)  # let connect_args drive it
    query.pop("channel_binding", None)

    url = urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    connect_args = {"ssl": True} if want_ssl else {}
    return url, connect_args


_url, _connect_args = _prepare_url(settings.database_url)

engine = create_async_engine(
    _url, pool_pre_ping=True, future=True, connect_args=_connect_args
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one AsyncSession per request."""
    async with SessionLocal() as session:
        yield session
