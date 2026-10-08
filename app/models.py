"""SQLAlchemy models mirroring the canonical schema in researchfrontier-db.

These do NOT create the tables (the db repo owns the DDL). They map the existing
tables for querying and upserts. The two PG enums are referenced with
``create_type=False`` so SQLAlchemy binds/reads them correctly without trying to
(re)create the type.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

REVIEW_STATUS = ENUM(
    "peer_reviewed",
    "preprint_published",
    "preprint",
    "retracted",
    "unknown",
    name="review_status",
    create_type=False,
)
CONFIDENCE = ENUM("high", "medium", "low", name="confidence_level", create_type=False)


class Base(DeclarativeBase):
    pass


class Domain(Base):
    __tablename__ = "domain"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    openalex_id: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    works_count: Mapped[int] = mapped_column(BigInteger, default=0)


class Field(Base):
    __tablename__ = "field"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    openalex_id: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domain.id"))
    works_count: Mapped[int] = mapped_column(BigInteger, default=0)


class Subfield(Base):
    __tablename__ = "subfield"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    openalex_id: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("field.id"))
    works_count: Mapped[int] = mapped_column(BigInteger, default=0)


class Topic(Base):
    __tablename__ = "topic"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    openalex_id: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    subfield_id: Mapped[int] = mapped_column(ForeignKey("subfield.id"))
    works_count: Mapped[int] = mapped_column(BigInteger, default=0)


class Work(Base):
    __tablename__ = "work"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    openalex_id: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    doi: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    published_doi: Mapped[str | None] = mapped_column(Text, nullable=True)

    title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    authors: Mapped[list] = mapped_column(JSONB, default=list)

    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    publication_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingested_date: Mapped[date] = mapped_column(Date, server_default=func.current_date())

    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    cited_by_count: Mapped[int] = mapped_column(Integer, default=0)

    primary_source_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_source_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    openalex_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    crossref_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    landing_page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_oa: Mapped[bool] = mapped_column(Boolean, default=False)

    is_retracted: Mapped[bool] = mapped_column(Boolean, default=False)
    review_status: Mapped[str] = mapped_column(REVIEW_STATUS, default="unknown")
    review_confidence: Mapped[str] = mapped_column(CONFIDENCE, default="low")
    review_evidence: Mapped[dict] = mapped_column(JSONB, default=dict)

    primary_topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topic.id"), nullable=True
    )
    primary_subfield_id: Mapped[int | None] = mapped_column(
        ForeignKey("subfield.id"), nullable=True
    )
    primary_field_id: Mapped[int | None] = mapped_column(
        ForeignKey("field.id"), nullable=True
    )
    primary_domain_id: Mapped[int | None] = mapped_column(
        ForeignKey("domain.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkTopic(Base):
    __tablename__ = "work_topic"
    work_id: Mapped[int] = mapped_column(ForeignKey("work.id"), primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topic.id"), primary_key=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class SubfieldStats(Base):
    __tablename__ = "subfield_stats"
    subfield_id: Mapped[int] = mapped_column(ForeignKey("subfield.id"), primary_key=True)
    works_7d: Mapped[int] = mapped_column(BigInteger, default=0)
    works_30d: Mapped[int] = mapped_column(BigInteger, default=0)
    works_prev_30d: Mapped[int] = mapped_column(BigInteger, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TopicStats(Base):
    __tablename__ = "topic_stats"
    topic_id: Mapped[int] = mapped_column(ForeignKey("topic.id"), primary_key=True)
    works_30d: Mapped[int] = mapped_column(BigInteger, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Digest(Base):
    __tablename__ = "digest"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subfield_id: Mapped[int] = mapped_column(ForeignKey("subfield.id"))
    edition_date: Mapped[date] = mapped_column(Date)
    window_days: Mapped[int] = mapped_column(Integer, default=2)
    headline: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), default=list)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
