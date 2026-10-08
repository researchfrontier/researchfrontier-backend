from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Domain, Field, Subfield, Work
from ..schemas import Author, Breadcrumb, PaperOut


def work_to_paper(work: Work, primary_topic_name: str | None = None) -> PaperOut:
    authors: list[Author] = []
    for a in work.authors or []:
        if isinstance(a, dict) and a.get("name"):
            authors.append(Author(name=a["name"], position=a.get("position")))
    doi_url = f"https://doi.org/{work.doi}" if work.doi else work.landing_page_url
    return PaperOut(
        id=work.id,
        title=work.title,
        abstract=work.abstract,
        authors=authors,
        doi=work.doi,
        doi_url=doi_url,
        published_doi=work.published_doi,
        publication_date=work.publication_date,
        cited_by_count=work.cited_by_count,
        primary_source_name=work.primary_source_name,
        primary_source_type=work.primary_source_type,
        landing_page_url=work.landing_page_url,
        pdf_url=work.pdf_url,
        is_oa=work.is_oa,
        review_status=work.review_status,
        review_confidence=work.review_confidence,
        review_evidence=work.review_evidence or {},
        primary_topic=primary_topic_name,
    )


def normalized_to_paper(rec: dict) -> PaperOut:
    """Build a PaperOut from a live OpenAlex-normalized dict (no local DB row)."""
    doi = rec.get("doi")
    oaid = (rec.get("openalex_id") or "").rsplit("/", 1)[-1]
    pid = int(oaid[1:]) if oaid[:1] in ("W", "w") and oaid[1:].isdigit() else 0
    authors = [
        Author(name=a["name"], position=a.get("position"))
        for a in (rec.get("authors") or [])
        if isinstance(a, dict) and a.get("name")
    ]
    primary_topic = next(
        (t.get("display_name") for t in (rec.get("topics") or []) if t.get("is_primary")),
        None,
    )
    return PaperOut(
        id=pid,
        title=rec.get("title") or "(untitled)",
        abstract=rec.get("abstract"),
        authors=authors,
        doi=doi,
        doi_url=(f"https://doi.org/{doi}" if doi else rec.get("landing_page_url")),
        published_doi=rec.get("published_doi"),
        publication_date=rec.get("publication_date"),
        cited_by_count=rec.get("cited_by_count", 0) or 0,
        primary_source_name=rec.get("primary_source_name"),
        primary_source_type=rec.get("primary_source_type"),
        landing_page_url=rec.get("landing_page_url"),
        pdf_url=rec.get("pdf_url"),
        is_oa=bool(rec.get("is_oa")),
        review_status=rec.get("review_status", "unknown"),
        review_confidence=rec.get("review_confidence", "low"),
        review_evidence=rec.get("review_evidence") or {},
        primary_topic=primary_topic,
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
