"""Derive the peer-review / publication badge for a work from its metadata.

Implements the rule table from the design research (OpenAlex primary signal,
Crossref as confirmation). Rules are evaluated top-to-bottom; first match wins.
Every badge follows from an explicit metadata signal, so it is never a bare guess.

Badges:
    retracted            red, overrides everything
    peer_reviewed        journal/conference version of record
    preprint_published   a preprint that now has a peer-reviewed version
    preprint             preprint / posted content, not peer reviewed
    unknown              insufficient or conflicting metadata
"""

from __future__ import annotations

from typing import Any

REPOSITORY_TYPES = {"repository"}
PEER_REVIEWED_SOURCE_TYPES = {"journal", "conference", "book series"}


def _locations(work: dict[str, Any]) -> list[dict[str, Any]]:
    locs = work.get("locations") or []
    if not locs and work.get("primary_location"):
        locs = [work["primary_location"]]
    return [loc for loc in locs if isinstance(loc, dict)]


def compute_badge(work: dict[str, Any]) -> tuple[str, str]:
    """Return ``(review_status, confidence)`` for an OpenAlex work dict."""
    oa_type = work.get("type")
    primary = work.get("primary_location") or {}
    source = (primary.get("source") or {}) if isinstance(primary, dict) else {}
    source_type = source.get("type")
    indexed_in = work.get("indexed_in") or []
    locs = _locations(work)
    versions = {loc.get("version") for loc in locs}

    # 1) Retracted (confirm with Crossref upstream before showing red — see ingest).
    if work.get("is_retracted"):
        return "retracted", "medium"

    has_published_journal = any(
        (loc.get("source") or {}).get("type") in PEER_REVIEWED_SOURCE_TYPES
        and loc.get("version") == "publishedVersion"
        for loc in locs
        if isinstance(loc.get("source"), dict)
    )
    has_repository_submitted = any(
        (loc.get("source") or {}).get("type") in REPOSITORY_TYPES
        and loc.get("version") in (None, "submittedVersion")
        for loc in locs
        if isinstance(loc.get("source"), dict)
    )

    # 2) Peer-reviewed / published: primary venue is a journal/conference AND a
    #    published version exists.
    if source_type in PEER_REVIEWED_SOURCE_TYPES and (
        has_published_journal or "publishedVersion" in versions
    ):
        conf = "high" if "doaj" in indexed_in else "medium"
        return "peer_reviewed", conf

    # 3) Preprint that now has a published version (both copies present).
    if oa_type == "preprint" and has_published_journal:
        return "preprint_published", "high"

    # 4) Preprint / posted content.
    if oa_type == "preprint" or (
        source_type in REPOSITORY_TYPES and "publishedVersion" not in versions
    ) or has_repository_submitted:
        return "preprint", "high"

    # 5) A journal article with no version info — published but not individually
    #    confirmed as peer-reviewed research.
    if source_type in PEER_REVIEWED_SOURCE_TYPES:
        return "peer_reviewed", "low"

    # 6) Everything else.
    return "unknown", "low"
