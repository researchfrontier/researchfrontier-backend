"""Ingest recent papers from OpenAlex (and optionally arXiv) into the DB.

Run: ``python -m app.jobs.ingest --days 7 --max-per-field 100``

For each research field (OpenAlex subfield) already in the taxonomy, pulls works
published in the last N days, normalizes them, derives the peer-review badge, and
upserts (idempotently, keyed on OpenAlex id or DOI). Later this is scoped to the
subfields users actually follow; for the slice it sweeps the known taxonomy.

Writes are **batched per subfield**: a single bulk ``INSERT ... ON CONFLICT DO
UPDATE`` for the works, then a bulk replace of their topic links. The earlier
row-at-a-time path issued ~hundreds of sequential statements per subfield and was
latency-bound against the remote (Neon) database — sweeping all 252 subfields
could not finish inside the CI time budget (it burned the 6h job limit and was
killed). Batching collapses that to a handful of round-trips per subfield. A
per-row fallback preserves the "one bad row doesn't sink the batch" guarantee, and
transient deadlocks are retried.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError

from ..db import SessionLocal
from ..models import Domain, Field, Subfield, Topic, Work, WorkTopic
from ..sources import openalex

# Postgres SQLSTATEs worth retrying: deadlock detected and serialization failure.
# A bulk write briefly share-locks many FK-parent rows at once, so a concurrent
# taxonomy/stats update can occasionally deadlock against it; the lock window is
# tiny, so a short retry clears it.
_RETRY_SQLSTATES = {"40P01", "40001"}

# Columns written for a Work (also the ON CONFLICT update set), kept in one place so
# the bulk and single-row paths can't drift.
_WORK_COLS = (
    "openalex_id", "doi", "published_doi", "title", "abstract", "authors",
    "publication_date", "publication_year", "language", "cited_by_count",
    "primary_source_name", "primary_source_type", "openalex_type", "crossref_type",
    "landing_page_url", "pdf_url", "is_oa", "is_retracted", "review_status",
    "review_confidence", "review_evidence", "primary_topic_id", "primary_subfield_id",
    "primary_field_id", "primary_domain_id",
)


async def _load_maps(session):
    """openalex_url -> local numeric id, for each taxonomy level."""
    maps = {}
    for model, key in ((Domain, "domain"), (Field, "field"), (Subfield, "subfield"), (Topic, "topic")):
        rows = (await session.execute(select(model.openalex_id, model.id))).all()
        maps[key] = {url: lid for url, lid in rows}
    return maps


def _work_values(maps, rec: dict) -> dict:
    """Normalized record -> column values for the ``work`` table."""
    return {
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
        "primary_topic_id": maps["topic"].get(rec.get("primary_topic_oaid")),
        "primary_subfield_id": maps["subfield"].get(rec.get("primary_subfield_oaid")),
        "primary_field_id": maps["field"].get(rec.get("primary_field_oaid")),
        "primary_domain_id": maps["domain"].get(rec.get("primary_domain_oaid")),
    }


def _topic_links(maps, rec: dict) -> list[dict]:
    """Normalized record -> work_topic rows (without work_id), de-duped by topic."""
    seen: set[int] = set()
    links: list[dict] = []
    for t in rec.get("topics", []):
        tid = maps["topic"].get(t.get("topic_oaid"))
        if tid is None or tid in seen:
            continue
        seen.add(tid)
        links.append(
            {"topic_id": tid, "score": t.get("score", 0.0) or 0.0, "is_primary": bool(t.get("is_primary"))}
        )
    return links


def _drop_doi_conflicts(rows: list[dict], existing: dict[str, str]) -> tuple[list[dict], int]:
    """Drop rows whose non-null DOI would violate ``work.doi``'s unique index — a DOI
    duplicated within this batch, or one already stored under a *different*
    openalex_id. ``work`` carries two unique indexes (openalex_id and doi); the bulk
    ``ON CONFLICT`` can only arbitrate on one, so a DOI clash would raise 23505 and
    sink the whole batch. These are exactly the rows the old per-row path skipped.
    ``existing`` maps doi -> owning openalex_id already in the DB. Returns (kept, dropped)."""
    kept: list[dict] = []
    seen: set[str] = set()
    dropped = 0
    for v in rows:
        doi = v["doi"]
        if doi:
            owner = existing.get(doi)
            if (owner is not None and owner != v["openalex_id"]) or doi in seen:
                dropped += 1
                continue
            seen.add(doi)
        kept.append(v)
    return kept, dropped


async def _bulk_upsert(session, maps, recs: list[dict]) -> int:
    """Bulk-upsert the works carrying an OpenAlex id (all of them, in practice) plus
    their topic links, in a handful of statements. Returns the number of works."""
    # Dedup within the batch on the conflict key: ``ON CONFLICT DO UPDATE`` may not
    # touch the same openalex_id twice in one statement. Keep the last occurrence.
    by_oaid: dict[str, dict] = {}
    for rec in recs:
        oaid = rec.get("openalex_id")
        if oaid:
            by_oaid[oaid] = rec
    if not by_oaid:
        return 0

    # Stable order so concurrent batches take row locks in the same order.
    rows = sorted((_work_values(maps, r) for r in by_oaid.values()), key=lambda v: v["openalex_id"])

    # Keep a lone DOI clash from blowing up (and demoting) the whole batch.
    dois = [v["doi"] for v in rows if v["doi"]]
    existing: dict[str, str] = {}
    if dois:
        existing = dict(
            (await session.execute(select(Work.doi, Work.openalex_id).where(Work.doi.in_(dois)))).all()
        )
    rows, dropped = _drop_doi_conflicts(rows, existing)
    if dropped:
        print(f"[skip] {dropped} work(s) with a conflicting DOI", flush=True)
    if not rows:
        return 0

    stmt = insert(Work).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Work.openalex_id],
        set_={c: stmt.excluded[c] for c in _WORK_COLS},
    ).returning(Work.id, Work.openalex_id)
    id_by_oaid = {oaid: wid for wid, oaid in (await session.execute(stmt)).all()}

    # Replace the topic links for exactly these works: bulk delete, then bulk insert.
    work_ids = list(id_by_oaid.values())
    await session.execute(delete(WorkTopic).where(WorkTopic.work_id.in_(work_ids)))

    wt_rows: list[dict] = []
    seen_pairs: set[tuple[int, int]] = set()
    for oaid, rec in by_oaid.items():
        wid = id_by_oaid.get(oaid)
        if wid is None:
            continue
        for link in _topic_links(maps, rec):
            pair = (wid, link["topic_id"])
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            wt_rows.append({"work_id": wid, **link})
    if wt_rows:
        await session.execute(insert(WorkTopic).values(wt_rows).on_conflict_do_nothing())
    return len(work_ids)


async def _upsert_one(session, maps, rec: dict) -> int | None:
    """Upsert a single work (+ its topic links). Dedup target: OpenAlex id if
    present, else DOI; records with neither are skipped. Used by the fallback path
    and for DOI-only records the bulk path (keyed on openalex_id) can't carry."""
    values = _work_values(maps, rec)
    if values["openalex_id"]:
        conflict = [Work.openalex_id]
    elif values["doi"]:
        conflict = [Work.doi]
    else:
        return None

    stmt = (
        insert(Work)
        .values(**values)
        .on_conflict_do_update(index_elements=conflict, set_={c: values[c] for c in _WORK_COLS})
        .returning(Work.id)
    )
    work_id = await session.scalar(stmt)
    await session.execute(delete(WorkTopic).where(WorkTopic.work_id == work_id))
    for link in _topic_links(maps, rec):
        await session.execute(
            insert(WorkTopic).values(work_id=work_id, **link).on_conflict_do_nothing()
        )
    return work_id


