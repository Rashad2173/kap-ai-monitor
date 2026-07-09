from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.integrations.chroma_client import search_chroma
from app.schemas.api import AskResponse
from app.services.bank_registry import get_bank_name
from app.utils.llm_utils import safe_chat_completion


def search_rag(
    question: str,
    bank: str,
    n_results: int = 6,
) -> list[dict[str, Any]]:
    return search_chroma(
        question=question,
        bank=bank,
        n_results=n_results,
    )


def build_raw_rag_answer(bank: str, sources: list[dict[str, Any]]) -> AskResponse:
    if not sources:
        return AskResponse(
            answer="Bu soru için RAG tarafında ilgili kaynak bulunamadı.",
            mode="rag",
            sources=[],
        )

    bank_name = get_bank_name(bank)
    lines: list[str] = []

    if bank == "ALL":
        lines.append("Tüm bankalar içinde ilgili rapor parçalarından bulunan sonuçlar:")
    else:
        lines.append(f"{bank_name} için ilgili rapor parçalarından bulunan sonuç:")

    lines.append("")

    for idx, source in enumerate(sources[:4], start=1):
        header = f"{idx}. Kaynak: {source.get('ticker', '')}"

        if source.get("page") not in ("", None, -1):
            header += f", sayfa {source.get('page')}"

        if source.get("table_type"):
            header += f", bölüm: {source.get('table_type')}"

        if source.get("metric"):
            header += f", kalem: {source.get('metric')}"

        lines.append(header)
        lines.append(str(source.get("text", ""))[:900])
        lines.append("")

    return AskResponse(
        answer="\n".join(lines),
        mode="rag",
        sources=sources,
    )


def llm_build_rag_answer(
    question: str,
    bank: str,
    sources: list[dict[str, Any]],
) -> AskResponse:
    if not sources:
        return AskResponse(
            answer="Bu soru için RAG tarafında ilgili kaynak bulunamadı.",
            mode="rag_gpt",
            sources=[],
        )

    context = []

    for idx, source in enumerate(sources[:6], start=1):
        context.append(
            {
                "source_no": idx,
                "ticker": source.get("ticker"),
                "period": source.get("period"),
                "chunk_type": source.get("chunk_type"),
                "table_type": source.get("table_type"),
                "metric": source.get("metric"),
                "page": source.get("page"),
                "text": source.get("text", ""),
            }
        )

    system_prompt = """
Sen KAP banka finansal raporları için çalışan kaynak-denetimli bir analiz asistanısın.

Kurallar:
- Sadece verilen kaynak parçalarına dayanarak cevap ver.
- Kaynakta açıkça bulunmayan tarih, sayfa, bölüm, tutar, oran veya neden üretme.
- Kullanıcının sorduğu ana bilgi kaynaklarda açıkça yoksa "Verilen kaynaklarda açıkça bulunamadı" de.
- Sayfa, tablo ve kalem bilgisini sadece source metadata veya kaynak metninde varsa belirt.
- Bir kaynak parçası benzer ama soruyu doğrudan cevaplamıyorsa bunu doğrudan cevap gibi sunma.
- Cevabı Türkçe, kısa ve net ver.
""".strip()

    payload = {
        "selected_bank": bank,
        "question": question,
        "sources": context,
    }

    answer = safe_chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )

    # LLM çağrısı başarısız olursa (ağ/timeout/rate-limit) ham RAG cevabına düş.
    if not answer:
        return build_raw_rag_answer(bank=bank, sources=sources)

    return AskResponse(
        answer=answer,
        mode="rag_gpt",
        sources=sources,
    )


def answer_rag_question(
    question: str,
    bank: str,
    n_results: int = 6,
) -> AskResponse:
    sources = search_rag(
        question=question,
        bank=bank,
        n_results=n_results,
    )

    if settings.OPENAI_API_KEY:
        return llm_build_rag_answer(
            question=question,
            bank=bank,
            sources=sources,
        )

    return build_raw_rag_answer(
        bank=bank,
        sources=sources,
    )