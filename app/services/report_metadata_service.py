from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import FinancialMetric, RagChunk, Report
from app.schemas.api import AskResponse
from app.services.bank_registry import get_bank_name
from app.utils.text_utils import (
    get_current_total_value,
    get_previous_total_value,
    normalize_text,
)


def report_source(report: Report, reason: str) -> dict[str, Any]:
    return {
        "type": "report",
        "ticker": report.ticker,
        "disclosure_id": report.disclosure_id,
        "publish_date": str(report.publish_date) if report.publish_date else None,
        "publish_time": str(report.publish_time) if report.publish_time else None,
        "period_code": report.period_code,
        "subject": report.subject,
        "disclosure_type": report.disclosure_type,
        "reason": reason,
    }


def is_consolidation_status_question(question: str) -> bool:
    q = normalize_text(question)
    terms = [
        "konsolide",
        "solo",
        "konsolide olmayan",
        "finansal tablo tipi",
        "finansal tablo niteligi",
        "consolidated",
        "unconsolidated",
    ]

    return any(term in q for term in terms)


def latest_report(session: Session, bank: str) -> Report | None:
    query = session.query(Report)

    if bank != "ALL":
        query = query.filter(Report.ticker == bank)

    return (
        query.order_by(
            Report.publish_date.desc().nullslast(),
            Report.publish_time.desc().nullslast(),
            Report.id.desc(),
        )
        .limit(1)
        .one_or_none()
    )


def get_latest_report_ids(
    session: Session,
    tickers: list[str] | None = None,
) -> dict[str, int]:
    """Ticker başına en güncel raporun id'sini döndürür (sıralama latest_report() ile aynı)."""
    if tickers is None:
        tickers = [row[0] for row in session.query(Report.ticker).distinct().all()]

    result: dict[str, int] = {}

    for ticker in tickers:
        report = (
            session.query(Report)
            .filter(Report.ticker == ticker)
            .order_by(
                Report.publish_date.desc().nullslast(),
                Report.publish_time.desc().nullslast(),
                Report.id.desc(),
            )
            .limit(1)
            .one_or_none()
        )

        if report:
            result[ticker] = report.id

    return result


def get_display_period_label(session: Session, bank: str) -> str:
    """En güncel raporun period_code'unu gösterime uygun etikete çevirir, örn. "2026_Q1" -> "2026 Q1"."""
    report = latest_report(session, bank)

    if report and report.period_code:
        return report.period_code.replace("_", " ")

    return "güncel dönem"


def extract_period(column_name: str | None) -> str:
    if not column_name:
        return ""

    match = re.search(r"\(([^)]+)\)", column_name)

    if match:
        return match.group(1)

    return column_name


def normalize_statement_type(value: str | None) -> str | None:
    value_norm = normalize_text(value)

    if not value_norm:
        return None

    if "konsolide olmayan" in value_norm or "unconsolidated" in value_norm:
        return "Konsolide Olmayan (solo)"

    if "konsolide" in value_norm or "consolidated" in value_norm:
        return "Konsolide"

    if "solo" in value_norm:
        return "Konsolide Olmayan (solo)"

    return None


def find_consolidation_status(
    session: Session,
    report: Report,
) -> tuple[str | None, dict[str, Any] | None]:
    metrics = (
        session.query(FinancialMetric)
        .filter(FinancialMetric.report_id == report.id)
        .limit(200)
        .all()
    )

    for metric in metrics:
        raw = metric.raw_json or {}
        status = normalize_statement_type(raw.get("stmt_type"))

        if status:
            return status, {
                "type": "financial_metric",
                "ticker": metric.ticker,
                "period": metric.period,
                "table_type": metric.table_type,
                "metric": metric.metric,
                "page": metric.page,
                "unit": metric.unit,
                "statement_type": raw.get("stmt_type"),
                "reason": "financial_metrics.raw_json.stmt_type alanından tespit edildi.",
            }

    chunks = (
        session.query(RagChunk)
        .filter(RagChunk.report_id == report.id)
        .limit(2000)
        .all()
    )

    for chunk in chunks:
        metadata = chunk.metadata_json or {}
        status = normalize_statement_type(metadata.get("stmt_type"))

        if status:
            return status, {
                "type": "document_location_chunk",
                "ticker": chunk.ticker,
                "period": chunk.period,
                "chunk_type": chunk.chunk_type,
                "table_type": chunk.table_type,
                "metric": chunk.metric,
                "page": chunk.page,
                "statement_type": metadata.get("stmt_type"),
                "reason": "rag_chunks.metadata_json.stmt_type alanından tespit edildi.",
                "preview": chunk.text[:500] if chunk.text else "",
            }

    for chunk in chunks:
        text_norm = normalize_text(chunk.text)
        status = None

        if "finansal tablo niteligi konsolide olmayan" in text_norm:
            status = "Konsolide Olmayan (solo)"
        elif "finansal tablo niteligi konsolide" in text_norm:
            status = "Konsolide"
        elif "ara donem konsolide olmayan finansal" in text_norm:
            status = "Konsolide Olmayan (solo)"
        elif "ara donem konsolide finansal" in text_norm:
            status = "Konsolide"

        if status:
            return status, {
                "type": "document_location_chunk",
                "ticker": chunk.ticker,
                "period": chunk.period,
                "chunk_type": chunk.chunk_type,
                "table_type": chunk.table_type,
                "metric": chunk.metric,
                "page": chunk.page,
                "reason": "Rapor/chunk metnindeki finansal tablo niteliği ifadesinden tespit edildi.",
                "preview": chunk.text[:500] if chunk.text else "",
            }

    return None, None


