"""On-demand Trends analytics not precomputed in the cron: the per-institution
reverse view (which fields an institution is most active on), fetched live from
OpenAlex and cached for a day. The field-centric Trends data is served straight from
Postgres by the trends router (zero live calls)."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..sources import openalex
from .cache import TTLCache

# Reverse-view results change slowly; a day-long cache keeps per-institution views to
# at most one OpenAlex call per institution per day.
trends_cache = TTLCache(ttl=86400.0)


async def institution_search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    key = f"inst-search|{q.lower()}|{limit}"

    async def factory() -> list[dict[str, Any]]:
        async with openalex.make_client() as client:
            return await openalex.search_institutions(client, query=q, limit=limit)

    return await trends_cache.get_or_set(key, factory)


async def institution_fields(inst_id: str, limit: int = 12) -> list[dict[str, Any]]:
    """Which subfields an institution is most active on (last ~3 years), one cached
    OpenAlex group_by call."""
    iid = (inst_id or "").strip()
    if not iid:
        return []
    key = f"inst-fields|{iid}|{limit}"

    async def factory() -> list[dict[str, Any]]:
        today = date.today()
        frm = date(today.year - 3, 1, 1)
        async with openalex.make_client() as client:
            buckets = await openalex.group_raw(
                client,
                group_by="primary_topic.subfield.id",
                from_date=frm,
                to_date=today,
                extra_filter=f"authorships.institutions.id:{iid}",
                limit=limit,
            )
        out: list[dict[str, Any]] = []
        for b in buckets:
            out.append(
                {
                    "subfield_id": openalex.oaid_to_int(b.get("key") or ""),
                    "name": b.get("name"),
                    "count": b.get("count", 0) or 0,
                }
            )
        return out

    return await trends_cache.get_or_set(key, factory)
