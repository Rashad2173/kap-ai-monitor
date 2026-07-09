from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import chromadb
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from FlagEmbedding import BGEM3FlagModel
from openai import OpenAI
from pydantic import BaseModel
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env")

from app.db.connection import SessionLocal
from app.db.models import FinancialMetric, RagChunk


CHROMA_PATH = "data/chroma"
COLLECTION_NAME = "kap_reports_bge_m3"
MODEL_NAME = "BAAI/bge-m3"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


BANK_NAMES = {
    "ALL": "Tüm Bankalar",
    "AKBNK": "Akbank",
    "GARAN": "Garanti BBVA",
    "VAKBN": "VakıfBank",
    "ISCTR": "İş Bankası",
    "YKBNK": "Yapı Kredi",
    "HALKB": "Halkbank",
    "ALBRK": "Albaraka Türk",
    "TSKB": "TSKB",
    "SKBNK": "Şekerbank",
    "ICBCT": "ICBC Turkey",
    "KLNMA": "Türkiye Kalkınma ve Yatırım Bankası",
    "QNBTR": "QNB Türkiye",
}


app = FastAPI(
    title="KAP AI Monitor API",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    bank: str = "ALL"
    question: str


class AskResponse(BaseModel):
    answer: str
    mode: str
    sources: list[dict[str, Any]]


_embed_model: BGEM3FlagModel | None = None
_chroma_collection = None
_openai_client: OpenAI | None = None


# ============================================================
# Genel yardımcılar
# ============================================================

def get_db() -> Session:
    return SessionLocal()


def get_openai_client() -> OpenAI:
    global _openai_client

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY .env dosyasında bulunamadı.")

    if _openai_client is None:
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)

    return _openai_client


def normalize_text(text: str | None) -> str:
    if not text:
        return ""

    text = text.lower()

    tr_map = str.maketrans(
        {
            "ı": "i",
            "İ": "i",
            "ğ": "g",
            "Ğ": "g",
            "ü": "u",
            "Ü": "u",
            "ş": "s",
            "Ş": "s",
            "ö": "o",
            "Ö": "o",
            "ç": "c",
            "Ç": "c",
        }
    )

    text = text.translate(tr_map)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def parse_number(value: str | None) -> float | None:
    if value is None:
        return None

    cleaned = str(value).strip()
    cleaned = cleaned.replace(".", "")
    cleaned = cleaned.replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return None


def format_value(value: str | None, unit: str | None = "1.000 TL") -> str:
    if not value:
        return "-"

    if unit in {"1.000 TL", "1000 TL", "Bin TL", "bin TL"}:
        return f"{value} bin TL"

    if unit:
        return f"{value} {unit}"

    return value


def clean_bank(bank: str | None) -> str:
    if not bank:
        return "ALL"

    bank = bank.upper().strip()

    if bank not in BANK_NAMES:
        return "ALL"

    return bank


def get_current_total_value(values_json: dict[str, Any]) -> tuple[str | None, str | None]:
    if not isinstance(values_json, dict):
        return None, None

    for key, value in values_json.items():
        key_norm = normalize_text(key)

        if "cari" in key_norm and "toplam" in key_norm:
            return key, str(value)

    for key, value in values_json.items():
        key_norm = normalize_text(key)

        if "cari" in key_norm:
            return key, str(value)

    for key, value in values_json.items():
        return key, str(value)

    return None, None


def get_previous_total_value(values_json: dict[str, Any]) -> tuple[str | None, str | None]:
    if not isinstance(values_json, dict):
        return None, None

    for key, value in values_json.items():
        key_norm = normalize_text(key)

        if "onceki" in key_norm and "toplam" in key_norm:
            return key, str(value)

    for key, value in values_json.items():
        key_norm = normalize_text(key)

        if "onceki" in key_norm:
            return key, str(value)

    return None, None


def current_numeric_value(item: FinancialMetric) -> float:
    _, value = get_current_total_value(item.values_json)
    parsed = parse_number(value)
    return parsed if parsed is not None else float("-inf")


# ============================================================
# Intent tespiti
# ============================================================

