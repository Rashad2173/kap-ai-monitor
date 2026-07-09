from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import RagChunk
from app.schemas.api import AskResponse
from app.services.bank_registry import get_bank_name
from app.services.rag_service import search_rag
from app.services.report_metadata_service import get_latest_report_ids
from app.utils.llm_utils import safe_chat_completion
from app.utils.text_utils import normalize_text


# Yüksek sinyalli ifadeler: bunlardan biri varsa chunk gerçekten denetim/risk
# içeriğine işaret eder. Ağırlıkları büyük tutuyoruz ki sıralamada öne çıksınlar.
HIGH_SIGNAL_KEYWORDS: dict[str, int] = {
    "dikkat cekilen husus": 120,
    "dikkat cekilen hususlar": 120,
    "sartli sonucun dayanagi": 120,
    "sinirli olumlu sonucun dayanagi": 120,
    "kovusturmanin ertelenmesi": 100,
    "deferred prosecution agreement": 100,
    "serbest karsilik": 90,
    "guney new york": 80,
    "tms 37": 80,
    "sartli sonuc": 60,
    "sinirli olumlu sonuc": 60,
    "bagimsiz denetci gorusu": 50,
    "denetim sonucu": 40,
    "sinirli denetim": 40,
    "olumlu sonuc": 30,
}


# Bağlamsal kelimeler: tek başlarına çok yaygın oldukları için (her tabloda
# "karşılıklar", "risk" geçer) yalnızca yüksek sinyalli bir ifade de varsa
# katkı veriyorlar. Böylece sadece "karşılıklar" içeren gelir tablosu satırları
# yanlışlıkla denetim bağlamı sanılmıyor.
CONTEXT_KEYWORDS: dict[str, int] = {
    "amerika birlesik devletleri": 12,
    "yaptirim": 10,
    "iran": 10,
    "dava": 10,
    "hukuki": 10,
    "husus": 6,
    "suphe": 6,
    "karsiliklar": 5,
    "karsilik": 5,
    "risk": 5,
}


def keyword_score(text: str) -> int:
    norm = normalize_text(text)
    score = 0
    high_present = False

    for keyword, weight in HIGH_SIGNAL_KEYWORDS.items():
        if keyword in norm:
            score += weight
            high_present = True

    if high_present:
        for keyword, weight in CONTEXT_KEYWORDS.items():
            if keyword in norm:
                score += weight

    return score


def _dedupe_key(source: dict[str, Any]) -> tuple[Any, Any, str]:
    text_norm = normalize_text(source.get("text", ""))[:80]
    return (source.get("ticker"), source.get("page"), text_norm)


def get_keyword_risk_chunks(
    session: Session,
    bank: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    query = session.query(RagChunk)

    if bank != "ALL":
        query = query.filter(RagChunk.ticker == bank)

    # Ticker başına en güncel rapora ait chunk'larla sınırla; aksi halde eski
    # dönemin denetim/risk metni yanlışlıkla güncel gibi sunulabilir.
    latest_report_ids = get_latest_report_ids(
        session,
        tickers=[bank] if bank != "ALL" else None,
    )

    if latest_report_ids:
        query = query.filter(RagChunk.report_id.in_(latest_report_ids.values()))

    chunks = query.limit(1500).all()

    scored: list[tuple[int, RagChunk]] = []

    for chunk in chunks:
        # Denetim/risk içeriği metinseldir; tablo satırlarını eleyerek
        # gelir/bilanço satırlarının risk bağlamı gibi sunulmasını engelliyoruz.
        if chunk.chunk_type == "table_row":
            continue

        text = chunk.text or ""
        score = keyword_score(text)

        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)

    sources: list[dict[str, Any]] = []

    for score, chunk in scored[:limit]:
        sources.append(
            {
                "type": "risk_audit_chunk",
                "ticker": chunk.ticker,
                "period": chunk.period,
                "chunk_type": chunk.chunk_type,
                "table_type": chunk.table_type,
                "metric": chunk.metric,
                "page": chunk.page,
                "score": score,
                "preview": chunk.text[:500] if chunk.text else "",
                "text": chunk.text or "",
            }
        )

    return sources


