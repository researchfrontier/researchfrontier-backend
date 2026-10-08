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
    total: int
    papers: list[PaperOut]


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


class DigestOut(BaseModel):
    subfield: Breadcrumb
    edition_date: date
    window_days: int
    headline: str | None = None
    summary: str | None = None
    stats: dict = {}
    papers: list[PaperOut] = []
    generated: bool = False   # true if computed on the fly (no stored edition)
