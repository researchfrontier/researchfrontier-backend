from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Domain, Field, Subfield, Work
from ..schemas import Author, Breadcrumb, PaperOut


def work_to_paper(work: Work, primary_topic_name: str | None = None) -> PaperOut:
    authors: list[Author] = []
    for a in work.authors or []:
        if isinstance(a, dict) and a.get("name"):
            authors.append(Author(name=a["name"]))
    doi_url = f"https://doi.org/{work.doi}" if work.doi else work.landing_page_url
    return PaperOut(
        id=work.id,
        title=work.title,
        authors=authors,
        doi=work.doi,
        doi_url=doi_url,
        publication_date=work.publication_date,
        cited_by_count=work.cited_by_count,
        primary_source_name=work.primary_source_name,
        primary_source_type=work.primary_source_type,
        landing_page_url=work.landing_page_url,
        review_status=work.review_status,
        review_confidence=work.review_confidence,
        primary_topic=primary_topic_name,
    )


async def get_breadcrumb(session: AsyncSession, subfield_id: int) -> Breadcrumb:
    row = (
        await session.execute(
            select(
                Subfield.id,
                Subfield.display_name,
                Subfield.description,
                Subfield.wikipedia_url,
                Subfield.wikidata_id,
                Field.id,
                Field.display_name,
                Domain.id,
                Domain.display_name,
            )
            .join(Field, Subfield.field_id == Field.id)
            .join(Domain, Field.domain_id == Domain.id)
            .where(Subfield.id == subfield_id)
        )
    ).first()
    if not row:
        return Breadcrumb(subfield_id=subfield_id)
    sid, sname, sdesc, swiki, swd, fid, fname, did, dname = row
    # A handful of stored Wikipedia URLs carry literal spaces; normalize to underscores
    # so the link resolves.
    wiki = swiki.replace(" ", "_") if swiki else None
    return Breadcrumb(
        domain_id=did,
        domain_name=dname,
        field_id=fid,
        field_name=fname,
        subfield_id=sid,
        subfield_name=sname,
        subfield_description=sdesc,
        subfield_wikipedia_url=wiki,
        subfield_wikidata_id=swd,
    )
