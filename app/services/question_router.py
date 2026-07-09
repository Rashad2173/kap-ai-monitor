from __future__ import annotations

from app.db.connection import SessionLocal
from app.schemas.api import AskRequest, AskResponse
from app.services.bank_registry import (
    clean_bank,
    detect_banks_from_question,
)
from app.services.document_location_service import answer_document_location_question
from app.services.executive_summary_service import build_executive_summary_answer
from app.services.risk_audit_service import (
    answer_multi_bank_risk_audit_question,
    answer_risk_audit_question,
)
from app.services.numeric_service import answer_numeric_question
from app.services.question_classifier import (
    detect_metric_intent,
    detect_question_type,
)
from app.services.rag_service import answer_rag_question
from app.services.report_metadata_service import (
    answer_period_dates_question,
    answer_report_metadata_question,
)


def route_question(request: AskRequest) -> AskResponse:
    bank = clean_bank(request.bank)
    question = request.question.strip()

    if not question:
        return AskResponse(
            answer="Lütfen bir soru yaz.",
            mode="empty",
            sources=[],
        )

    mentioned_banks = detect_banks_from_question(question)

    # Soruda tek bir banka geçiyorsa, UI'da ne seçili olursa olsun o bankaya
    # odaklan. (Eski koddaki ALL / non-ALL dalları aynı işi yapıyordu.)
    if len(mentioned_banks) == 1:
        bank = mentioned_banks[0]

    # Birden fazla banka geçiyorsa karşılaştırma/çok bankalı handler'lara
    # iletmek üzere listeyi taşıyoruz.
    multi_banks = mentioned_banks if len(mentioned_banks) > 1 else None

    question_type = detect_question_type(question)

    session = SessionLocal()

    try:
        if question_type == "executive_summary":
            return build_executive_summary_answer(
                question=question,
                bank=bank,
                session=session,
            )

        if question_type == "numeric_metric":
            metric_intent = detect_metric_intent(question)

            if not metric_intent:
                return AskResponse(
                    answer="Sayısal soru algılandı ama metrik türü belirlenemedi.",
                    mode="numeric_unknown_metric",
                    sources=[],
                )

            return answer_numeric_question(
                session=session,
                question=question,
                bank=bank,
                intent=metric_intent,
                banks=multi_banks,
            )

        if question_type == "report_metadata":
            return answer_report_metadata_question(
                session=session,
                question=question,
                bank=bank,
            )

        if question_type == "period_dates":
            return answer_period_dates_question(
                session=session,
                bank=bank,
            )

        if question_type == "document_location":
            return answer_document_location_question(
                session=session,
                question=question,
                bank=bank,
            )

        if question_type == "risk_audit":
            if multi_banks:
                return answer_multi_bank_risk_audit_question(
                    session=session,
                    question=question,
                    banks=multi_banks,
                )

            return answer_risk_audit_question(
                session=session,
                question=question,
                bank=bank,
            )

        if question_type == "comparison":
            metric_intent = detect_metric_intent(question)

            if metric_intent:
                return answer_numeric_question(
                    session=session,
                    question=question,
                    bank=bank,
                    intent=metric_intent,
                    banks=multi_banks,
                )

            return answer_rag_question(
                question=question,
                bank=bank,
                n_results=8,
            )

        return answer_rag_question(
            question=question,
            bank=bank,
            n_results=6,
        )

    finally:
        session.close()