METRIC_ALIASES = {
    "net_kar": [
        "son 3 ay kar",
        "son üç ay kar",
        "net kar",
        "net kâr",
        "dönem net karı",
        "dönem net kârı",
        "dönem net kar",
        "dönem net kâr",
        "kar zarar",
        "kâr zarar",
        "karı ne kadar",
        "kârı ne kadar",
    ],
    "krediler": [
        "krediler",
        "kredi",
        "toplam kredi",
        "krediler toplamı",
        "krediler toplami",
    ],
    "varliklar_toplami": [
        "varlıklar toplamı",
        "varliklar toplami",
        "aktif toplamı",
        "aktif toplami",
        "toplam aktif",
        "aktifler",
    ],
    "mevduat": [
        "mevduat",
        "toplam mevduat",
    ],
    "ozkaynak": [
        "özkaynak",
        "ozkaynak",
        "öz kaynak",
        "oz kaynak",
        "özkaynaklar",
        "ozkaynaklar",
        "toplam özkaynak",
        "toplam ozkaynak",
    ],
    "nakit": [
        "nakit ve nakit benzerleri",
        "nakit değerler",
        "nakit degerler",
        "merkez bankası",
        "merkez bankasi",
    ],
    "finansal_varliklar": [
        "finansal varlıklar",
        "finansal varliklar",
        "finansal varlıklar net",
        "finansal varliklar net",
    ],
}


def detect_metric_intent(question: str) -> str | None:
    q = normalize_text(question)

    for intent, aliases in METRIC_ALIASES.items():
        for alias in aliases:
            if normalize_text(alias) in q:
                return intent

    return None


def is_numeric_question(question: str) -> bool:
    q = normalize_text(question)

    numeric_words = [
        "kaç",
        "kac",
        "ne kadar",
        "tutar",
        "tutarı",
        "toplam",
        "toplamı",
        "toplami",
        "kar",
        "kâr",
        "kredi",
        "mevduat",
        "varlık",
        "varlik",
        "aktif",
        "özkaynak",
        "ozkaynak",
        "değer",
        "degeri",
    ]

    return any(normalize_text(word) in q for word in numeric_words)


# ============================================================
# GPT için aday toplama
# ============================================================

def candidate_match_score(metric: str, intent: str, table_type: str | None) -> int:
    metric_norm = normalize_text(metric)
    table_norm = normalize_text(table_type or "")

    score = 0

    if table_norm == "bilanco":
        score += 40
    elif table_norm in {"gelir_tablosu", "kapsamli_gelir_tablosu"}:
        score += 30
    elif table_norm == "nakit_akis_tablosu":
        score -= 20
    elif table_norm in {"nazim_hesaplar", "diger"}:
        score -= 40

    if intent == "krediler":
        if "kredi" not in metric_norm and "krediler" not in metric_norm:
            return -999

        if metric_norm == "krediler":
            score += 120
        elif metric_norm.startswith("krediler "):
            score += 110
        elif "krediler" in metric_norm:
            score += 70
        elif "kredi" in metric_norm:
            score += 35

        return score

    if intent == "net_kar":
        if "kar" not in metric_norm and "zarar" not in metric_norm:
            return -999

        if table_norm in {"gelir_tablosu", "kapsamli_gelir_tablosu"}:
            score += 80

        good_terms = [
            "donem net kari",
            "donem net kar",
            "donem net kar veya zarari",
            "net donem kari",
        ]

        if metric_norm in good_terms:
            score += 130
        elif any(term in metric_norm for term in good_terms):
            score += 100
        elif "net kar" in metric_norm:
            score += 60

        return score

    if intent == "varliklar_toplami":
        if (
            "varliklar toplami" not in metric_norm
            and "aktif toplami" not in metric_norm
            and "toplam aktif" not in metric_norm
        ):
            return -999

        if table_norm == "bilanco":
            score += 90

        if metric_norm == "varliklar toplami":
            score += 130
        else:
            score += 90

        return score

    if intent == "mevduat":
        if "mevduat" not in metric_norm:
            return -999

        if table_norm == "bilanco":
            score += 90

        if metric_norm == "mevduat":
            score += 130
        elif metric_norm.startswith("mevduat "):
            score += 90
        else:
            score += 50

        return score

    if intent == "ozkaynak":
        if "ozkaynak" not in metric_norm and "oz kaynak" not in metric_norm:
            return -999

        if table_norm == "bilanco":
            score += 90

        if metric_norm in {"ozkaynak", "ozkaynaklar"}:
            score += 130
        else:
            score += 70

        return score

    if intent == "nakit":
        if "nakit" not in metric_norm and "merkez bankasi" not in metric_norm:
            return -999

        if table_norm == "bilanco":
            score += 90

        if "nakit ve nakit benzerleri" in metric_norm:
            score += 130
        elif "nakit degerler" in metric_norm:
            score += 80
        elif "merkez bankasi" in metric_norm:
            score += 50

        return score

    if intent == "finansal_varliklar":
        if "finansal varliklar" not in metric_norm:
            return -999

        if table_norm == "bilanco":
            score += 90

        if "finansal varliklar net" in metric_norm:
            score += 130
        else:
            score += 80

        return score

    return -999


