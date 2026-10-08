"""Precompute per-subfield rankings for the Trends page: most-cited papers and the
most-active institutions / countries.

Run: ``python -m app.jobs.sync_rankings [--limit N --offset M]``

Fills ``subfield_top_cited`` and ``subfield_ranking`` (latest snapshot). Three OpenAlex
calls per subfield (most-cited sort + 2 group_by). Use ``--limit/--offset`` to run a
round-robin batch per day and stay within the credit budget.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ..db import SessionLocal
from ..models import Subfield, SubfieldRanking, SubfieldTopCited
from ..sources import openalex

TOP_CITED = 5
TOP_ENTITIES = 10
CITED_FROM = date(2010, 1, 1)          # most-cited: broad window (citations accrue)
PAUSE = 0.3                            # politeness pause between OpenAlex calls


async def main(limit: int | None = None, offset: int = 0) -> None:
    today = date.today()
    active_from = date(today.year - 3, 1, 1)   # "most active": recent 3-year window
    async with openalex.make_client() as client, SessionLocal() as session:
        subfields = (
            await session.execute(select(Subfield.id).order_by(Subfield.id))
        ).scalars().all()
        if limit is not None:
            subfields = subfields[offset : offset + limit]

        for i, sid in enumerate(subfields, 1):
            # --- most-cited papers --- (only replace stored rows on a SUCCESSFUL fetch,
            # so a transient API error never wipes good data to nothing).
            try:
                raw = await openalex.fetch_recent_works(
                    client,
                    from_date=CITED_FROM,
                    to_date=today,
                    subfield_id=sid,
                    sort="cited_by_count:desc",
                    max_results=TOP_CITED,
                )
                cited_rows = []
                for rank, r in enumerate(raw, 1):
                    rec = openalex.normalize_work(r)
                    cited_rows.append(
                        {
                            "subfield_id": sid,
                            "rank": rank,
                            "openalex_id": rec.get("openalex_id"),
                            "title": rec.get("title") or "(untitled)",
                            "doi": rec.get("doi"),
                            "landing_page_url": rec.get("landing_page_url"),
                            "cited_by_count": rec.get("cited_by_count", 0) or 0,
                            "publication_year": rec.get("publication_year"),
                        }
                    )
                await session.execute(
                    delete(SubfieldTopCited).where(SubfieldTopCited.subfield_id == sid)
                )
                if cited_rows:
                    await session.execute(insert(SubfieldTopCited).values(cited_rows))
            except Exception as exc:  # noqa: BLE001 — keep prior rows on failure
                print(f"[warn] top-cited subfield {sid}: {exc}")
            await asyncio.sleep(PAUSE)

            # --- most-active institutions / countries ---
            for dim, group_by in (
                ("institution", "authorships.institutions.id"),
                ("country", "authorships.countries"),
            ):
                try:
                    buckets = await openalex.group_raw(
                        client,
                        group_by=group_by,
                        from_date=active_from,
                        to_date=today,
                        extra_filter=f"primary_topic.subfield.id:{sid}",
                        limit=TOP_ENTITIES,
                    )
                    rows = []
                    for rank, b in enumerate(buckets, 1):
                        short = str(b.get("key") or "").rsplit("/", 1)[-1]
                        rows.append(
                            {
                                "subfield_id": sid,
                                "dimension": dim,
                                "rank": rank,
                                "entity_key": short,
                                "entity_name": b.get("name") or short,
                                "works_count": b.get("count", 0) or 0,
                            }
                        )
                    await session.execute(
                        delete(SubfieldRanking).where(
                            SubfieldRanking.subfield_id == sid,
                            SubfieldRanking.dimension == dim,
                        )
                    )
                    if rows:
                        await session.execute(insert(SubfieldRanking).values(rows))
                except Exception as exc:  # noqa: BLE001 — keep prior rows on failure
                    print(f"[warn] {dim} subfield {sid}: {exc}")
                await asyncio.sleep(PAUSE)

            await session.commit()
            if i % 25 == 0:
                print(f"  rankings: {i}/{len(subfields)} subfields")
        print("rankings sync complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit, offset=args.offset))
