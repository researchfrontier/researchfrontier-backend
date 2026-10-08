from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Domain, Field, Subfield, SubfieldStats, Topic, TopicStats, Work
from ..schemas import (
    DigestOut,
    DirectionOut,
    DirectionsOut,
    HotField,
    PaperList,
    PaperOut,
)
from ..services.papers import live_papers, live_reviews
from ..services.serialize import get_breadcrumb, work_to_paper
from ..services.time_window import reference_date

router = APIRouter(prefix="/api/fields", tags=["fields"])

# A "field" in the product == an OpenAlex subfield (a specific research area).


async def _ranked_fields(
    session: AsyncSession, *, limit: int, ascending: bool, require_positive: bool
) -> list[HotField]:
    """Rank fields by TRUE recent output (last 30 days, from subfield_stats — not the
    stored paper sample). ``ascending`` flips hottest <-> coldest; ties break by name
    so the list is stable. ``require_positive`` keeps un-measured/silent fields out of
    the hot list while letting the cold list surface the genuinely quiet ones."""
    order = SubfieldStats.works_30d.asc() if ascending else SubfieldStats.works_30d.desc()
    query = (
        select(
            SubfieldStats.subfield_id,
            SubfieldStats.works_30d,
            SubfieldStats.works_prev_30d,
            Subfield.display_name,
            Field.id,
            Field.display_name,
            Domain.display_name,
        )
        .join(Subfield, Subfield.id == SubfieldStats.subfield_id)
        .join(Field, Subfield.field_id == Field.id)
        .join(Domain, Field.domain_id == Domain.id)
    )
    if require_positive:
        query = query.where(SubfieldStats.works_30d > 0)
    query = query.order_by(order, Subfield.display_name.asc()).limit(limit)

    rows = (await session.execute(query)).all()

    out: list[HotField] = []
    for sid, w30, wprev, sname, fid, fname, dname in rows:
        w30, wprev = int(w30), int(wprev)
        delta = w30 - wprev
        out.append(
            HotField(
                subfield_id=sid,
                subfield_name=sname,
                field_id=fid,
                field_name=fname,
                domain_name=dname,
                count=w30,
                prev_count=wprev,
                delta=delta,
                momentum=round(delta / wprev, 3) if wprev else float(w30),
            )
        )
    return out


@router.get("/hot", response_model=list[HotField])
async def hot_fields(
    window: int = Query(30, ge=1, le=365, description="kept for compatibility; hot is 30-day"),
    limit: int = Query(12, ge=1, le=300),
    session: AsyncSession = Depends(get_session),
) -> list[HotField]:
    """The hottest research fields now: ranked by TRUE recent output (last 30 days,
    from OpenAlex counts), with momentum vs the previous 30 days. Reads
    subfield_stats, not the stored paper sample."""
    return await _ranked_fields(session, limit=limit, ascending=False, require_positive=True)


@router.get("/cold", response_model=list[HotField])
async def cold_fields(
    limit: int = Query(12, ge=1, le=300),
    session: AsyncSession = Depends(get_session),
) -> list[HotField]:
    """The coldest research fields now: the quietest areas by TRUE recent output (last
    30 days). Same shape as /hot but ranked ascending, and it keeps fields with zero
    recent works — those are precisely the cold ones."""
    return await _ranked_fields(session, limit=limit, ascending=True, require_positive=False)