def collect_numeric_candidates(
    session: Session,
    bank: str,
    intent: str,
    max_per_bank: int = 12,
) -> list[FinancialMetric]:
    query = session.query(FinancialMetric)

    if bank != "ALL":
        query = query.filter(FinancialMetric.ticker == bank)

    rows = query.all()

    grouped: dict[str, list[tuple[int, float, FinancialMetric]]] = {}

    for row in rows:
        score = candidate_match_score(row.metric, intent, row.table_type)

        if score <= -999:
            continue

        value = current_numeric_value(row)

        grouped.setdefault(row.ticker, []).append((score, value, row))

    selected: list[FinancialMetric] = []

    for _, items in grouped.items():
        items.sort(
            key=lambda item: (
                item[0],
                item[1],
            ),
            reverse=True,
        )

        selected.extend([row for _, _, row in items[:max_per_bank]])

    return selected


def row_to_candidate(row: FinancialMetric) -> dict[str, Any]:
    current_col, current_value = get_current_total_value(row.values_json)
    previous_col, previous_value = get_previous_total_value(row.values_json)

    return {
        "candidate_id": row.id,
        "ticker": row.ticker,
        "period": row.period,
        "table_type": row.table_type,
        "metric": row.metric,
        "parent_section": row.parent_section or "",
        "page": row.page,
        "unit": row.unit,
        "current_column": current_col,
        "current_value": current_value,
        "previous_column": previous_col,
        "previous_value": previous_value,
    }


def group_candidates_by_ticker(rows: list[FinancialMetric]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        grouped.setdefault(row.ticker, []).append(row_to_candidate(row))

    return grouped


def llm_select_numeric_candidates(
    question: str,
    bank: str,
    intent: str,
    candidates: list[FinancialMetric],
) -> dict[str, Any]:
    if not candidates:
        return {"selections": []}

    payload = {
        "question": question,
        "selected_bank": bank,
        "intent": intent,
        "candidates_by_ticker": group_candidates_by_ticker(candidates),
    }

    system_prompt = """
Sen finansal rapor metrik eşleştirme asistanısın.

Görevin:
- Kullanıcının sorusuna göre verilen aday satırlar arasından doğru finansal metrik satırını seçmek.
- Asla yeni sayı üretme.
- Asla aday listesinde olmayan candidate_id üretme.
- Her banka için en doğru candidate_id seç.
- Emin değilsen candidate_id değerini null yap.

Önemli kurallar:
1. "Krediler toplamı" sorulursa ana bilanço kalemi olan Krediler satırını seç.
   Doğruya örnekler: "Krediler", "Krediler (5)", "Krediler 5.1.5", "Krediler 1.7".
   Yanlışa örnekler: "Krediler ve İhraç Edilen Menkul Değerlerden Sağlanan Nakit",
   "Alınan krediler", "kredi riski", "beklenen zarar karşılıkları", "gayrinakdi krediler".
2. "Net kâr" sorulursa gelir tablosundaki dönem net kârı/zararı satırını seç.
3. "Varlıklar toplamı / aktif toplamı" sorulursa bilanço tablosundaki VARLIKLAR TOPLAMI satırını seç.
4. "Mevduat" sorulursa bilanço pasifindeki ana MEVDUAT satırını seç.
5. "Özkaynak" sorulursa ana ÖZKAYNAKLAR satırını seç.
6. Aynı metrik birden fazla tabloda geçerse bilanço/gelir tablosu ana kalemini, nakit akış satırlarına tercih et.

Sadece geçerli JSON döndür:
{
  "selections": [
    {
      "ticker": "GARAN",
      "candidate_id": 123,
      "reason": "Kısa seçim gerekçesi"
    }
  ]
}
""".strip()

    client = get_openai_client()

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )

    content = response.choices[0].message.content or "{}"

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {"selections": []}

    if not isinstance(data, dict):
        return {"selections": []}

    if not isinstance(data.get("selections"), list):
        return {"selections": []}

    return data


