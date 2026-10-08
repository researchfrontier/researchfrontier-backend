"""QA the per-subfield Wikipedia/Wikidata intro mappings.

Run: ``python -m app.jobs.qa_subfield_links``

Reads every subfield's stored ``wikidata_id`` from the DB and checks its Wikidata
'instance of' (P31). Flags subfields whose link points at a journal, book,
disambiguation page, company, publisher, etc. (not an academic field) — i.e. the
~9-13% of OpenAlex mis-mappings. Curate the output into ``SUBFIELD_LINK_OVERRIDES``
in ``sync_taxonomy`` and re-run the taxonomy sync.

Queries Wikidata only (no OpenAlex key needed); needs the DB populated (all 252
subfields), so run it against the production database.
"""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import select

from ..db import SessionLocal
from ..models import Subfield
from .sync_taxonomy import SUBFIELD_LINK_OVERRIDES

# Wikidata 'instance of' targets that mean the link is NOT an academic field.
BAD_INSTANCE_OF = {
    "Q5633421": "scientific journal",
    "Q737498": "academic journal",
    "Q1002697": "periodical",
    "Q41298": "magazine",
    "Q5398426": "television series",
    "Q571": "book",
    "Q47461344": "written work",
    "Q7725634": "literary work",
    "Q3331189": "edition",
    "Q4167410": "disambiguation page",
    "Q4167836": "wikimedia category",
    "Q783794": "company",
    "Q4830453": "business",
    "Q891723": "public company",
    "Q6881511": "enterprise",
    "Q2085381": "publisher",
    "Q1320047": "book publisher",
    "Q431289": "brand",
    "Q213051": "journal issue",
}

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
UA = "ResearchFrontier-QA/1.0 (+https://researchfrontier.github.io)"


async def _p31_and_sitelinks(qids: list[str]) -> tuple[dict[str, list[str]], dict[str, str]]:
    p31: dict[str, list[str]] = {}
    sitelink: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=40.0, headers={"User-Agent": UA}) as client:
        for i in range(0, len(qids), 50):
            batch = qids[i : i + 50]
            resp = await client.get(
                WIKIDATA_API,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(batch),
                    "props": "claims|sitelinks",
                    "sitefilter": "enwiki",
                    "format": "json",
                },
            )
            resp.raise_for_status()
            for q, ent in resp.json().get("entities", {}).items():
                types: list[str] = []
                for c in ent.get("claims", {}).get("P31", []):
                    try:
                        types.append(c["mainsnak"]["datavalue"]["value"]["id"])
                    except (KeyError, TypeError):
                        pass
                p31[q] = types
                sitelink[q] = (ent.get("sitelinks", {}).get("enwiki", {}) or {}).get("title")
    return p31, sitelink


async def main() -> None:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    Subfield.id,
                    Subfield.display_name,
                    Subfield.wikidata_id,
                    Subfield.wikipedia_url,
                ).order_by(Subfield.id)
            )
        ).all()

    qids = [wd for _, _, wd, _ in rows if wd and wd.startswith("Q")]
    p31, sitelink = await _p31_and_sitelinks(qids)

    flagged = 0
    print("subfield_id\tname\treason\tenwiki_title\tcurrent_url\toverridden")
    for sid, name, wd, wp in rows:
        overridden = sid in SUBFIELD_LINK_OVERRIDES
        if not wd:
            print(f"{sid}\t{name}\tno-wikidata\t\t{wp}\t{overridden}")
            flagged += 1
            continue
        types = p31.get(wd, [])
        bad = sorted({BAD_INSTANCE_OF[t] for t in types if t in BAD_INSTANCE_OF})
        if bad:
            # High-precision: the target is a journal/book/disambiguation/company, etc.
            print(f"{sid}\t{name}\t{', '.join(bad)}\t{sitelink.get(wd)}\t{wp}\t{overridden}")
            flagged += 1

    print(f"\nFLAGGED: {flagged} / {len(rows)} subfields "
          f"({len(SUBFIELD_LINK_OVERRIDES)} already overridden)")
    print("Curate the above into SUBFIELD_LINK_OVERRIDES in app/jobs/sync_taxonomy.py, "
          "then re-run: python -m app.jobs.sync_taxonomy")


if __name__ == "__main__":
    asyncio.run(main())
