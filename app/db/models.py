from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.connection import Base


class Bank(Base):
    __tablename__ = "banks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    ticker: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        index=True,
        nullable=False,
    )

    company_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    reports: Mapped[list["Report"]] = relationship(
        back_populates="bank",
        cascade="all, delete-orphan",
    )


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    bank_id: Mapped[int] = mapped_column(
        ForeignKey("banks.id"),
        nullable=False,
    )

    ticker: Mapped[str] = mapped_column(
        String(20),
        index=True,
        nullable=False,
    )

    disclosure_id: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False,
    )

    publish_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    publish_time: Mapped[time | None] = mapped_column(
        Time,
        nullable=True,
    )

    year: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
    )

    period_label: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    period_code: Mapped[str | None] = mapped_column(
        String(30),
        index=True,
        nullable=True,
    )

    subject: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    disclosure_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    detail_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    pdf_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    raw_json: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    bank: Mapped["Bank"] = relationship(back_populates="reports")

    files: Mapped[list["ReportFile"]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
    )

    metrics: Mapped[list["FinancialMetric"]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
    )

    chunks: Mapped[list["RagChunk"]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
    )


class ReportFile(Base):
    __tablename__ = "report_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    report_id: Mapped[int] = mapped_column(
        ForeignKey("reports.id"),
        nullable=False,
    )

    file_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    local_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    file_hash: Mapped[str | None] = mapped_column(
        String(128),
        index=True,
        nullable=True,
    )

    page_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    report: Mapped["Report"] = relationship(back_populates="files")

    __table_args__ = (
        UniqueConstraint(
            "report_id",
            "file_type",
            "local_path",
            name="uq_report_file",
        ),
    )


class FinancialMetric(Base):
    __tablename__ = "financial_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    report_id: Mapped[int] = mapped_column(
        ForeignKey("reports.id"),
        nullable=False,
    )

    ticker: Mapped[str] = mapped_column(
        String(20),
        index=True,
        nullable=False,
    )

    period: Mapped[str | None] = mapped_column(
        String(50),
        index=True,
        nullable=True,
    )

    table_type: Mapped[str | None] = mapped_column(
        String(100),
        index=True,
        nullable=True,
    )

    metric: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    parent_section: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    unit: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    page: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    all_zero: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    values_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )

    raw_json: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    report: Mapped["Report"] = relationship(back_populates="metrics")


class RagChunk(Base):
    __tablename__ = "rag_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    report_id: Mapped[int] = mapped_column(
        ForeignKey("reports.id"),
        nullable=False,
    )

    ticker: Mapped[str] = mapped_column(
        String(20),
        index=True,
        nullable=False,
    )

    period: Mapped[str | None] = mapped_column(
        String(50),
        index=True,
        nullable=True,
    )

    chunk_type: Mapped[str | None] = mapped_column(
        String(50),
        index=True,
        nullable=True,
    )

    table_type: Mapped[str | None] = mapped_column(
        String(100),
        index=True,
        nullable=True,
    )

    metric: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    parent_section: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    page: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    metadata_json: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    embedding_status: Mapped[str] = mapped_column(
        String(30),
        default="pending",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    report: Mapped["Report"] = relationship(back_populates="chunks")