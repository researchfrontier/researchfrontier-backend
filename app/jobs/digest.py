"""Build the per-field Mon/Wed/Fri in-app digest editions.

Run: ``python -m app.jobs.digest --window 3``

For every field with recent activity, assembles one edition for today: the top
works in the window, counts by badge, and the leading directions. Upserts on
(subfield_id, edition_date) so re-runs are idempotent.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ..db import SessionLocal
from ..models import Digest, Subfield, Topic, Work


async def _build_for_subfield(session, sid: int, sname: str, ref: date, window: int) -> bool:
    start = ref - timedelta(days=window)
    works = (
        await session.execute(
            select(Work.id, Work.review_status, Work.primary_topic_id, Work.cited_by_count)
            .where(
                Work.primary_subfield_id == sid,
                Work.publication_date > start,
                Work.publication_date <= ref,
            )
            .order_by(Work.cited_by_count.desc(), Work.publication_date.desc())
        )
    ).all()
    if not works:
        return False

    by_status: dict[str, int] = {}
    by_topic: dict[int, int] = {}
    for _wid, status, topic_id, _cites in works:
        by_status[status] = by_status.get(status, 0) + 1
        if topic_id:
            by_topic[topic_id] = by_topic.get(topic_id, 0) + 1

    top_topic_ids = sorted(by_topic, key=by_topic.get, reverse=True)[:3]
    topic_names: list[str] = []
    if top_topic_ids:
        rows = (
            await session.execute(
                select(Topic.id, Topic.display_name).where(Topic.id.in_(top_topic_ids))
            )
        ).all()
        name_map = {tid: n for tid, n in rows}
        topic_names = [name_map[t] for t in top_topic_ids if t in name_map]

    work_ids = [wid for wid, *_ in works][:10]
    # Headline the number of works actually featured (what the UI shows), not the whole
    # window's volume — otherwise the brief reads "50 works" while listing 10. The total
    # volume is kept in the summary and stats.
    featured = len(work_ids)
    total = len(works)
    headline = f"{featured} notable work{'s' if featured != 1 else ''} in {sname}"
    volume = f"{total} new work{'s' if total != 1 else ''} this window"
    summary = (
        f"{volume} · leading directions: " + ", ".join(topic_names) + "."
        if topic_names
        else f"{volume} in {sname}."
    )
    stats = {"by_status": by_status, "count": total, "featured": featured, "directions": topic_names}

    stmt = (
        insert(Digest)
        .values(
            subfield_id=sid,
            edition_date=ref,
            window_days=window,
            headline=headline,
            summary=summary,
            work_ids=work_ids,
            stats=stats,
        )
        .on_conflict_do_update(
            index_elements=[Digest.subfield_id, Digest.edition_date],
            set_={
                "window_days": window,
                "headline": headline,
                "summary": summary,
                "work_ids": work_ids,
                "stats": stats,
            },
        )
    )
    await session.execute(stmt)
    return True


async def main() -> None:
    parser = argparse.ArgumentParser(description="Build digest editions.")
    parser.add_argument("--window", type=int, default=3, help="trailing days per edition")
    args = parser.parse_args()
    ref = date.today()

    async with SessionLocal() as session:
        subfields = (
            await session.execute(select(Subfield.id, Subfield.display_name))
        ).all()
        built = 0
        for sid, sname in subfields:
            if await _build_for_subfield(session, sid, sname, ref, args.window):
                built += 1
        await session.commit()
        print(f"digest complete: {built} editions for {ref.isoformat()}")


if __name__ == "__main__":
    asyncio.run(main())
