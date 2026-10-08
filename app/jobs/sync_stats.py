"""Compute aggregate statistics from OpenAlex counts (group_by), independent of
the stored paper sample.

Run: ``python -m app.jobs.sync_stats``

Fills:
  - subfield_stats: true recent-work counts per field (7d / 30d / previous 30d),
    powering the hot-fields ranking, momentum, and the "N of total" labels.
  - topic_stats: true recent-work counts per topic, powering real "directions".

Cost: a few subfield-level group_by calls + one group_by per subfield for its
topics (~250 calls). Cheap on the OpenAlex credit budget; runs in the daily cron.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ..db import SessionLocal
from ..models import (
    Subfield,
    SubfieldStats,
    SubfieldStatsHistory,
    TopicStats,
    TopicStatsHistory,
)
from ..sources import openalex


async def main() -> None:
    today = date.today()
    d7 = today - timedelta(days=7)
    d30 = today - timedelta(days=30)
    d60 = today - timedelta(days=60)

    async with openalex.make_client() as client, SessionLocal() as session:
        # 1) Subfield-level counts (top ~200 groups) for ranking + momentum.
        s30, _ = await openalex.group_counts(
            client, group_by="primary_topic.subfield.id", from_date=d30, to_date=today
        )
        s7, _ = await openalex.group_counts(
            client, group_by="primary_topic.subfield.id", from_date=d7, to_date=today
        )
        sprev, _ = await openalex.group_counts(
            client, group_by="primary_topic.subfield.id", from_date=d60, to_date=d30
        )
        print(f"subfield group_by: 30d={len(s30)} 7d={len(s7)} prev={len(sprev)}")

        subfields = (await session.execute(select(Subfield.id))).scalars().all()

        # 2) Per-subfield directions: exact topic counts + exact subfield 30d total.
        exact30: dict[int, int] = {}
        for i, sid in enumerate(subfields, 1):
            try:
                tcounts, total = await openalex.group_counts(
                    client,
                    group_by="primary_topic.id",
                    from_date=d30,
                    to_date=today,
                    extra_filter=f"primary_topic.subfield.id:{sid}",
                )
            except Exception as exc:  # noqa: BLE001 — keep going
                print(f"[warn] directions subfield {sid}: {exc}")
                continue
            exact30[sid] = total
            if tcounts:
                rows = [{"topic_id": tid, "works_30d": c} for tid, c in tcounts.items()]
                stmt = insert(TopicStats).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[TopicStats.topic_id],
                    set_={"works_30d": stmt.excluded.works_30d, "computed_at": func.now()},
                )
                await session.execute(stmt)
                await session.commit()
            if i % 50 == 0:
                print(f"  directions: {i}/{len(subfields)} subfields")

        # 3) Upsert subfield_stats (exact 30d where we have it, else group_by top-200).
        for sid in subfields:
            values = {
                "subfield_id": sid,
                "works_7d": s7.get(sid, 0),
                "works_30d": exact30.get(sid, s30.get(sid, 0)),
                "works_prev_30d": sprev.get(sid, 0),
            }
            stmt = insert(SubfieldStats).values(**values).on_conflict_do_update(
                index_elements=[SubfieldStats.subfield_id],
                set_={
                    "works_7d": values["works_7d"],
                    "works_30d": values["works_30d"],
                    "works_prev_30d": values["works_prev_30d"],
                    "computed_at": func.now(),
                },
            )
            await session.execute(stmt)
        await session.commit()

        # 4) Append today's snapshot into the append-only history tables (Trends
        # momentum accrues forward). Idempotent: a same-day rerun changes nothing.
        sf_hist = (
            insert(SubfieldStatsHistory)
            .from_select(
                ["subfield_id", "snapshot_date", "works_7d", "works_30d", "works_prev_30d"],
                select(
                    SubfieldStats.subfield_id,
                    func.current_date().label("snapshot_date"),
                    SubfieldStats.works_7d,
                    SubfieldStats.works_30d,
                    SubfieldStats.works_prev_30d,
                ),
            )
            .on_conflict_do_nothing(index_elements=["subfield_id", "snapshot_date"])
        )
        await session.execute(sf_hist)
        tp_hist = (
            insert(TopicStatsHistory)
            .from_select(
                ["topic_id", "snapshot_date", "works_30d"],
                select(
                    TopicStats.topic_id,
                    func.current_date().label("snapshot_date"),
                    TopicStats.works_30d,
                ),
            )
            .on_conflict_do_nothing(index_elements=["topic_id", "snapshot_date"])
        )
        await session.execute(tp_hist)
        await session.commit()
        print("stats sync complete (history appended)")


if __name__ == "__main__":
    asyncio.run(main())
