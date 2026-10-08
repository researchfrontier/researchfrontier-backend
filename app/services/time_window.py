from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Work


async def reference_date(session: AsyncSession) -> date:
    """The "today" that date windows are measured back from.

    In production this is the real current date. In demo mode (default) it is the
    most recent paper in the DB, so the bundled seed always falls inside the
    "last 7/30 days" windows regardless of when you run it.
    """
    if get_settings().demo_relative_dates:
        latest = await session.scalar(select(func.max(Work.publication_date)))
        if latest is not None:
            return latest
    return date.today()
