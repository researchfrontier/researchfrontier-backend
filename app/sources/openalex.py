"""OpenAlex client — the taxonomy + works backbone (CC0).

Docs: https://docs.openalex.org . Since ~Feb 2026 a free API key raises the daily
credit budget 10x; the polite-pool ``mailto`` is deprecated in favour of the key,
but we still send a contact email when provided. For large backfills prefer the
monthly S3 snapshot over the live API.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import httpx

from ..config import get_settings
from ..services.badges import compute_badge

settings = get_settings()


def _auth_params() -> dict[str, str]:
    params: dict[str, str] = {}
    if settings.openalex_api_key:
        params["api_key"] = settings.openalex_api_key
    if settings.contact_email:
        params["mailto"] = settings.contact_email
    return params


def make_client() -> httpx.AsyncClient:
    ua = "ResearchFrontier/0.1 (+https://researchfrontier.github.io)"
    if settings.contact_email:
        ua += f" mailto:{settings.contact_email}"
    return httpx.AsyncClient(
        base_url=settings.openalex_base,
        headers={"User-Agent": ua},
        timeout=httpx.Timeout(30.0),
    )


def oaid_to_int(url_or_id: str) -> int | None:
    """'.../subfields/1702' -> 1702 ; '.../T10017' -> 10017."""
    if not url_or_id:
        return None
    seg = url_or_id.rstrip("/").rsplit("/", 1)[-1]
    seg = seg[1:] if seg[:1].upper() == "T" else seg
    return int(seg) if seg.isdigit() else None


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    return doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip().lower() or None


def parse_date(value: str | None) -> date | None:
    """OpenAlex ships dates as 'YYYY-MM-DD' strings; asyncpg needs a date object."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """OpenAlex ships abstracts as an inverted index to respect copyright; rebuild it."""
    if not inverted_index:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions.append((i, word))
    if not positions:
        return None
    positions.sort()
    return " ".join(word for _, word in positions)


async def iter_entities(client: httpx.AsyncClient, entity: str) -> AsyncIterator[dict[str, Any]]:
    """Page through a list entity: 'domains' | 'fields' | 'subfields' | 'topics'."""
    cursor = "*"
    while cursor:
        resp = await client.get(
            f"/{entity}",
            params={"per-page": 200, "cursor": cursor, **_auth_params()},
        )
        resp.raise_for_status()
        payload = resp.json()
        for row in payload.get("results", []):
            yield row
        cursor = payload.get("meta", {}).get("next_cursor")


async def fetch_recent_works(
    client: httpx.AsyncClient,
    *,
    from_date: date,
    to_date: date | None = None,
    subfield_oaid: str | None = None,
    extra_filter: str | None = None,
    max_results: int = 200,
) -> list[dict[str, Any]]:
    """Fetch works published in a date range, optionally restricted to a subfield.

    ``subfield_oaid`` is the OpenAlex subfield URL/id; we filter on
    ``primary_topic.subfield.id`` so a work lands in exactly one field feed.
    """
    filters = [f"from_publication_date:{from_date.isoformat()}"]
    if to_date:
        filters.append(f"to_publication_date:{to_date.isoformat()}")
    if subfield_oaid:
        filters.append(f"primary_topic.subfield.id:{oaid_to_int(subfield_oaid)}")
    if extra_filter:
        filters.append(extra_filter)

    results: list[dict[str, Any]] = []
    cursor = "*"
    while cursor and len(results) < max_results:
        resp = await client.get(
            "/works",
            params={
                "filter": ",".join(filters),
                "sort": "publication_date:desc",
                "per-page": min(200, max_results - len(results)),
                "cursor": cursor,
                **_auth_params(),
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        results.extend(payload.get("results", []))
        cursor = payload.get("meta", {}).get("next_cursor")
    return results[:max_results]


def normalize_work(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a raw OpenAlex work into our normalized shape (+ derived badge)."""
    primary = raw.get("primary_location") or {}
    source = primary.get("source") or {}
    best_oa = raw.get("best_oa_location") or {}
    oa = raw.get("open_access") or {}

    authors = [
        {
            "name": (a.get("author") or {}).get("display_name"),
            "position": a.get("author_position"),
        }
        for a in (raw.get("authorships") or [])
        if (a.get("author") or {}).get("display_name")
    ]

    status, confidence, evidence = compute_badge(raw)

    ptopic = raw.get("primary_topic") or {}
    topics_raw = raw.get("topics") or []
    topics = []
    primary_topic_oaid = ptopic.get("id")
    for t in topics_raw:
        topics.append(
            {
                "topic_oaid": t.get("id"),
                "display_name": t.get("display_name"),
                "score": t.get("score", 0.0),
                "is_primary": t.get("id") == primary_topic_oaid,
                "subfield_oaid": (t.get("subfield") or {}).get("id"),
                "keywords": [k.get("display_name") for k in (raw.get("keywords") or [])][:8],
            }
        )

    return {
        "openalex_id": raw.get("id"),
        "doi": normalize_doi(raw.get("doi")),
        "published_doi": None,
        "title": raw.get("title") or raw.get("display_name") or "(untitled)",
        "abstract": reconstruct_abstract(raw.get("abstract_inverted_index")),
        "authors": authors,
        "publication_date": parse_date(raw.get("publication_date")),
        "publication_year": raw.get("publication_year"),
        "language": raw.get("language"),
        "cited_by_count": raw.get("cited_by_count", 0),
        "primary_source_name": source.get("display_name"),
        "primary_source_type": source.get("type"),
        "openalex_type": raw.get("type"),
        "crossref_type": raw.get("type_crossref"),
        "landing_page_url": primary.get("landing_page_url"),
        "pdf_url": best_oa.get("pdf_url") or primary.get("pdf_url"),
        "is_oa": bool(oa.get("is_oa")),
        "is_retracted": bool(raw.get("is_retracted")),
        "review_status": status,
        "review_confidence": confidence,
        "review_evidence": evidence,
        "primary_topic_oaid": primary_topic_oaid,
        "primary_subfield_oaid": (ptopic.get("subfield") or {}).get("id"),
        "primary_field_oaid": (ptopic.get("field") or {}).get("id"),
        "primary_domain_oaid": (ptopic.get("domain") or {}).get("id"),
        "topics": topics,
    }
