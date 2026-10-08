"""Live paper lists fetched from OpenAlex (filtered, searchable), with a short TTL
cache. Used for the field papers tab and the topic drill-down, where the small
stored feed sample isn't enough to support search + status filtering well."""

from __future__ import annotations

from datetime import date, timedelta

from ..schemas import PaperOut
from ..sources import openalex
from .cache import papers_cache
from .serialize import normalized_to_paper

# Map a review-status filter to an OpenAlex work-type proxy, so the live query
# returns enough of the requested kind before we refine by the derived badge.
STATUS_TYPE_PROXY = {
    "peer_reviewed": "article|review|book-chapter|conference-paper|book",
    "preprint_published": "article|review",
    "preprint": "preprint",
}


async def live_papers(
    *,
    subfield_id: int | None = None,
    topic_id: int | None = None,
    window: int = 30,
    status: str | None = None,
    search: str | None = None,
    limit: int = 50,
) -> tuple[list[PaperOut], bool]:
    """Return ``(papers[:limit], has_more)``. ``has_more`` tells the UI whether asking
    for a larger ``limit`` could surface more rows — which, under a status filter, is
    NOT the same as ``len(papers) < limit`` (we over-fetch raw works and then drop the
    ones whose derived badge doesn't match, so a short page can just mean the raw window
    was too shallow). It's true when we already hold more matches than ``limit``, or when
    the raw OpenAlex fetch hit its cap (more works remain to examine deeper)."""
    status = status or None
    if status == "all":
        status = None
    key = (
        f"sf={subfield_id}|tp={topic_id}|w={window}|s={status}"
        f"|q={(search or '').strip().lower()}|l={limit}"
    )

    async def factory() -> tuple[list[PaperOut], bool]:
        today = date.today()
        frm = today - timedelta(days=window)
        type_filter = STATUS_TYPE_PROXY.get(status) if status else None
        # Over-fetch when a status is set, since we then refine by the derived badge.
        over = limit if status is None else limit * 3
        max_results = max(over, 80)
        async with openalex.make_client() as client:
            raw = await openalex.fetch_recent_works(
                client,
                from_date=frm,
                to_date=today,
                subfield_id=subfield_id,
                topic_id=topic_id,
                search=search,
                type_filter=type_filter,
                max_results=max_results,
            )
        # Fewer raw works than we asked for => OpenAlex's cursor is exhausted, so no
        # deeper fetch would help; otherwise more works remain to examine.
        raw_exhausted = len(raw) < max_results
        papers = [normalized_to_paper(openalex.normalize_work(r)) for r in raw]
        if status:
            papers = [p for p in papers if p.review_status == status]
        has_more = len(papers) > limit or not raw_exhausted
        return papers[:limit], has_more

    return await papers_cache.get_or_set(key, factory)