def build_numeric_answer_from_llm_selection(
    bank: str,
    candidates: list[FinancialMetric],
    selection_json: dict[str, Any],
) -> AskResponse:
    candidate_map = {row.id: row for row in candidates}
    selections = selection_json.get("selections", [])

    selected_rows: list[tuple[FinancialMetric, str]] = []

    for item in selections:
        if not isinstance(item, dict):
            continue

        candidate_id = item.get("candidate_id")
        reason = str(item.get("reason", ""))

        if candidate_id is None:
            continue

        try:
            candidate_id_int = int(candidate_id)
        except (TypeError, ValueError):
            continue

        row = candidate_map.get(candidate_id_int)

        if not row:
            continue

        selected_rows.append((row, reason))

    if not selected_rows:
        return AskResponse(
            answer=(
                "GPT aday satırlar arasından güvenilir bir seçim yapamadı. "
                "Adayları genişletmemiz veya prompt kurallarını güncellememiz gerekebilir."
            ),
            mode="numeric_gpt_match",
            sources=[],
        )

    if bank == "ALL":
        selected_rows.sort(key=lambda pair: current_numeric_value(pair[0]), reverse=True)

        lines = ["Seçilen metrik için banka bazlı sonuçlar:"]
        sources: list[dict[str, Any]] = []

        for row, reason in selected_rows:
            current_col, current_value = get_current_total_value(row.values_json)
            previous_col, previous_value = get_previous_total_value(row.values_json)

            lines.append(
                f"- {row.ticker}: {row.metric} = {format_value(current_value, row.unit)}"
            )

            sources.append(
                {
                    "type": "financial_metric",
                    "ticker": row.ticker,
                    "period": row.period,
                    "table_type": row.table_type,
                    "metric": row.metric,
                    "page": row.page,
                    "value_column": current_col,
                    "value": current_value,
                    "previous_value_column": previous_col,
                    "previous_value": previous_value,
                    "unit": row.unit,
                    "reason": reason,
                    "preview": f"{row.metric}: {format_value(current_value, row.unit)}",
                }
            )

        return AskResponse(
            answer="\n".join(lines),
            mode="numeric_gpt_match",
            sources=sources,
        )

    row, reason = selected_rows[0]
    current_col, current_value = get_current_total_value(row.values_json)
    previous_col, previous_value = get_previous_total_value(row.values_json)

    bank_name = BANK_NAMES.get(bank, bank)

    answer = (
        f"{bank_name} için {row.period} döneminde **{row.metric}** kaleminin "
        f"cari toplam değeri **{format_value(current_value, row.unit)}** olarak görünüyor."
    )

    if previous_value:
        answer += f"\nÖnceki dönem toplam değeri ise **{format_value(previous_value, row.unit)}**."

    answer += f"\nKaynak: {row.table_type}, sayfa {row.page}."

    if reason:
        answer += f"\nEşleştirme gerekçesi: {reason}"

    return AskResponse(
        answer=answer,
        mode="numeric_gpt_match",
        sources=[
            {
                "type": "financial_metric",
                "ticker": row.ticker,
                "period": row.period,
                "table_type": row.table_type,
                "metric": row.metric,
                "page": row.page,
                "value_column": current_col,
                "value": current_value,
                "previous_value_column": previous_col,
                "previous_value": previous_value,
                "unit": row.unit,
                "reason": reason,
                "preview": f"{row.metric}: {format_value(current_value, row.unit)}",
            }
        ],
    )


# ============================================================
# Chroma / RAG
# ============================================================

def get_chroma_collection():
    global _chroma_collection

    if _chroma_collection is not None:
        return _chroma_collection

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    _chroma_collection = client.get_collection(COLLECTION_NAME)

    return _chroma_collection


