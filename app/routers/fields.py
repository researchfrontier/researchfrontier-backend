from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Domain, Field, Subfield, Topic, Work
from ..schemas import (
    DigestOut,
    DirectionOut,
    DirectionsOut,
    HotField,
    PaperList,
)
from ..services.serialize import get_breadcrumb, work_to_paper
from ..services.time_window import reference_date

router = APIRouter(prefix="/api/fields", tags=["fields"])

# A "field" in the product == an OpenAlex subfield (a specific research area).


@router.get("/hot", response_model=list[HotField])
async def hot_fields(
    window: int = Query(30, ge=1, le=365),
    limit: int = Query(12, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> list[HotField]:
    """The hottest research fields right now: most recent output, ranked with a
    momentum signal (growth vs the previous equal-length window)."""
    ref = await reference_date(session)
    start = ref - timedelta(days=window)
    prev_start = ref - timedelta(days=2 * window)

    async def counts(lo, hi):
        rows = (
            await session.execute(
                select(Work.primary_subfield_id, func.count())
                .where(
                    Work.primary_subfield_id.is_not(None),
                    Work.publication_date > lo,
                    Work.publication_date <= hi,
                )
                .group_by(Work.primary_subfield_id)
            )
        ).all()
        return {sid: c for sid, c in rows}

    cur = await counts(start, ref)
    prev = await counts(prev_start, start)
    if not cur:
        return []

    meta_rows = (
        await session.execute(
            select(
                Subfield.id,
                Subfield.display_name,
                Field.id,
                Field.display_name,
                Domain.display_name,
            )
            .join(Field, Subfield.field_id == Field.id)
            .join(Domain, Field.domain_id == Domain.id)
            .where(Subfield.id.in_(cur.keys()))
        )
    ).all()

    out: list[HotField] = []
    for sid, sname, fid, fname, dname in meta_rows:
        c = cur.get(sid, 0)
        p = prev.get(sid, 0)
        out.append(
            HotField(
                subfield_id=sid,
                subfield_name=sname,
                field_id=fid,
                field_name=fname,
                domain_name=dname,
                count=c,
                prev_count=p,
                delta=c - p,
                momentum=round((c - p) / p, 3) if p else float(c),
            )
        )
    out.sort(key=lambda h: (h.count, h.momentum), reverse=True)
    return out[:limit]


@router.get("/{subfield_id}/papers", response_model=PaperList)
async def field_papers(
    subfield_id: int,
    window: int = Query(30, ge=1, le=365),
    status: str | None = Query(None, description="filter by review_status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> PaperList:
    ref = await reference_date(session)
    start = ref - timedelta(days=window)

    conds = [
        Work.primary_subfield_id == subfield_id,
        Work.publication_date > start,
        Work.publication_date <= ref,
    ]
    if status:
        conds.append(Work.review_status == status)

    total = await session.scalar(select(func.count()).select_from(Work).where(*conds)) or 0

    rows = (
        await session.execute(
            select(Work, Topic.display_name)
            .join(Topic, Work.primary_topic_id == Topic.id, isouter=True)
            .where(*conds)
            .order_by(Work.publication_date.desc(), Work.cited_by_count.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    papers = [work_to_paper(w, tname) for w, tname in rows]
    bc = await get_breadcrumb(session, subfield_id)
    return PaperList(
        subfield=bc,
        window_days=window,
        reference_date=ref,
        total=total,
        papers=papers,
    )


@router.get("/{subfield_id}/directions", response_model=DirectionsOut)
async def field_directions(
    subfield_id: int,
    window: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
) -> DirectionsOut:
    """Where the field is moving: its topics ranked by recent output, with the
    change vs the previous window (the "emerging directions" signal)."""
    ref = await reference_date(session)
    start = ref - timedelta(days=window)
    prev_start = ref - timedelta(days=2 * window)

    async def counts(lo, hi):
        rows = (
            await session.execute(
                select(Work.primary_topic_id, func.count())
                .where(
                    Work.primary_subfield_id == subfield_id,
                    Work.primary_topic_id.is_not(None),
                    Work.publication_date > lo,
                    Work.publication_date <= hi,
                )
                .group_by(Work.primary_topic_id)
            )
        ).all()
        return {tid: c for tid, c in rows}

    cur = await counts(start, ref)
    prev = await counts(prev_start, start)
    total = sum(cur.values())

    topics = (
        await session.scalars(select(Topic).where(Topic.subfield_id == subfield_id))
    ).all()
    tmap = {t.id: t for t in topics}

    directions: list[DirectionOut] = []
    for tid, c in cur.items():
        t = tmap.get(tid)
        directions.append(
            DirectionOut(
                topic_id=tid,
                topic_name=t.display_name if t else f"Topic {tid}",
                count=c,
                share=round(c / total, 3) if total else 0.0,
                delta=c - prev.get(tid, 0),
                keywords=(t.keywords if t else []) or [],
            )
        )
    directions.sort(key=lambda d: (d.count, d.delta), reverse=True)

    bc = await get_breadcrumb(session, subfield_id)
    return DirectionsOut(
        subfield=bc,
        window_days=window,
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
