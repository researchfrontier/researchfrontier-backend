"""Ingest recent papers from OpenAlex (and optionally arXiv) into the DB.

Run: ``python -m app.jobs.ingest --days 7 --max-per-field 100``

For each research field (OpenAlex subfield) already in the taxonomy, pulls works
published in the last N days, normalizes them, derives the peer-review badge, and
upserts (idempotently, keyed on OpenAlex id or DOI). Later this is scoped to the
subfields users actually follow; for the slice it sweeps the known taxonomy.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ..db import SessionLocal
from ..models import Domain, Field, Subfield, Topic, Work, WorkTopic
from ..sources import openalex


async def _load_maps(session):
    """openalex_url -> local numeric id, for each taxonomy level."""
    maps = {}
    for model, key in ((Domain, "domain"), (Field, "field"), (Subfield, "subfield"), (Topic, "topic")):
        rows = (await session.execute(select(model.openalex_id, model.id))).all()
        maps[key] = {url: lid for url, lid in rows}
    return maps


async def _upsert_work(session, maps, rec: dict) -> int | None:
    topic_id = maps["topic"].get(rec.get("primary_topic_oaid"))
    subfield_id = maps["subfield"].get(rec.get("primary_subfield_oaid"))
    field_id = maps["field"].get(rec.get("primary_field_oaid"))
    domain_id = maps["domain"].get(rec.get("primary_domain_oaid"))

    values = {
        "openalex_id": rec.get("openalex_id"),
        "doi": rec.get("doi"),
        "published_doi": rec.get("published_doi"),
        "title": rec.get("title") or "(untitled)",
        "abstract": rec.get("abstract"),
        "authors": rec.get("authors") or [],
        "publication_date": rec.get("publication_date"),
        "publication_year": rec.get("publication_year"),
        "language": rec.get("language"),
        "cited_by_count": rec.get("cited_by_count", 0) or 0,
        "primary_source_name": rec.get("primary_source_name"),
        "primary_source_type": rec.get("primary_source_type"),
        "openalex_type": rec.get("openalex_type"),
        "crossref_type": rec.get("crossref_type"),
        "landing_page_url": rec.get("landing_page_url"),
        "pdf_url": rec.get("pdf_url"),
        "is_oa": bool(rec.get("is_oa")),
        "is_retracted": bool(rec.get("is_retracted")),
        "review_status": rec.get("review_status", "unknown"),
        "review_confidence": rec.get("review_confidence", "low"),
        "review_evidence": rec.get("review_evidence") or {},
        "primary_topic_id": topic_id,
        "primary_subfield_id": subfield_id,
        "primary_field_id": field_id,
        "primary_domain_id": domain_id,
    }

    # Dedup target: OpenAlex id if present, else DOI. Skip records with neither.
    if values["openalex_id"]:
        conflict = [Work.openalex_id]
    elif values["doi"]:
        conflict = [Work.doi]
    else:
        return None

    update_cols = {k: v for k, v in values.items()}
    stmt = (
        insert(Work)
        .values(**values)
        .on_conflict_do_update(index_elements=conflict, set_=update_cols)
        .returning(Work.id)
    )
    work_id = await session.scalar(stmt)

    # Replace the work's topic links.
    await session.execute(delete(WorkTopic).where(WorkTopic.work_id == work_id))
    seen: set[int] = set()
    for t in rec.get("topics", []):
        tid = maps["topic"].get(t.get("topic_oaid"))
        if tid is None or tid in seen:
            continue
        seen.add(tid)
        await session.execute(
            insert(WorkTopic)
            .values(
                work_id=work_id,
                topic_id=tid,
                score=t.get("score", 0.0) or 0.0,
                is_primary=bool(t.get("is_primary")),
            )
            .on_conflict_do_nothing()
        )
    return work_id


async def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest recent papers.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--max-per-field", type=int, default=100)
    parser.add_argument("--subfield", type=int, default=None, help="limit to one subfield id")
    args = parser.parse_args()

    from_date = date.today() - timedelta(days=args.days)

    async with openalex.make_client() as client, SessionLocal() as session:
        maps = await _load_maps(session)
        q = select(Subfield.id, Subfield.openalex_id).order_by(Subfield.id)
        if args.subfield:
            q = q.where(Subfield.id == args.subfield)
        subfields = (await session.execute(q)).all()

        total = 0
        for sid, soaid in subfields:
            try:
                raw = await openalex.fetch_recent_works(
                    client,
                    from_date=from_date,
                    subfield_oaid=soaid,
                    max_results=args.max_per_field,
                )
            except Exception as exc:  # noqa: BLE001 — keep sweeping other fields
                print(f"[warn] subfield {sid} fetch failed: {exc}")
                continue
            for raw_work in raw:
                rec = openalex.normalize_work(raw_work)
                try:
                    async with session.begin_nested():  # savepoint: isolate bad rows
                        wid = await _upsert_work(session, maps, rec)
                    if wid is not None:
                        total += 1
                except Exception as exc:  # noqa: BLE001 — skip one row, keep going
                    print(f"[skip] {rec.get('openalex_id')}: {exc}")
            await session.commit()
            print(f"subfield {sid}: ingested {len(raw)} works")
        print(f"ingest complete: {total} works upserted")


if __name__ == "__main__":
    asyncio.run(main())