@router.get("/{subfield_id}/papers", response_model=PaperList)
async def field_papers(
    subfield_id: int,
    window: int = Query(30, ge=1, le=365),
    status: str | None = Query(None, description="peer_reviewed|preprint|preprint_published|retracted|all"),
    venue: str | None = Query(None, description="journal|conference|book series — primary venue type"),
    search: str | None = Query(None, description="full-text query"),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> PaperList:
    """Recent papers in a field — fetched live from OpenAlex (filtered by status,
    optional venue type, and optional full-text search), so the list is complete and
    searchable rather than a small stored sample. `total_available` is the field's true
    count for the window."""
    papers, has_more = await live_papers(
        subfield_id=subfield_id, window=window, status=status, venue=venue, search=search, limit=limit
    )
    bc = await get_breadcrumb(session, subfield_id)
    stats = await session.get(SubfieldStats, subfield_id)
    total_available = 0 if stats is None else int(stats.works_7d if window <= 7 else stats.works_30d)
    return PaperList(
        subfield=bc,
        window_days=window,
        reference_date=date.today(),
        total=len(papers),
        total_available=total_available,
        has_more=has_more,
        papers=papers,
    )


@router.get("/{subfield_id}/reviews", response_model=list[PaperOut])
async def field_reviews(
    subfield_id: int,
    limit: int = Query(3, ge=1, le=10),
    session: AsyncSession = Depends(get_session),
) -> list[PaperOut]:
    """A few authoritative recent review articles for a field — entry points a
    non-expert can start from, fetched live from OpenAlex (journal-venue reviews,
    ranked by citations). May return fewer than `limit` where reviews are scarce."""
    return await live_reviews(subfield_id=subfield_id, limit=limit)


@router.get("/{subfield_id}/directions", response_model=DirectionsOut)
async def field_directions(
    subfield_id: int,
    window: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
) -> DirectionsOut:
    """Where the field is moving: its topics ranked by TRUE recent output (last 30
    days, from OpenAlex counts in topic_stats), not by the stored paper sample."""
    ref = await reference_date(session)

    rows = (
        await session.execute(
            select(Topic.id, Topic.display_name, Topic.keywords, TopicStats.works_30d)
            .join(TopicStats, TopicStats.topic_id == Topic.id)
            .where(Topic.subfield_id == subfield_id, TopicStats.works_30d > 0)
            .order_by(TopicStats.works_30d.desc())
        )
    ).all()

    total = sum(int(r[3]) for r in rows)
    directions = [
        DirectionOut(
            topic_id=tid,
            topic_name=name,
            count=int(c),
            share=round(c / total, 3) if total else 0.0,
            delta=0,  # per-topic momentum not tracked yet
            keywords=(kw or []),
        )
        for tid, name, kw, c in rows
    ]

    bc = await get_breadcrumb(session, subfield_id)
    return DirectionsOut(
        subfield=bc,
        window_days=30,
        reference_date=ref,
        total=total,
        directions=directions,
    )


@router.get("/{subfield_id}/digest", response_model=DigestOut)
async def field_digest(
    subfield_id: int,
    session: AsyncSession = Depends(get_session),
) -> DigestOut:
    """The in-app Mon/Wed/Fri brief for a field. Returns the latest stored edition
    if a scheduled job built one; otherwise computes a transient brief so the UI
    always has something to show."""
    from ..models import Digest  # local import to avoid a cycle at module load

    bc = await get_breadcrumb(session, subfield_id)

    stored = await session.scalar(
        select(Digest)
        .where(Digest.subfield_id == subfield_id)
        .order_by(Digest.edition_date.desc())
        .limit(1)
    )

    if stored and stored.work_ids:
        rows = (
            await session.execute(
                select(Work, Topic.display_name)
                .join(Topic, Work.primary_topic_id == Topic.id, isouter=True)
                .where(Work.id.in_(stored.work_ids))
            )
        ).all()
        order = {wid: i for i, wid in enumerate(stored.work_ids)}
        rows.sort(key=lambda r: order.get(r[0].id, 1_000_000))
        return DigestOut(
            subfield=bc,
            edition_date=stored.edition_date,
            window_days=stored.window_days,
            headline=stored.headline,
            summary=stored.summary,
            stats=stored.stats or {},
            papers=[work_to_paper(w, tname) for w, tname in rows],
            generated=False,
        )

    # --- transient brief ---
    ref = await reference_date(session)
    window_days = 7
    start = ref - timedelta(days=window_days)
    rows = (
        await session.execute(
            select(Work, Topic.display_name)
            .join(Topic, Work.primary_topic_id == Topic.id, isouter=True)
            .where(
                Work.primary_subfield_id == subfield_id,
                Work.publication_date > start,
                Work.publication_date <= ref,
            )
            .order_by(Work.cited_by_count.desc(), Work.publication_date.desc())
            .limit(6)
        )
    ).all()
    papers = [work_to_paper(w, tname) for w, tname in rows]

    by_status: dict[str, int] = {}
    for w, _ in rows:
        by_status[w.review_status] = by_status.get(w.review_status, 0) + 1
    top_topics = [tname for _, tname in rows if tname][:3]

    name = bc.subfield_name or "this field"
    headline = f"{len(papers)} notable work{'s' if len(papers) != 1 else ''} in {name}"
    if top_topics:
        summary = "Recent momentum around " + ", ".join(dict.fromkeys(top_topics)) + "."
    else:
        summary = f"A quiet window in {name}."

    return DigestOut(
        subfield=bc,
        edition_date=ref,
        window_days=window_days,
        headline=headline,
        summary=summary,
        stats={"by_status": by_status, "count": len(papers)},
        papers=papers,
        generated=True,
    )
