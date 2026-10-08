"""Paper lists served from the ingested ``work`` table in Postgres — status/venue
filtered, searchable, paginated, newest first. Serving from the store (instead of a
live OpenAlex call per visit) makes browsing fast, reliable, reproducible, and
independent of the OpenAlex credit budget; the ingestion job keeps ``work`` fresh."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Topic, Work
from ..schemas import PaperOut
from .serialize import work_to_paper


async def stored_papers(
    session: AsyncSession,
    *,
    subfield_id: int | None = None,
    topic_id: int | None = None,
    window: int = 30,
    status: str | None = None,
    venue: str | None = None,
    search: str | None = None,
    limit: int = 50,
    ref: date | None = None,
) -> tuple[list[PaperOut], bool]:
    """Return ``(papers[:limit], has_more)`` for a field or topic, from stored works:
    filtered by the recency window, derived review status, primary venue type, and an
    optional title/abstract search, newest first (citations break ties). ``has_more``
    is true when more than ``limit`` rows match, so the UI's "Load more" can grow the
    page. The same request returns the same rows (stable, citable), unlike a live feed."""
    status = status if status and status != "all" else None
    venue = venue if venue and venue != "all" else None
    ref = ref or date.today()
    start = ref - timedelta(days=window)

    q = (
        select(Work, Topic.display_name)
        .join(Topic, Work.primary_topic_id == Topic.id, isouter=True)
        .where(Work.publication_date > start, Work.publication_date <= ref)
    )
    if subfield_id is not None:
        q = q.where(Work.primary_subfield_id == subfield_id)
    if topic_id is not None:
        q = q.where(Work.primary_topic_id == topic_id)
    if status:
        q = q.where(Work.review_status == status)
    if venue:
        q = q.where(Work.primary_source_type == venue)
    if search and search.strip():
        like = f"%{search.strip()}%"
        q = q.where(or_(Work.title.ilike(like), Work.abstract.ilike(like)))

    # Fetch one extra row to detect whether a larger page would surface more.
    q = q.order_by(Work.publication_date.desc(), Work.cited_by_count.desc()).limit(limit + 1)
    rows = (await session.execute(q)).all()
    has_more = len(rows) > limit
    papers = [work_to_paper(w, tname) for w, tname in rows[:limit]]
    return papers, has_more