async def _upsert_doi_only(session, maps, recs: list[dict]) -> int:
    """Upsert records that have a DOI but no OpenAlex id (e.g. a future arXiv source)
    via the per-row path — the bulk path keys on openalex_id and can't carry them.
    Each row is savepoint-isolated so it can't abort the bulk transaction. For the
    current OpenAlex-only sweep this is a no-op (every work has an id)."""
    count = 0
    for rec in recs:
        if rec.get("openalex_id") or not rec.get("doi"):
            continue
        try:
            async with session.begin_nested():
                if await _upsert_one(session, maps, rec) is not None:
                    count += 1
        except Exception as exc:  # noqa: BLE001 — skip one row, keep going
            print(f"[skip] {rec.get('doi')}: {exc}", flush=True)
    return count


def _is_retryable(exc: BaseException) -> bool:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(exc, "sqlstate", None)
    return sqlstate in _RETRY_SQLSTATES


async def _write_subfield(session, maps, raws: list[dict]) -> int:
    """Persist one subfield's works. Fast bulk path with a deadlock retry; on a
    non-transient write error fall back to the resilient row-at-a-time path so a
    single bad row is skipped, not the whole subfield. The fallback is logged so a
    drift back toward the old slow behaviour is visible before the timeout fires."""
    recs = [openalex.normalize_work(r) for r in raws]

    for attempt in range(3):
        try:
            count = await _bulk_upsert(session, maps, recs)
            count += await _upsert_doi_only(session, maps, recs)
            await session.commit()
            return count
        except DBAPIError as exc:
            await session.rollback()
            if _is_retryable(exc) and attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            reason = (str(exc).splitlines() or [exc.__class__.__name__])[0][:160]
            print(f"[fallback] bulk write failed ({reason}); per-row path", flush=True)
            break

    # Fallback: per-row with savepoints, so one bad row doesn't abort the batch.
    count = 0
    for rec in recs:
        try:
            async with session.begin_nested():  # savepoint: isolate bad rows
                wid = await _upsert_one(session, maps, rec)
            if wid is not None:
                count += 1
        except Exception as exc:  # noqa: BLE001 — skip one row, keep going
            print(f"[skip] {rec.get('openalex_id') or rec.get('doi')}: {exc}", flush=True)
    await session.commit()
    return count


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
                print(f"[warn] subfield {sid} fetch failed: {exc}", flush=True)
                continue

            try:
                count = await _write_subfield(session, maps, raw)
            except Exception as exc:  # noqa: BLE001 — never let one subfield sink the sweep
                await session.rollback()
                print(f"[warn] subfield {sid} write failed: {exc}", flush=True)
                continue

            total += count
            print(f"subfield {sid}: fetched {len(raw)}, upserted {count}", flush=True)
        print(f"ingest complete: {total} works upserted", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
