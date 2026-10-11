from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import (
    Domain,
    Field,
    Subfield,
    SubfieldRanking,
    SubfieldStats,
    SubfieldStatsHistory,
    SubfieldTopCited,
    SubfieldYearCount,
    Topic,
    TopicStats,
)
from ..schemas import (
    InstitutionFieldItem,
    InstitutionFieldsOut,
    InstitutionHit,
    TrendCitedItem,
    TrendField,
    TrendFieldDetail,
    TrendHotOut,
    TrendMomentumPoint,
    TrendRankItem,
    TrendTopic,
    TrendYearPoint,
)
from ..services.serialize import get_breadcrumb
from ..services.trends import institution_fields, institution_search

router = APIRouter(prefix="/api/trends", tags=["trends"])


async def _years_for(session: AsyncSession, subfield_ids: list[int]) -> dict[int, list[TrendYearPoint]]:
    if not subfield_ids:
        return {}
    rows = (
        await session.execute(
            select(
                SubfieldYearCount.subfield_id,
                SubfieldYearCount.publication_year,
                SubfieldYearCount.works_count,
            )
            .where(SubfieldYearCount.subfield_id.in_(subfield_ids))
            .order_by(SubfieldYearCount.subfield_id, SubfieldYearCount.publication_year)
        )
    ).all()
    out: dict[int, list[TrendYearPoint]] = defaultdict(list)
    for sid, year, count in rows:
        out[sid].append(TrendYearPoint(year=int(year), count=int(count)))
    return out


@router.get("/hot", response_model=TrendHotOut)
async def trends_hot(
    limit: int = Query(12, ge=1, le=60),
    session: AsyncSession = Depends(get_session),
) -> TrendHotOut:
    """Hottest fields now (by true 30-day output) with their multi-year output curve —
    the Trends landing list. Served entirely from Postgres."""
    rows = (
        await session.execute(
            select(
                SubfieldStats.subfield_id,
                SubfieldStats.works_30d,
                SubfieldStats.works_prev_30d,
                Subfield.display_name,
                Field.display_name,
                Domain.display_name,
            )
            .join(Subfield, Subfield.id == SubfieldStats.subfield_id)
            .join(Field, Subfield.field_id == Field.id)
            .join(Domain, Field.domain_id == Domain.id)
            .where(SubfieldStats.works_30d > 0)
            .order_by(SubfieldStats.works_30d.desc(), Subfield.display_name.asc())
            .limit(limit)
        )
    ).all()
    ids = [r[0] for r in rows]
    years = await _years_for(session, ids)
    fields = []
    for sid, w30, wprev, sname, fname, dname in rows:
        w30, wprev = int(w30), int(wprev)
        delta = w30 - wprev
        fields.append(
            TrendField(
                subfield_id=sid,
                subfield_name=sname,
                field_name=fname,
                domain_name=dname,
                count_30d=w30,
                prev_30d=wprev,
                momentum=round(delta / wprev, 3) if wprev else float(w30),
                years=years.get(sid, []),
            )
        )
    return TrendHotOut(fields=fields)