def find_balance_period_row(session: Session, bank: str) -> FinancialMetric | None:
    rows = (
        session.query(FinancialMetric)
        .filter(
            FinancialMetric.ticker == bank,
            FinancialMetric.table_type == "bilanco",
        )
        .all()
    )

    for row in rows:
        if normalize_text(row.metric) == "varliklar toplami":
            return row

    return None


def find_income_period_row(session: Session, bank: str) -> FinancialMetric | None:
    rows = (
        session.query(FinancialMetric)
        .filter(
            FinancialMetric.ticker == bank,
            FinancialMetric.table_type == "gelir_tablosu",
        )
        .all()
    )

    candidates = [
        row
        for row in rows
        if normalize_text(row.metric).startswith("donem net kari")
        or normalize_text(row.metric).startswith("donem net kar")
    ]

    if not candidates:
        return None

    def sort_key(row: FinancialMetric) -> tuple[int, int]:
        current_col, _ = get_current_total_value(row.values_json)
        has_range = 1 if current_col and "-" in current_col else 0

        return has_range, row.page or -1

    candidates.sort(key=sort_key, reverse=True)

    return candidates[0]


def answer_report_metadata_question(
    session: Session,
    question: str,
    bank: str,
) -> AskResponse:
    report = latest_report(session, bank)

    if not report:
        return AskResponse(
            answer="Bu banka için rapor metadata kaydı bulunamadı.",
            mode="report_metadata",
            sources=[],
        )

    q = normalize_text(question)
    bank_name = get_bank_name(report.ticker)

    if is_consolidation_status_question(question):
        status, source = find_consolidation_status(session, report)

        if not status:
            return AskResponse(
                answer=(
                    f"{bank_name} {report.period_code or ''} raporu için konsolide/solo "
                    "bilgisi güvenilir şekilde tespit edilemedi."
                ),
                mode="consolidation_status",
                sources=[report_source(report, "Konsolide/solo bilgisi için metadata kaydı kontrol edildi.")],
            )

        source_list = [source] if source else []
        source_list.append(
            report_source(report, "Konsolide/solo bilgisi için ilgili rapor metadata kaydı.")
        )

        return AskResponse(
            answer=(
                f"{bank_name} {report.period_code or ''} finansal verisi "
                f"**{status}** finansal tablodan alınmıştır."
            ),
            mode="consolidation_status",
            sources=source_list,
        )

    if "bildirim turu" in q:
        answer = (
            f"{bank_name} {report.period_code or ''} finansal sonucu KAP'ta "
            f"**{report.subject or '-'}** bildirim türü altında yayımlanmış."
        )

        if report.disclosure_type:
            answer += f" Kısa tür kodu: **{report.disclosure_type}**."

        return AskResponse(
            answer=answer,
            mode="report_metadata",
            sources=[report_source(report, "KAP bildirim türü metadata kaydı.")],
        )

    publish_date = str(report.publish_date) if report.publish_date else "-"
    publish_time = str(report.publish_time) if report.publish_time else "-"

    return AskResponse(
        answer=(
            f"{bank_name} {report.period_code or ''} finansal raporu KAP'ta "
            f"**{publish_date} {publish_time}** tarihinde yayımlanmış."
        ),
        mode="report_metadata",
        sources=[report_source(report, "KAP yayın tarihi metadata kaydı.")],
    )


def answer_period_dates_question(
    session: Session,
    bank: str,
) -> AskResponse:
    if bank == "ALL":
        return AskResponse(
            answer="Cari/önceki dönem tarihleri için önce tek bir banka seçilmeli.",
            mode="period_dates",
            sources=[],
        )

    balance_row = find_balance_period_row(session, bank)
    income_row = find_income_period_row(session, bank)
    lines: list[str] = []
    sources: list[dict[str, Any]] = []
    bank_name = get_bank_name(bank)

    if balance_row:
        current_col, _ = get_current_total_value(balance_row.values_json)
        previous_col, _ = get_previous_total_value(balance_row.values_json)
        lines.append(
            f"{bank_name} bilançosunda cari dönem **{extract_period(current_col)}**, "
            f"önceki dönem **{extract_period(previous_col)}**."
        )
        sources.append(
            {
                "type": "financial_metric",
                "ticker": balance_row.ticker,
                "period": balance_row.period,
                "table_type": balance_row.table_type,
                "metric": balance_row.metric,
                "page": balance_row.page,
                "current_column": current_col,
                "previous_column": previous_col,
                "reason": "Bilanço dönem kolonları için seçildi.",
            }
        )

    if income_row:
        current_col, _ = get_current_total_value(income_row.values_json)
        previous_col, _ = get_previous_total_value(income_row.values_json)
        lines.append(
            f"Gelir tablosunda cari dönem **{extract_period(current_col)}**, "
            f"önceki dönem **{extract_period(previous_col)}**."
        )
        sources.append(
            {
                "type": "financial_metric",
                "ticker": income_row.ticker,
                "period": income_row.period,
                "table_type": income_row.table_type,
                "metric": income_row.metric,
                "page": income_row.page,
                "current_column": current_col,
                "previous_column": previous_col,
                "reason": "Gelir tablosu dönem kolonları için seçildi.",
            }
        )

    if not lines:
        return AskResponse(
            answer="Cari/önceki dönem tarihlerini gösterecek güvenilir kolon bulunamadı.",
            mode="period_dates",
            sources=[],
        )

    return AskResponse(
        answer="\n".join(lines),
        mode="period_dates",
        sources=sources,
    )
