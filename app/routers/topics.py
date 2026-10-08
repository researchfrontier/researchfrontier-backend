from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Topic, TopicStats
from ..schemas import TopicPapers
from ..services.papers import live_papers
from ..services.serialize import get_breadcrumb

router = APIRouter(prefix="/api/topics", tags=["topics"])


@router.get("/{topic_id}/papers", response_model=TopicPapers)
async def topic_papers(
    topic_id: int,
    window: int = Query(30, ge=1, le=365),
    status: str | None = Query(None, description="peer_reviewed|preprint|...|all"),
    venue: str | None = Query(None, description="journal|conference|book series — primary venue type"),
    search: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> TopicPapers:
    """Recent papers for a single topic (the directions drill-down), fetched live
    from OpenAlex and filtered by status / venue type / search."""
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="topic not found")

    papers, has_more = await live_papers(
        topic_id=topic_id, window=window, status=status, venue=venue, search=search, limit=limit
    )
    bc = await get_breadcrumb(session, topic.subfield_id)
    stats = await session.get(TopicStats, topic_id)
    return TopicPapers(
        topic_id=topic_id,
        topic_name=topic.display_name,
        subfield=bc,
        window_days=window,
        total_available=int(stats.works_30d) if stats else 0,
        has_more=has_more,
        papers=papers,
    )
