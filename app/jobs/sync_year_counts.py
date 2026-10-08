"""Retroactive yearly output per subfield, from OpenAlex ``group_by=publication_year``.

Run: ``python -m app.jobs.sync_year_counts [--limit N --offset M]``

Fills ``subfield_year_counts`` — a multi-year output curve per field that powers the
Trends charts from day one (no accrual needed). One group_by call per subfield; run
monthly. ``--limit/--offset`` allow round-robin batches to spread the API budget.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ..db import SessionLocal
from ..models import Subfield, SubfieldYearCount
from ..sources import openalex

START_YEAR = 2015


async def main(limit: int | None = None, offset: int = 0) -> None:
    frm = date(START_YEAR, 1, 1)
    today = date.today()
    async with openalex.make_client() as client, SessionLocal() as session:
        subfields = (
            await session.execute(select(Subfield.id).order_by(Subfield.id))
        ).scalars().all()
        if limit is not None:
            subfields = subfields[offset : offset + limit]

        for i, sid in enumerate(subfields, 1):
            try:
                counts, _ = await openalex.group_counts(
                    client,
                    group_by="publication_year",
                    from_date=frm,
                    to_date=today,
                    extra_filter=f"primary_topic.subfield.id:{sid}",
                )
            except Exception as exc:  # noqa: BLE001 — keep going
                print(f"[warn] year_counts subfield {sid}: {exc}")
                continue
            if counts:
                rows = [
                    {"subfield_id": sid, "publication_year": y, "works_count": c}
                    for y, c in counts.items()
                ]
                stmt = insert(SubfieldYearCount).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[
                        SubfieldYearCount.subfield_id,
                        SubfieldYearCount.publication_year,
                    ],
                    set_={"works_count": stmt.excluded.works_count, "computed_at": func.now()},
                )
                await session.execute(stmt)
                await session.commit()
            if i % 50 == 0:
                print(f"  year_counts: {i}/{len(subfields)} subfields")
        print("year-counts sync complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit, offset=args.offset))
