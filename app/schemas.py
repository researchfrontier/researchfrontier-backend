from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class Author(BaseModel):
    name: str
    position: str | None = None


class Breadcrumb(BaseModel):
    domain_id: int | None = None
    domain_name: str | None = None
    field_id: int | None = None
    field_name: str | None = None
    subfield_id: int | None = None
    subfield_name: str | None = None


class PaperOut(BaseModel):
    id: int
    title: str
    abstract: str | None = None
    authors: list[Author] = []
    doi: str | None = None
    doi_url: str | None = None
    published_doi: str | None = None
    publication_date: date | None = None
    cited_by_count: int = 0
    primary_source_name: str | None = None
    primary_source_type: str | None = None
    landing_page_url: str | None = None
    pdf_url: str | None = None
    is_oa: bool = False
    # Peer-review badge (derived, with its evidence)
    review_status: str
    review_confidence: str
    review_evidence: dict = {}
    primary_topic: str | None = None


class PaperList(BaseModel):
    subfield: Breadcrumb
    window_days: int
    reference_date: date
    total: int                 # papers in the stored feed sample (what we return)
    total_available: int = 0   # true count in the field for this window (OpenAlex)
    has_more: bool = False     # a larger limit could surface more rows (load-more)
    papers: list[PaperOut]


class TopicPapers(BaseModel):
    topic_id: int
    topic_name: str
    subfield: Breadcrumb
    window_days: int
    total_available: int = 0
    has_more: bool = False     # a larger limit could surface more rows (load-more)
    papers: list[PaperOut] = []


class DirectionOut(BaseModel):
    topic_id: int
    topic_name: str
    count: int
    share: float          # fraction of the field's recent output
    delta: int            # change vs the previous window of equal length
    keywords: list[str] = []


class DirectionsOut(BaseModel):
    subfield: Breadcrumb
    window_days: int
    reference_date: date
    total: int
    directions: list[DirectionOut]


class HotField(BaseModel):
    subfield_id: int
    subfield_name: str
    field_id: int
    field_name: str
    domain_name: str
    count: int            # papers in the current window
    prev_count: int
    delta: int
    momentum: float       # relative growth, used for ranking/display


class SubfieldNode(BaseModel):
    id: int
    name: str
    works_count: int


class FieldNode(BaseModel):
    id: int
    name: str
    works_count: int
    subfields: list[SubfieldNode] = []


class DomainNode(BaseModel):
    id: int
    name: str
    works_count: int
    fields: list[FieldNode] = []


class LimitItem(BaseModel):
    label: str
    now: str = "—"       # current/live value where safely available, else "—"
    limit: str           # the free-plan limit (documented)
    live: bool = False    # true when `now` is a live measurement


class ServiceLimit(BaseModel):
    service: str
    plan: str
    items: list[LimitItem] = []
    note: str | None = None


class LimitsOut(BaseModel):
    services: list[ServiceLimit] = []


class DigestOut(BaseModel):
    subfield: Breadcrumb
    edition_date: date
    window_days: int
    headline: str | None = None
    summary: str | None = None
    stats: dict = {}
    papers: list[PaperOut] = []
    generated: bool = False   # true if computed on the fly (no stored edition)