def get_embedding_model() -> BGEM3FlagModel:
    global _embed_model

    if _embed_model is not None:
        return _embed_model

    _embed_model = BGEM3FlagModel(
        MODEL_NAME,
        use_fp16=False,
    )

    return _embed_model


def embed_query(question: str) -> list[float]:
    model = get_embedding_model()

    output = model.encode(
        [question],
        batch_size=1,
        max_length=8192,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )

    vec = output["dense_vecs"][0]

    if hasattr(vec, "tolist"):
        return vec.tolist()

    return list(vec)


def search_rag(
    question: str,
    bank: str,
    n_results: int = 6,
) -> list[dict[str, Any]]:
    collection = get_chroma_collection()
    query_embedding = embed_query(question)

    where_filter = None

    if bank != "ALL":
        where_filter = {"ticker": bank}

    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where=where_filter,
        include=["documents", "metadatas", "distances"],
    )

    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]

    sources: list[dict[str, Any]] = []

    for doc, metadata, distance in zip(documents, metadatas, distances):
        metadata = metadata or {}

        sources.append(
            {
                "type": "rag_chunk",
                "ticker": metadata.get("ticker", ""),
                "period": metadata.get("period", ""),
                "chunk_type": metadata.get("chunk_type", ""),
                "table_type": metadata.get("table_type", ""),
                "metric": metadata.get("metric", ""),
                "page": metadata.get("page", ""),
                "distance": distance,
                "preview": doc[:500],
                "text": doc,
            }
        )

    return sources