def get_risk_audit_sources(
    session: Session,
    bank: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Keyword skorlaması + semantik RAG aramasını birleştirir.

    Önce yüksek-precision keyword chunk'larını alır, ardından semantik RAG
    sonuçlarını ekleyerek recall'u artırır. Tekrar eden parçalar elenir.
    """
    keyword_sources = get_keyword_risk_chunks(session, bank, limit=limit)

    seen = {_dedupe_key(source) for source in keyword_sources}
    merged = list(keyword_sources)

    rag_sources = search_rag(
        question=(
            "denetim sonucu, sınırlı denetim, dikkat çekilen hususlar, "
            "şartlı görüş, önemli riskler, hukuki davalar"
        ),
        bank=bank,
        n_results=limit,
    )

    for source in rag_sources:
        normalized = {
            "type": "rag_chunk",
            "ticker": source.get("ticker"),
            "period": source.get("period"),
            "chunk_type": source.get("chunk_type"),
            "table_type": source.get("table_type"),
            "metric": source.get("metric"),
            "page": source.get("page"),
            "score": 0,
            "preview": str(source.get("text", ""))[:500],
            "text": source.get("text", ""),
        }

        key = _dedupe_key(normalized)

        if key in seen:
            continue

        seen.add(key)
        merged.append(normalized)

    return merged[:limit]


def llm_build_risk_audit_answer(
    question: str,
    bank: str,
    sources: list[dict[str, Any]],
) -> AskResponse:
    if not sources:
        return AskResponse(
            answer="Bu soru için denetim, risk veya dikkat çekilen husus kaynağı bulunamadı.",
            mode="risk_audit_no_source",
            sources=[],
        )

    bank_name = get_bank_name(bank)

    def raw_answer() -> AskResponse:
        lines = [f"{bank_name} için denetim/risk kaynakları:"]

        for source in sources[:3]:
            lines.append("")
            lines.append(
                f"- Sayfa {source.get('page')}: "
                f"{str(source.get('text', ''))[:700]}"
            )

        return AskResponse(
            answer="\n".join(lines),
            mode="risk_audit_raw",
            sources=sources,
        )

    if not settings.OPENAI_API_KEY:
        return raw_answer()

    context = []

    for idx, source in enumerate(sources[:8], start=1):
        context.append(
            {
                "source_no": idx,
                "ticker": source.get("ticker"),
                "period": source.get("period"),
                "chunk_type": source.get("chunk_type"),
                "table_type": source.get("table_type"),
                "metric": source.get("metric"),
                "page": source.get("page"),
                "score": source.get("score"),
                "text": str(source.get("text", ""))[:1600],
            }
        )

    system_prompt = """
Sen KAP banka finansal raporlarında denetim, risk ve dikkat çekilen husus analizi yapan asistansın.

Kurallar:
- Sadece verilen kaynak parçalarına dayan.
- Gelir tablosu satırlarını "dikkat çekilen husus" gibi sunma.
- Kullanıcı "dikkat çekilen husus" soruyorsa özellikle "Dikkat Çekilen Hususlar" başlığını veya hukuki/denetim açıklamalarını ara.
- Kullanıcı "şartlı denetim", "şartlı sonuç" veya "sınırlı olumlu sonuç" soruyorsa "Şartlı Sonucun Dayanağı" veya "Sınırlı Olumlu Sonucun Dayanağı" metnine dayan.
- Kaynakta açıkça yoksa "kaynaklarda açıkça bulunamadı" de.
- Uydurma dava, sebep, yönetim açıklaması veya risk çıkarımı yapma.
- Cevabı Türkçe, net ve kaynak odaklı ver.
- Sayfa bilgisi varsa belirt.
""".strip()

    payload = {
        "bank": bank,
        "bank_name": bank_name,
        "question": question,
        "sources": context,
    }

    answer = safe_chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )

    # API başarısızsa ham kaynak özetine düş; kullanıcıyı boş cevapla bırakma.
    if not answer:
        return raw_answer()

    return AskResponse(
        answer=answer,
        mode="risk_audit_gpt",
        sources=sources,
    )


def answer_risk_audit_question(
    session: Session,
    question: str,
    bank: str,
) -> AskResponse:
    sources = get_risk_audit_sources(
        session=session,
        bank=bank,
        limit=8,
    )

    return llm_build_risk_audit_answer(
        question=question,
        bank=bank,
        sources=sources,
    )


def answer_multi_bank_risk_audit_question(
    session: Session,
    question: str,
    banks: list[str],
) -> AskResponse:
    all_sources: list[dict[str, Any]] = []

    for bank in banks:
        sources = get_risk_audit_sources(
            session=session,
            bank=bank,
            limit=5,
        )

        for source in sources:
            source["source_bank"] = bank
            source["source_bank_name"] = get_bank_name(bank)

        all_sources.extend(sources)

    if not all_sources:
        return AskResponse(
            answer="Bu bankalar için denetim, risk veya dikkat çekilen husus kaynağı bulunamadı.",
            mode="multi_bank_risk_audit_no_source",
            sources=[],
        )

    def raw_answer() -> AskResponse:
        lines = ["Seçili bankalar için denetim/risk kaynakları:"]

        for source in all_sources[:8]:
            lines.append("")
            lines.append(
                f"- {source.get('source_bank_name')} | Sayfa {source.get('page')}: "
                f"{str(source.get('text', ''))[:700]}"
            )

        return AskResponse(
            answer="\n".join(lines),
            mode="multi_bank_risk_audit_raw",
            sources=all_sources,
        )

    if not settings.OPENAI_API_KEY:
        return raw_answer()

    context = []

    for idx, source in enumerate(all_sources[:12], start=1):
        context.append(
            {
                "source_no": idx,
                "bank": source.get("source_bank"),
                "bank_name": source.get("source_bank_name"),
                "ticker": source.get("ticker"),
                "period": source.get("period"),
                "chunk_type": source.get("chunk_type"),
                "table_type": source.get("table_type"),
                "metric": source.get("metric"),
                "page": source.get("page"),
                "score": source.get("score"),
                "text": str(source.get("text", ""))[:1600],
            }
        )

    system_prompt = """
Sen KAP banka finansal raporlarında denetim, risk ve dikkat çekilen husus analizi yapan asistansın.

Görevin:
- Kullanıcının sorduğu bankaları ayrı ayrı değerlendir.
- Sadece verilen kaynak parçalarına dayan.
- Her banka için denetim türünü, denetim sonucunu ve varsa özel dikkat çekilen husus / şartlı sonuç dayanağını belirt.
- Bir bankanın kaynağı yoksa "bu banka için kaynak bulunamadı" de.
- Kaynakta olmayan bilgi uydurma.
- Gelir tablosu satırlarını denetim, risk veya dikkat çekilen husus gibi sunma.
- Kullanıcı iki veya daha fazla bankayı karşılaştırıyorsa, en sonda aralarındaki farkı açıkça yaz.
- Cevabı Türkçe, net ve kaynak odaklı ver.
- Sayfa bilgisi varsa belirt.

Dikkat çekilen husus kuralları:
- Sadece kaynakta açıkça "Dikkat Çekilen Husus", "Dikkat Çekilen Hususlar", "Şartlı Sonucun Dayanağı", "Sınırlı Olumlu Sonucun Dayanağı" veya benzeri özel bir başlık varsa bunu dikkat çekilen husus olarak yaz.
- Denetçinin olumlu sonuç paragrafını dikkat çekilen husus gibi sunma.
- "Herhangi bir husus dikkatimizi çekmemiştir" tarzındaki olumlu sonuç cümleleri özel dikkat çekilen husus değildir.
- Eğer özel bir dikkat çekilen husus yoksa açıkça "Dikkat çekilen özel bir husus belirtilmemiştir" de.
- Eğer denetim sonucu şartlı veya sınırlı olumlu ise, bunun dayanağını kaynakta geçtiği şekliyle açıkla.
- Eğer denetim sonucu olumlu ise ve özel dikkat çekilen husus yoksa bunu ayrı şekilde belirt.
- Aynı bankaya ait birden fazla kaynak varsa, denetim raporuna ait en ilgili kaynağı esas al.

Cevap formatı:
Her banka için şu bilgileri ver:
- Denetim Türü
- Denetim Sonucu
- Dikkat Çekilen Husus / Şartlı Sonucun Dayanağı
- Kaynak Sayfa

Son bölümde:
- Bankalar arasında denetim sonucu açısından fark var mı, kısa şekilde açıkla.
""".strip()

    payload = {
        "question": question,
        "banks": [
            {
                "ticker": bank,
                "bank_name": get_bank_name(bank),
            }
            for bank in banks
        ],
        "sources": context,
    }

    answer = safe_chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )

    if not answer:
        return raw_answer()

    return AskResponse(
        answer=answer,
        mode="multi_bank_risk_audit_gpt",
        sources=all_sources,
    )