"""Sync the research-field taxonomy from OpenAlex into our DB.

Run: ``python -m app.jobs.sync_taxonomy``

This is the source of truth for the taxonomy. New/emerging fields enter simply by
re-running it: OpenAlex periodically mints new topics/subfields with stable ids,
and the upsert slots them in without disturbing existing rows. Loads in
dependency order (domains → fields → subfields → topics).
"""

from __future__ import annotations

import asyncio

from sqlalchemy.dialects.postgresql import insert

from ..db import SessionLocal
from ..models import Domain, Field, Subfield, Topic
from ..sources import openalex

# Curated corrections for subfield -> Wikipedia/Wikidata intro links. OpenAlex
# mis-maps ~9-13% of subfields to a journal/book/disambiguation/narrower topic; these
# overrides repoint the wrong ones and PERSIST across syncs (applied after the OpenAlex
# pull). Extend from the `python -m app.jobs.qa_subfield_links` report.
SUBFIELD_LINK_OVERRIDES: dict[int, dict[str, str]] = {
    # Verified 2026-10-08 with app.jobs.qa_subfield_links (Wikidata P31) over all 252
    # subfields: these mapped to a journal / book / company / disambiguation page, or a
    # non-existent English article, or a wrong concept. Repointed to the field article.
    2002: {"wikipedia_url": "https://en.wikipedia.org/wiki/Economics", "wikidata_id": "Q8134"},           # was "Econometrics"
    3103: {"wikipedia_url": "https://en.wikipedia.org/wiki/Astronomy", "wikidata_id": "Q333"},            # was the journal "Astronomy and Astrophysics"
    1802: {"wikipedia_url": "https://en.wikipedia.org/wiki/Information_system", "wikidata_id": "Q121182"}, # was a book
    2100: {"wikipedia_url": "https://en.wikipedia.org/wiki/Energy", "wikidata_id": "Q11379"},             # was a Czech company
    2103: {"wikipedia_url": "https://en.wikipedia.org/wiki/Fuel", "wikidata_id": "Q42501"},               # had no English article
    2207: {"wikipedia_url": "https://en.wikipedia.org/wiki/Control_engineering", "wikidata_id": "Q4917288"},  # was a journal
    2302: {"wikipedia_url": "https://en.wikipedia.org/wiki/Ecosystem_model", "wikidata_id": "Q295046"},   # had no English article
    2712: {"wikipedia_url": "https://en.wikipedia.org/wiki/Endocrinology", "wikidata_id": "Q162606"},     # was a journal
    2740: {"wikipedia_url": "https://en.wikipedia.org/wiki/Pulmonology", "wikidata_id": "Q203337"},       # was a journal
    3303: {"wikipedia_url": "https://en.wikipedia.org/wiki/Development_studies", "wikidata_id": "Q651571"},  # was "Planned community"
    3310: {"wikipedia_url": "https://en.wikipedia.org/wiki/Linguistics", "wikidata_id": "Q8162"},         # was a book
    3311: {"wikipedia_url": "https://en.wikipedia.org/wiki/Safety", "wikidata_id": "Q10566551"},          # had no English article
    3319: {"wikipedia_url": "https://en.wikipedia.org/wiki/Life_course_approach", "wikidata_id": "Q1811049"},  # was "lifetime"
    3603: {"wikipedia_url": "https://en.wikipedia.org/wiki/Alternative_medicine", "wikidata_id": "Q188504"},   # had no English article
    3607: {"wikipedia_url": "https://en.wikipedia.org/wiki/Medical_laboratory", "wikidata_id": "Q2296168"},    # had no English article
    3616: {"wikipedia_url": "https://en.wikipedia.org/wiki/Speech-language_pathology"},                   # was a written work
}


async def _sync_level(session, client, entity, model, parent_key, parent_attr):
    batch: list[dict] = []
    count = 0

    async def flush():
        nonlocal batch
        if not batch:
            return
        stmt = insert(model).values(batch)
        update_cols = {
            c.name: stmt.excluded[c.name]
            for c in model.__table__.columns
            if c.name not in ("id",)
        }
        stmt = stmt.on_conflict_do_update(index_elements=[model.id], set_=update_cols)
        await session.execute(stmt)
        await session.commit()
        batch = []

    async for row in openalex.iter_entities(client, entity):
        oaid = openalex.oaid_to_int(row.get("id", ""))
        if oaid is None:
            continue
        values = {
            "id": oaid,
            "openalex_id": row.get("id"),
            "display_name": row.get("display_name") or "",
            "description": row.get("description"),
            "works_count": row.get("works_count", 0) or 0,
        }
        if parent_key:
            parent = row.get(parent_key) or {}
            values[parent_attr] = openalex.oaid_to_int(parent.get("id", ""))
        if model is Subfield:
            # Encyclopedic intro link per field (shown to non-experts). ids.wikipedia is a
            # canonical article URL; ids.wikidata is a permanent QID anchor (Q-number).
            ids = row.get("ids") or {}
            wiki = ids.get("wikipedia")
            values["wikipedia_url"] = wiki.replace(" ", "_") if wiki else None
            wd = ids.get("wikidata") or ""
            values["wikidata_id"] = wd.rstrip("/").rsplit("/", 1)[-1] or None if wd else None
            # Apply curated corrections for known OpenAlex mis-maps (persist across syncs).
            override = SUBFIELD_LINK_OVERRIDES.get(oaid)
            if override:
                if override.get("wikipedia_url"):
                    values["wikipedia_url"] = override["wikipedia_url"]
                if override.get("wikidata_id"):
                    values["wikidata_id"] = override["wikidata_id"]
        if model is Topic:
            kws = row.get("keywords") or []
            values["keywords"] = [k if isinstance(k, str) else k.get("display_name", "") for k in kws]
        batch.append(values)
        count += 1
        if len(batch) >= 200:
            await flush()
    await flush()
    return count


async def main() -> None:
    async with openalex.make_client() as client, SessionLocal() as session:
        n_d = await _sync_level(session, client, "domains", Domain, None, None)
        print(f"domains: {n_d}")
        n_f = await _sync_level(session, client, "fields", Field, "domain", "domain_id")
        print(f"fields: {n_f}")
        n_s = await _sync_level(session, client, "subfields", Subfield, "field", "field_id")
        print(f"subfields: {n_s}")
        n_t = await _sync_level(session, client, "topics", Topic, "subfield", "subfield_id")
        print(f"topics: {n_t}")
    print("taxonomy sync complete")


if __name__ == "__main__":
    asyncio.run(main())
