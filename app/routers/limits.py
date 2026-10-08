"""Free-plan limits for the services this project runs on.

None of Neon / Render / OpenAlex expose an open (unauthenticated) API for your
account's plan limits, so the limits themselves are documented constants. Where a
live number is available WITHOUT exposing a key, we read it server-side and return
only the number: Neon DB size (our own `pg_database_size`) and the OpenAlex daily
credit budget (read from response headers, key stays in the backend).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..schemas import LimitItem, LimitsOut, ServiceLimit
from ..services.cache import TTLCache
from ..sources import openalex

router = APIRouter(prefix="/api", tags=["meta"])

_cache = TTLCache(ttl=600.0)


def _pretty_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit in ("B", "KB", "MB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


async def _openalex_budget() -> dict | None:
    async def factory() -> dict:
        async with openalex.make_client() as client:
            resp = await client.get("/works", params={"per-page": 1, **openalex._auth_params()})
            h = resp.headers
            return {
                "limit": h.get("x-ratelimit-limit"),
                "remaining": h.get("x-ratelimit-remaining"),
            }

    try:
        return await _cache.get_or_set("openalex-budget", factory)
    except Exception:  # noqa: BLE001
        return None


@router.get("/limits", response_model=LimitsOut)
async def limits(session: AsyncSession = Depends(get_session)) -> LimitsOut:
    # --- Neon (live DB size) ---
    try:
        size = await session.scalar(text("SELECT pg_database_size(current_database())"))
    except Exception:  # noqa: BLE001
        size = None
    neon = ServiceLimit(
        service="Neon · Postgres",
        plan="Free",
        note="Serverless; auto-suspends after 5 min idle.",
        items=[
            LimitItem(
                label="Database size",
                now=_pretty_bytes(size) if size is not None else "—",
                limit="1 GB",
                live=size is not None,
            ),
            LimitItem(label="Compute", now="—", limit="100 CU-hours / mo"),
        ],
    )

    # --- OpenAlex (live daily credit budget from headers) ---
    oa = await _openalex_budget()
    oa_items: list[LimitItem] = []
    if oa and oa.get("limit") and oa.get("remaining"):
        try:
            lim = int(oa["limit"])
            rem = int(oa["remaining"])
            oa_items.append(
                LimitItem(
                    label="API credits today",
                    now=f"{lim - rem:,} used",
                    limit=f"{lim:,} / day",
                    live=True,
                )
            )
        except (TypeError, ValueError):
            pass
    if not oa_items:
        oa_items.append(LimitItem(label="API budget", now="—", limit="free key (~$1 / day)"))
    oa_items.append(LimitItem(label="Rate cap", now="—", limit="100 req/s"))
    openalex_svc = ServiceLimit(
        service="OpenAlex · data",
        plan="Free key",
        note="Resets daily. Data is CC0.",
        items=oa_items,
    )

    # --- Render (documented) ---
    render = ServiceLimit(
        service="Render · API",
        plan="Free",
        note="First request after idle wakes it (~30–60s cold start).",
        items=[
            LimitItem(label="Instance time", now="—", limit="750 hours / mo"),
            LimitItem(label="Idle", now="—", limit="sleeps after 15 min"),
        ],
    )

    return LimitsOut(services=[neon, openalex_svc, render])
