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
