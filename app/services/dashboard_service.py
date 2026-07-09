from __future__ import annotations

from app.db.connection import SessionLocal
from app.db.models import RagChunk
from app.schemas.api import DashboardSummaryResponse
from app.services.bank_registry import clean_bank, get_bank_name
from app.services.numeric_service import (
    collect_numeric_candidates,
    current_numeric_value,
    llm_select_numeric_candidates,
)
from app.utils.text_utils import format_value, get_current_total_value, normalize_text


def _select_best_metric(bank: str, intent: str):
    session = SessionLocal()

    try:
        candidates = collect_numeric_candidates(
            session=session,
            bank=bank,
            intent=intent,
            max_per_bank=10,
        )

        if not candidates:
            return None

        candidates.sort(key=current_numeric_value, reverse=True)
        return candidates[0]

    finally:
        session.close()


def _select_all_bank_metric_rows(intent: str, question: str):
    session = SessionLocal()

    try:
        candidates = collect_numeric_candidates(
            session=session,
            bank="ALL",
            intent=intent,
            max_per_bank=10,
        )

        if not candidates:
            return []

        selection = llm_select_numeric_candidates(
            question=question,
            bank="ALL",
            intent=intent,
            candidates=candidates,
        )

        selected_ids = set()

        for item in selection.get("selections", []):
            candidate_id = item.get("candidate_id")

            if candidate_id is None:
                continue

            try:
                selected_ids.add(int(candidate_id))
            except (TypeError, ValueError):
                continue

        if selected_ids:
            rows = [
                row
                for row in candidates
                if row.id in selected_ids
            ]
        else:
            best_by_ticker = {}

            for row in candidates:
                if row.ticker not in best_by_ticker:
                    best_by_ticker[row.ticker] = row

            rows = list(best_by_ticker.values())

        rows.sort(key=current_numeric_value, reverse=True)
        return rows

    finally:
        session.close()


def _get_audit_text_for_bank(bank: str) -> str | None:
    session = SessionLocal()

    try:
        query = session.query(RagChunk)

        if bank != "ALL":
            query = query.filter(RagChunk.ticker == bank)

        chunks = query.filter(
            RagChunk.chunk_type == "text"
        ).limit(100).all()

        for chunk in chunks:
            text_norm = normalize_text(chunk.text)

            if (
                "denetim sonucu" in text_norm
                or "dikkat cekilen" in text_norm
                or "sartli" in text_norm
                or "olumlu" in text_norm
            ):
                return chunk.text[:700]

        return None

    finally:
        session.close()


def get_dashboard_summary(bank: str = "ALL") -> DashboardSummaryResponse:
    bank = clean_bank(bank)

    if bank == "ALL":
        profit_rows = _select_all_bank_metric_rows(
            intent="net_kar",
            question="Tüm bankaların 2026 Q1 dönem net kârı ne kadar?",
        )

        if profit_rows:
            top = profit_rows[0]
            _, top_value = get_current_total_value(top.values_json)

            profit_title = f"{top.ticker}: {format_value(top_value, top.unit)}"
            profit_desc = "Son dönem net kâr kaleminde en yüksek bulunan banka."
        else:
            profit_title = "Veri bulunamadı"
            profit_desc = "Net kâr metriği bulunamadı."

        summary_lines = []

        for row in profit_rows[:8]:
            _, value = get_current_total_value(row.values_json)
            summary_lines.append(f"{row.ticker}: {format_value(value, row.unit)}")

        summary = (
            "Tüm bankalar için son dönem net kâr özetleri:\n"
            + "\n".join(summary_lines)
            if summary_lines
            else "Tüm bankalar için net kâr özeti üretilemedi."
        )

        return DashboardSummaryResponse(
            profit={
                "title": profit_title,
                "description": profit_desc,
            },
            highlight={
                "title": f"{len(profit_rows)} banka analiz edildi",
                "description": "Sayısal metriklerde GPT aday satırlar arasından seçim yapar.",
            },
            audit={
                "title": "Denetim bilgileri hazır",
                "description": "Denetçi görüşü ve dikkat çekilen hususlar RAG tarafında aranabilir.",
            },
            summary=summary,
        )

    bank_name = get_bank_name(bank)

    profit = _select_best_metric(bank, "net_kar")
    loans = _select_best_metric(bank, "krediler")
    assets = _select_best_metric(bank, "varliklar_toplami")
    audit_text = _get_audit_text_for_bank(bank)

    if profit:
        _, profit_value = get_current_total_value(profit.values_json)
        profit_title = format_value(profit_value, profit.unit)
        profit_desc = f"{profit.metric}, {profit.period}, sayfa {profit.page}"
    else:
        profit_title = "Bulunamadı"
        profit_desc = "Net kâr metriği yakalanamadı."

    if loans:
        _, loan_value = get_current_total_value(loans.values_json)
        highlight_title = f"Krediler: {format_value(loan_value, loans.unit)}"
        highlight_desc = f"{loans.period}, sayfa {loans.page}"
    elif assets:
        _, asset_value = get_current_total_value(assets.values_json)
        highlight_title = f"Aktif: {format_value(asset_value, assets.unit)}"
        highlight_desc = f"{assets.period}, sayfa {assets.page}"
    else:
        highlight_title = "Metrik bulunamadı"
        highlight_desc = "Kredi veya aktif toplamı yakalanamadı."

    if audit_text:
        audit_title = "Denetim metni bulundu"
        audit_desc = audit_text[:220] + "..."
    else:
        audit_title = "Denetim metni bulunamadı"
        audit_desc = "RAG chunklarında denetim metni yakalanamadı."

    summary = (
        f"{bank_name} için dashboard özeti:\n"
        f"- Son dönem net kâr: {profit_title}\n"
        f"- Öne çıkan metrik: {highlight_title}\n"
        f"- Denetim/Risk: {audit_title}\n\n"
        f"Detaylı analiz için '{bank_name} Q1 özetler misin?' diye sorabilirsin."
    )

    return DashboardSummaryResponse(
        profit={
            "title": profit_title,
            "description": profit_desc,
        },
        highlight={
            "title": highlight_title,
            "description": highlight_desc,
        },
        audit={
            "title": audit_title,
            "description": audit_desc,
        },
        summary=summary,
    )