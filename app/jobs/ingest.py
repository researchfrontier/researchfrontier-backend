"""Ingest recent papers from OpenAlex into the DB.

Run: ``python -m app.jobs.ingest --days 7`` (daily) / ``--days 30`` (weekly sweep)

For each research field (OpenAlex subfield) already in the taxonomy, pulls EVERY
work published in the last N days (all pages, so the store holds the field's
complete recent output, not a sample), normalizes them, derives the peer-review
badge, and upserts (idempotently, keyed on OpenAlex id or DOI). OpenAlex indexes
many works days or weeks after their publication date, and the free plan can't
filter on the date a work was added, so a daily 7-day pass is backed by a weekly
30-day sweep. Works past the longest window the UI shows are then pruned, so the
store stays bounded.

Writes are **batched per page** (up to 200 works): a single bulk ``INSERT ... ON
CONFLICT DO UPDATE`` that skips rows which came back unchanged. The earlier
row-at-a-time path issued ~hundreds of sequential statements per subfield and was
latency-bound against the remote (Neon) database — sweeping all 252 subfields
could not finish inside the CI time budget (it burned the 6h job limit and was
killed). Batching collapses that to one round-trip per page. A per-row fallback
preserves the "one bad row doesn't sink the batch" guarantee, and transient
deadlocks are retried.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError

from ..db import SessionLocal
from ..models import Digest, Subfield, Topic, Work
from ..services.time_window import MAX_WINDOW_DAYS, reference_date
from ..sources import openalex

# Postgres SQLSTATEs worth retrying: deadlock detected and serialization failure.
# A bulk write briefly share-locks many FK-parent rows at once, so a concurrent
# taxonomy/stats update can occasionally deadlock against it; the lock window is
# tiny, so a short retry clears it.
_RETRY_SQLSTATES = {"40P01", "40001"}

# Columns written for a Work (also the ON CONFLICT update set), kept in one place so
# the bulk and single-row paths can't drift.
_WORK_COLS = (
    "openalex_id", "doi", "title", "authors", "publication_date", "cited_by_count",
    "primary_source_name", "primary_source_type", "landing_page_url", "review_status",
    "review_confidence", "primary_topic_id", "primary_subfield_id",
)

# Authors kept per work: the byline shows up to 3 names, beyond that "First et al.",
# so a 4th name is all it takes to tell there are more than three.
BYLINE_AUTHORS = 4


async def _load_maps(session):
    """openalex_url -> local numeric id, for the taxonomy levels a work points at."""
    maps = {}
    for model, key in ((Subfield, "subfield"), (Topic, "topic")):
        rows = (await session.execute(select(model.openalex_id, model.id))).all()
        maps[key] = {url: lid for url, lid in rows}
    return maps


def _work_values(maps, rec: dict) -> dict:
    """Normalized record -> column values for the ``work`` table. Only what the paper
    lists render is kept: the byline's authors, and the landing page only when there
    is no DOI (the DOI link wins)."""
    doi = rec.get("doi")
    return {
        "openalex_id": rec.get("openalex_id"),
        "doi": doi,
        "title": rec.get("title") or "(untitled)",
        "authors": (rec.get("authors") or [])[:BYLINE_AUTHORS],
        "publication_date": rec.get("publication_date"),
        "cited_by_count": rec.get("cited_by_count", 0) or 0,
        "primary_source_name": rec.get("primary_source_name"),
        "primary_source_type": rec.get("primary_source_type"),
        "landing_page_url": None if doi else rec.get("landing_page_url"),
        "review_status": rec.get("review_status", "unknown"),
        "review_confidence": rec.get("review_confidence", "low"),
        "primary_topic_id": maps["topic"].get(rec.get("primary_topic_oaid")),
        "primary_subfield_id": maps["subfield"].get(rec.get("primary_subfield_oaid")),
    }


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
    """Bulk-upsert the works carrying an OpenAlex id (all of them, in practice) in a
    handful of statements. Returns the number of works inserted or changed (rows
    that came back identical are left untouched)."""
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
        # The 7/30-day lookbacks mostly re-read known works: rewriting identical rows
        # would only leave dead tuples behind (table bloat on a size-capped database).
        where=or_(
            *(getattr(Work, c).is_distinct_from(stmt.excluded[c]) for c in _WORK_COLS)
        ),
    ).returning(Work.id)
    return len((await session.execute(stmt)).all())


async def _upsert_one(session, maps, rec: dict) -> int | None:
    """Upsert a single work. Dedup target: OpenAlex id if
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
    return await session.scalar(stmt)


async def _upsert_doi_only(session, maps, recs: list[dict]) -> int:
    """Upsert records that have a DOI but no OpenAlex id (e.g. from a future source)
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


async def _write_page(session, maps, raws: list[dict]) -> int:
    """Persist one page of a subfield's works. Fast bulk path with a deadlock retry;
    on a non-transient write error fall back to the resilient row-at-a-time path so
    a single bad row is skipped, not the whole page. The fallback is logged so a
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


async def _prune_expired(session) -> tuple[int, int]:
    """Delete the works past the longest window the UI shows (no list can reach them
    any more) and the digest editions whose window reached into them, keeping the
    store bounded at ~MAX_WINDOW_DAYS of papers. Returns (works, digests) deleted."""
    cutoff = await reference_date(session) - timedelta(days=MAX_WINDOW_DAYS)
    works = await session.execute(
        delete(Work).where(or_(Work.publication_date.is_(None), Work.publication_date <= cutoff))
    )
    digests = await session.execute(
        delete(Digest).where(Digest.edition_date - Digest.window_days < cutoff)
    )
    await session.commit()
    return works.rowcount, digests.rowcount


async def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest recent papers.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--max-per-field", type=int, default=None, help="cap per field (default: every work)"
    )
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
            fetched = written = 0
            try:
                # Page by page, so even the largest field never sits in memory at once;
                # each page is committed, so a failure keeps what was already written.
                async for page in openalex.iter_recent_work_pages(
                    client,
                    from_date=from_date,
                    subfield_oaid=soaid,
                    max_results=args.max_per_field,
                ):
                    fetched += len(page)
                    written += await _write_page(session, maps, page)
            except Exception as exc:  # noqa: BLE001 — never let one subfield sink the sweep
                await session.rollback()
                print(f"[warn] subfield {sid} stopped after {fetched} works: {exc}", flush=True)
                total += written
                continue

            total += written
            print(f"subfield {sid}: fetched {fetched}, new or changed {written}", flush=True)
        print(f"ingest complete: {total} works new or changed", flush=True)

        works, digests = await _prune_expired(session)
        print(
            f"pruned {works} work(s) and {digests} digest edition(s) past the "
            f"{MAX_WINDOW_DAYS}-day window",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
