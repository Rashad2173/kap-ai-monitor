from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import RagChunk
from app.schemas.api import AskResponse
from app.services.bank_registry import get_bank_name
from app.services.report_metadata_service import get_latest_report_ids
from app.utils.text_utils import normalize_text


def location_source(chunk: RagChunk, reason: str) -> dict[str, Any]:
    return {
        "type": "document_location_chunk",
        "ticker": chunk.ticker,
        "period": chunk.period,
        "chunk_type": chunk.chunk_type,
        "table_type": chunk.table_type,
        "metric": chunk.metric,
        "page": chunk.page,
        "reason": reason,
        "preview": chunk.text[:500] if chunk.text else "",
        "text": chunk.text or "",
    }


def is_accounting_policy_question(question: str) -> bool:
    q = normalize_text(question)

    return (
        "muhasebe politika" in q
        or "onemli muhasebe" in q
        or "accounting policies" in q
    )


def query_terms(question: str) -> list[str]:
    q = normalize_text(question)

    if is_accounting_policy_question(question):
        return [
            "onemli muhasebe politikalarinin ozeti",
            "onemli muhasebe politikalari",
            "muhasebe politikalarinin ozeti",
            "muhasebe politikalari",
            "significant accounting policies",
            "accounting policies",
            "finansal tablolarin hazirlanmasina iliskin esaslar",
            "uygulanan muhasebe ilkeleri",
            "bddk muhasebe uygulamalari",
            "bddk muhasebe ve finansal raporlama mevzuati",
            "aciklama ve dipnotlar",
            "aciklayici dipnotlar",
            "tms",
            "tfrs",
        ]

    if "dipnot" in q:
        return ["aciklayici dipnot", "dipnot"]

    return [
        term
        for term in q.split()
        if len(term) > 4
        and term
        not in {
            "hangi",
            "bolum",
            "sayfa",
            "nerede",
            "raporunda",
            "aciklama",
            "aliyor",
        }
    ]


def chunk_location_label(chunk: RagChunk) -> str:
    text_norm = normalize_text(chunk.text)

    if (
        "onemli muhasebe politikalarinin ozeti" in text_norm
        or "muhasebe politikalarinin ozeti" in text_norm
        or "onemli muhasebe politikalari" in text_norm
    ):
        return "Önemli muhasebe politikalarının özeti ve açıklayıcı dipnotlar"

    if "finansal tablolarin hazirlanmasina iliskin esaslar" in text_norm:
        return "Finansal tabloların hazırlanmasına ilişkin esaslar"

    if "bddk muhasebe ve finansal raporlama mevzuati" in text_norm:
        return "BDDK muhasebe ve finansal raporlama mevzuatı"

    if "bagimsiz denetci gorusu" in text_norm:
        return "Bağımsız denetçi raporundaki atıf"

    if chunk.table_type:
        return chunk.table_type

    return "ilgili rapor bölümü"


def score_chunk(
    chunk: RagChunk,
    terms: list[str],
    accounting_policy: bool = False,
) -> int:
    text_norm = normalize_text(chunk.text)
    score = 0

    for term in terms:
        term_norm = normalize_text(term)

        if term_norm and term_norm in text_norm:
            score += 100 + len(term_norm)

    if accounting_policy:
        if "onemli muhasebe politikalarinin ozeti" in text_norm:
            score += 600
        if "muhasebe politikalarinin ozeti" in text_norm:
            score += 450
        if "aciklayici dipnot" in text_norm or "aciklama ve dipnot" in text_norm:
            score += 200
        if "bddk muhasebe ve finansal raporlama mevzuati" in text_norm:
            score += 120
        if "finansal tablolarin hazirlanmasina iliskin esaslar" in text_norm:
            score += 250
        if "muhasebe politikasinda yapilan degisiklik" in text_norm:
            score -= 250
        if chunk.chunk_type == "table_row":
            score -= 120
        if "bagimsiz denetci gorusu" in text_norm or "giris" in text_norm:
            score -= 80

    if chunk.page not in (None, -1):
        score += 5

    return score


def find_document_location_chunks(
    session: Session,
    bank: str,
    question: str,
    limit: int = 3,
) -> list[tuple[int, RagChunk]]:
    terms = query_terms(question)
    accounting_policy = is_accounting_policy_question(question)
    query = session.query(RagChunk)

    if bank != "ALL":
        query = query.filter(RagChunk.ticker == bank)

    # Ticker başına en güncel rapora ait chunk'larla sınırla; aksi halde eski
    # dönemin metni yanlışlıkla güncel konum bilgisi gibi seçilebilir.
    latest_report_ids = get_latest_report_ids(
        session,
        tickers=[bank] if bank != "ALL" else None,
    )

    if latest_report_ids:
        query = query.filter(RagChunk.report_id.in_(latest_report_ids.values()))

    chunks = query.limit(5000).all()
    scored: list[tuple[int, RagChunk]] = []

    for chunk in chunks:
        score = score_chunk(chunk, terms, accounting_policy)

        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda item: (item[0], -(item[1].page or 9999)), reverse=True)

    return scored[:limit]


def answer_document_location_question(
    session: Session,
    question: str,
    bank: str,
) -> AskResponse:
    matches = find_document_location_chunks(
        session=session,
        bank=bank,
        question=question,
        limit=3,
    )

    if not matches:
        return AskResponse(
            answer=(
                "Bu açıklamanın hangi bölümde yer aldığına dair güvenilir kaynak "
                "parçası bulunamadı."
            ),
            mode="document_location_no_source",
            sources=[],
        )

    _, best = matches[0]
    bank_name = get_bank_name(best.ticker)
    page_text = f"sayfa {best.page}" if best.page not in (None, -1) else "sayfa bilgisi yok"
    section = chunk_location_label(best)
    text_norm = normalize_text(best.text)

    if is_accounting_policy_question(question) and "bagimsiz denetci gorusu" in text_norm:
        answer = (
            f"{bank_name} için **önemli muhasebe politikalarının özeti ve açıklayıcı "
            f"dipnotlar** ifadesine en güvenilir atıf **{page_text}** içinde bulunuyor. "
            "Mevcut indekslenen metinlerde ayrıntılı dipnot bölümünün kendi başlığı "
            "ayrı bir chunk olarak bulunamadığı için yüksek seviye 'Giriş' bölümü "
            "kesin konum olarak raporlanmadı."
        )
    else:
        answer = (
            f"{bank_name} için ilgili açıklama **{section}** bölümünde, "
            f"**{page_text}** içinde geçiyor."
        )

    return AskResponse(
        answer=answer,
        mode="document_location",
        sources=[
            location_source(
                chunk,
                "Belge-konum sorusu için anahtar ifade ve bölüm skoru ile seçildi.",
            )
            for _, chunk in matches
        ],
    )
