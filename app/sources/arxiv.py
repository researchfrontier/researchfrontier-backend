"""Minimal arXiv client (the fast STEM-preprint feeder).

Uses the public query API (Atom). arXiv tightened throttling on 2026-10-01, so be
gentle (~1 request / 3s) and prefer OAI-PMH for bulk harvesting at scale. For the
slice we fetch the most recent submissions in a category and normalize them as
preprints. DOIs are the automatic DataCite form ``10.48550/arXiv.<id>``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import httpx

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
QUERY_URL = "http://export.arxiv.org/api/query"


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": "ResearchFrontier/0.1 (+https://researchfrontier.github.io)"},
        timeout=httpx.Timeout(30.0),
    )


async def fetch_recent(
    client: httpx.AsyncClient, category: str, max_results: int = 50
) -> list[dict[str, Any]]:
    """Fetch the most recent submissions for an arXiv category (e.g. 'cs.LG')."""
    resp = await client.get(
        QUERY_URL,
        params={
            "search_query": f"cat:{category}",
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": max_results,
        },
    )
    resp.raise_for_status()
    return _parse_atom(resp.text)


def _parse_atom(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    out: list[dict[str, Any]] = []
    for entry in root.findall(f"{ATOM}entry"):
        arxiv_url = (entry.findtext(f"{ATOM}id") or "").strip()
        arxiv_id = arxiv_url.rsplit("/", 1)[-1].split("v")[0]
        published = (entry.findtext(f"{ATOM}published") or "")[:10] or None
        journal_doi = entry.findtext(f"{ARXIV_NS}doi")
        pdf_url = None
        for link in entry.findall(f"{ATOM}link"):
            if link.get("title") == "pdf":
                pdf_url = link.get("href")
        authors = [
            {"name": a.findtext(f"{ATOM}name"), "position": None}
            for a in entry.findall(f"{ATOM}author")
        ]
        out.append(
            {
                "arxiv_id": arxiv_id,
                "openalex_id": None,
                "doi": f"10.48550/arxiv.{arxiv_id}".lower(),
                "published_doi": journal_doi,
                "title": " ".join((entry.findtext(f"{ATOM}title") or "").split()),
                "abstract": " ".join((entry.findtext(f"{ATOM}summary") or "").split()),
                "authors": authors,
                "publication_date": published,
                "publication_year": int(published[:4]) if published else None,
                "language": "en",
                "cited_by_count": 0,
                "primary_source_name": "arXiv",
                "primary_source_type": "repository",
                "openalex_type": "preprint",
                "crossref_type": "posted-content",
                "landing_page_url": arxiv_url,
                "pdf_url": pdf_url,
                "is_oa": True,
                "is_retracted": False,
                "review_status": "preprint_published" if journal_doi else "preprint",
                "review_confidence": "high",
                "review_evidence": {"source": "arxiv", "has_journal_doi": bool(journal_doi)},
            }
        )
    return out
