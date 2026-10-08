"""Derive the peer-review / publication badge for a work from its metadata.

Implements the rule table from the design research (OpenAlex primary signal,
Crossref as confirmation). Rules are evaluated top-to-bottom; first match wins.
The result always carries the *evidence* that drove it, so the UI can show "why"
and so a badge is never a bare guess.

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


def compute_badge(work: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Return ``(review_status, confidence, evidence)`` for an OpenAlex work dict."""
    evidence: dict[str, Any] = {}

    oa_type = work.get("type")
    primary = work.get("primary_location") or {}
    source = (primary.get("source") or {}) if isinstance(primary, dict) else {}
    source_type = source.get("type")
    source_name = source.get("display_name")
    indexed_in = work.get("indexed_in") or []
    locs = _locations(work)
    versions = {loc.get("version") for loc in locs}
    source_types = {
        (loc.get("source") or {}).get("type")
        for loc in locs
        if isinstance(loc.get("source"), dict)
    }

    evidence.update(
        openalex_type=oa_type,
        primary_source_type=source_type,
        primary_source_name=source_name,
        versions=sorted(v for v in versions if v),
        indexed_in=indexed_in,
    )

    # 1) Retracted (confirm with Crossref upstream before showing red — see ingest).
    if work.get("is_retracted"):
        evidence["is_retracted"] = True
        return "retracted", "medium", evidence

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
        evidence["rule"] = "journal_published_version"
        return "peer_reviewed", conf, evidence

    # 3) Preprint that now has a published version (both copies present).
    if oa_type == "preprint" and has_published_journal:
        evidence["rule"] = "preprint_with_published_version"
        return "preprint_published", "high", evidence

    # 4) Preprint / posted content.
    if oa_type == "preprint" or (
        source_type in REPOSITORY_TYPES and "publishedVersion" not in versions
    ) or has_repository_submitted:
        evidence["rule"] = "preprint_or_repository"
        return "preprint", "high", evidence

    # 5) A journal article with no version info — published but not individually
    #    confirmed as peer-reviewed research.
    if source_type in PEER_REVIEWED_SOURCE_TYPES:
        evidence["rule"] = "journal_no_version"
        return "peer_reviewed", "low", evidence

    # 6) Everything else.
    evidence["rule"] = "unresolved"
    return "unknown", "low", evidence