@router.get("/fields/{subfield_id}", response_model=TrendFieldDetail)
async def trends_field(
    subfield_id: int,
    session: AsyncSession = Depends(get_session),
) -> TrendFieldDetail:
    """Full Trends panel for one field: multi-year output curve, accrued momentum,
    top directions, most-cited papers, and most-active institutions/countries — all
    from Postgres (no live OpenAlex calls)."""
    bc = await get_breadcrumb(session, subfield_id)

    year_rows = (
        await session.execute(
            select(SubfieldYearCount.publication_year, SubfieldYearCount.works_count)
            .where(SubfieldYearCount.subfield_id == subfield_id)
            .order_by(SubfieldYearCount.publication_year)
        )
    ).all()
    years = [TrendYearPoint(year=int(y), count=int(c)) for y, c in year_rows]

    mom_rows = (
        await session.execute(
            select(SubfieldStatsHistory.snapshot_date, SubfieldStatsHistory.works_30d)
            .where(SubfieldStatsHistory.subfield_id == subfield_id)
            .order_by(SubfieldStatsHistory.snapshot_date)
        )
    ).all()
    momentum = [TrendMomentumPoint(date=d, works_30d=int(c)) for d, c in mom_rows]

    topic_rows = (
        await session.execute(
            select(Topic.id, Topic.display_name, TopicStats.works_30d)
            .join(TopicStats, TopicStats.topic_id == Topic.id)
            .where(Topic.subfield_id == subfield_id, TopicStats.works_30d > 0)
            .order_by(TopicStats.works_30d.desc())
            .limit(10)
        )
    ).all()
    top_topics = [TrendTopic(topic_id=t, topic_name=n, count=int(c)) for t, n, c in topic_rows]

    cited_rows = (
        await session.execute(
            select(SubfieldTopCited)
            .where(SubfieldTopCited.subfield_id == subfield_id)
            .order_by(SubfieldTopCited.rank)
        )
    ).scalars().all()
    most_cited = [
        TrendCitedItem(
            rank=c.rank,
            title=c.title,
            doi=c.doi,
            url=(f"https://doi.org/{c.doi}" if c.doi else c.landing_page_url),
            cited_by_count=int(c.cited_by_count),
            year=c.publication_year,
        )
        for c in cited_rows
    ]

    rank_rows = (
        await session.execute(
            select(SubfieldRanking)
            .where(SubfieldRanking.subfield_id == subfield_id)
            .order_by(SubfieldRanking.dimension, SubfieldRanking.rank)
        )
    ).scalars().all()
    institutions = [
        TrendRankItem(rank=r.rank, name=r.entity_name, key=r.entity_key, count=int(r.works_count))
        for r in rank_rows
        if r.dimension == "institution"
    ]
    countries = [
        TrendRankItem(rank=r.rank, name=r.entity_name, key=r.entity_key, count=int(r.works_count))
        for r in rank_rows
        if r.dimension == "country"
    ]

    return TrendFieldDetail(
        subfield=bc,
        years=years,
        momentum=momentum,
        top_topics=top_topics,
        most_cited=most_cited,
        institutions=institutions,
        countries=countries,
    )


@router.get("/institutions", response_model=list[InstitutionHit])
async def trends_institution_search(
    q: str = Query(..., min_length=2, description="institution name search"),
    limit: int = Query(8, ge=1, le=25),
) -> list[InstitutionHit]:
    """Search institutions by name (live OpenAlex, cached) for the reverse view.
    Degrades to an empty list if OpenAlex is unavailable, rather than erroring."""
    try:
        hits = await institution_search(q, limit=limit)
    except Exception:  # noqa: BLE001 — transient upstream failure -> empty result
        hits = []
    return [
        InstitutionHit(
            id=h["id"],
            name=h.get("name"),
            country_code=h.get("country_code"),
            works_count=h.get("works_count", 0) or 0,
        )
        for h in hits
    ]


@router.get("/institutions/{inst_id}", response_model=InstitutionFieldsOut)
async def trends_institution_fields(
    inst_id: str,
    limit: int = Query(12, ge=1, le=26),
) -> InstitutionFieldsOut:
    """Which fields an institution is most active on (live OpenAlex, cached a day).
    Degrades to an empty list if OpenAlex is unavailable, rather than erroring."""
    try:
        rows = await institution_fields(inst_id, limit=limit)
    except Exception:  # noqa: BLE001 — transient upstream failure -> empty result
        rows = []
    return InstitutionFieldsOut(
        id=inst_id,
        fields=[
            InstitutionFieldItem(subfield_id=r.get("subfield_id"), name=r.get("name"), count=r.get("count", 0))
            for r in rows
        ],
    )