def llm_build_rag_answer(question: str, bank: str, sources: list[dict[str, Any]]) -> AskResponse:
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
Sen KAP banka finansal raporları için çalışan bir analiz asistanısın.
Sadece verilen kaynak parçalarına dayanarak cevap ver.
Kaynakta olmayan bilgiyi uydurma.
Cevabı Türkçe, kısa ve net ver.
Mümkünse banka, dönem, sayfa ve tablo bilgisini belirt.
""".strip()

    payload = {
        "selected_bank": bank,
        "question": question,
        "sources": context,
    }

    client = get_openai_client()

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )

    answer = response.choices[0].message.content or "Cevap üretilemedi."

    return AskResponse(
        answer=answer,
        mode="rag_gpt",
        sources=sources,
    )


def build_raw_rag_answer(bank: str, sources: list[dict[str, Any]]) -> AskResponse:
    if not sources:
        return AskResponse(
            answer="Bu soru için RAG tarafında ilgili kaynak bulunamadı.",
            mode="rag",
            sources=[],
        )

    bank_name = BANK_NAMES.get(bank, bank)

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


# ============================================================
# Dashboard
# ============================================================

def get_audit_text_for_bank(
    session: Session,
    bank: str,
) -> str | None:
    query = session.query(RagChunk)

    if bank != "ALL":
        query = query.filter(RagChunk.ticker == bank)

    candidates = query.filter(RagChunk.chunk_type == "text").limit(80).all()

    for chunk in candidates:
        text_norm = normalize_text(chunk.text)

        if (
            "denetim sonucu" in text_norm
            or "dikkat cekilen" in text_norm
            or "sartli" in text_norm
            or "olumlu" in text_norm
        ):
            return chunk.text[:700]

    return None


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "message": "KAP AI Monitor API çalışıyor.",
        "openai_model": OPENAI_MODEL,
        "openai_key_loaded": bool(OPENAI_API_KEY),
    }


@app.get("/api/dashboard/summary")
def dashboard_summary(bank: str = "ALL"):
    bank = clean_bank(bank)
    session = get_db()

    try:
        if bank == "ALL":
            candidates = collect_numeric_candidates(
                session=session,
                bank="ALL",
                intent="net_kar",
                max_per_bank=10,
            )

            if OPENAI_API_KEY and candidates:
                selection = llm_select_numeric_candidates(
                    question="Tüm bankaların son 3 ay net kârı ne kadar?",
                    bank="ALL",
                    intent="net_kar",
                    candidates=candidates,
                )

                selected_ids = {
                    int(item["candidate_id"])
                    for item in selection.get("selections", [])
                    if item.get("candidate_id") is not None
                }

                rows = [row for row in candidates if row.id in selected_ids]
            else:
                rows = candidates

            rows.sort(key=current_numeric_value, reverse=True)

            if rows:
                top = rows[0]
                _, top_value = get_current_total_value(top.values_json)
                profit_title = f"{top.ticker}: {format_value(top_value, top.unit)}"
                profit_desc = "Son dönem net kâr kaleminde en yüksek bulunan banka."
            else:
                profit_title = "Veri bulunamadı"
                profit_desc = "Net kâr metriği bulunamadı."

            summary_lines = []

            for row in rows[:8]:
                _, value = get_current_total_value(row.values_json)
                summary_lines.append(f"{row.ticker}: {format_value(value, row.unit)}")

            summary = (
                "Tüm bankalar için son dönem net kâr özetleri:\n"
                + "\n".join(summary_lines)
                if summary_lines
                else "Tüm bankalar için net kâr özeti üretilemedi."
            )

            return {
                "profit": {
                    "title": profit_title,
                    "description": profit_desc,
                },
                "highlight": {
                    "title": f"{len(rows)} banka analiz edildi",
                    "description": "Sayısal metriklerde GPT aday satırlar arasından seçim yapar.",
                },
                "audit": {
                    "title": "Denetim bilgileri hazır",
                    "description": "Denetçi görüşü ve dikkat çekilen hususlar RAG tarafında aranabilir.",
                },
                "summary": summary,
            }

        bank_name = BANK_NAMES.get(bank, bank)

        profit_candidates = collect_numeric_candidates(session, bank, "net_kar", max_per_bank=8)
        loan_candidates = collect_numeric_candidates(session, bank, "krediler", max_per_bank=8)
        asset_candidates = collect_numeric_candidates(session, bank, "varliklar_toplami", max_per_bank=8)

        profit = max(profit_candidates, key=current_numeric_value) if profit_candidates else None
        loans = max(loan_candidates, key=current_numeric_value) if loan_candidates else None
        assets = max(asset_candidates, key=current_numeric_value) if asset_candidates else None

        audit_text = get_audit_text_for_bank(session, bank)

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
            f"Bu özet financial_metrics tablosu ve rag_chunks kayıtları üzerinden oluşturuldu."
        )

        return {
            "profit": {
                "title": profit_title,
                "description": profit_desc,
            },
            "highlight": {
                "title": highlight_title,
                "description": highlight_desc,
            },
            "audit": {
                "title": audit_title,
                "description": audit_desc,
            },
            "summary": summary,
        }

    finally:
        session.close()


# ============================================================
# Ask endpoint
# ============================================================

@app.post("/api/ask", response_model=AskResponse)
def ask(request: AskRequest):
    bank = clean_bank(request.bank)
    question = request.question.strip()

    if not question:
        return AskResponse(
            answer="Lütfen bir soru yaz.",
            mode="empty",
            sources=[],
        )

    session = get_db()

    try:
        metric_intent = detect_metric_intent(question)

        if metric_intent and is_numeric_question(question):
            candidates = collect_numeric_candidates(
                session=session,
                bank=bank,
                intent=metric_intent,
                max_per_bank=12,
            )

            if not candidates:
                return AskResponse(
                    answer="Bu soru için financial_metrics tablosunda aday sayısal satır bulamadım.",
                    mode="numeric_no_candidates",
                    sources=[],
                )

            if not OPENAI_API_KEY:
                return AskResponse(
                    answer=(
                        "Sayısal adaylar bulundu ancak OPENAI_API_KEY yüklü değil. "
                        "GPT aday seçimi için .env dosyasına OPENAI_API_KEY eklenmeli."
                    ),
                    mode="numeric_no_openai_key",
                    sources=[row_to_candidate(row) for row in candidates[:20]],
                )

            selection_json = llm_select_numeric_candidates(
                question=question,
                bank=bank,
                intent=metric_intent,
                candidates=candidates,
            )

            return build_numeric_answer_from_llm_selection(
                bank=bank,
                candidates=candidates,
                selection_json=selection_json,
            )

        sources = search_rag(
            question=question,
            bank=bank,
            n_results=6,
        )

        if OPENAI_API_KEY:
            return llm_build_rag_answer(
                question=question,
                bank=bank,
                sources=sources,
            )

        return build_raw_rag_answer(
            bank=bank,
            sources=sources,
        )

    finally:
        session.close()