from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import FinancialMetric
from app.utils.llm_utils import safe_chat_completion, safe_json_completion
from app.schemas.api import AskResponse
from app.services.bank_registry import get_bank_name
from app.services.numeric_service import (
    collect_numeric_candidates,
    current_numeric_value,
    llm_select_numeric_candidates,
    row_to_candidate,
)
from app.services.rag_service import search_rag
from app.services.report_metadata_service import get_display_period_label, latest_report
from app.utils.text_utils import (
    calculate_change_text,
    format_value,
    get_current_total_value,
    get_previous_total_value,
)


SUMMARY_METRICS = [
    {"label": "Dönem net kârı", "intent": "net_kar"},
    {"label": "Varlıklar toplamı", "intent": "varliklar_toplami"},
    {"label": "Krediler", "intent": "krediler"},
    {"label": "Mevduat", "intent": "mevduat"},
    {"label": "Özkaynaklar", "intent": "ozkaynak"},
]


PEER_RANKING_METRICS = [
    {
        "label": "Dönem net kârı",
        "intent": "net_kar",
        "question_template": "Tüm bankaların {period_label} dönem net kârı ne kadar?",
    },
    {
        "label": "Varlıklar toplamı",
        "intent": "varliklar_toplami",
        "question_template": "Tüm bankaların {period_label} varlıklar toplamı ne kadar?",
    },
    {
        "label": "Krediler",
        "intent": "krediler",
        "question_template": "Tüm bankaların {period_label} krediler toplamı ne kadar?",
    },
]


def metric_row_to_summary_item(
    label: str,
    row: FinancialMetric | None,
    reason: str = "",
) -> dict[str, Any]:
    if row is None:
        return {
            "label": label,
            "found": False,
            "reason": reason,
        }

    current_col, current_value = get_current_total_value(row.values_json)
    previous_col, previous_value = get_previous_total_value(row.values_json)

    return {
        "label": label,
        "found": True,
        "ticker": row.ticker,
        "period": row.period,
        "table_type": row.table_type,
        "metric": row.metric,
        "page": row.page,
        "unit": row.unit,
        "current_column": current_col,
        "current_value": current_value,
        "current_value_formatted": format_value(current_value, row.unit),
        "previous_column": previous_col,
        "previous_value": previous_value,
        "previous_value_formatted": format_value(previous_value, row.unit),
        "change_text": calculate_change_text(current_value, previous_value),
        "reason": reason,
    }


def build_metric_sources(summary_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []

    for item in summary_items:
        if not item.get("found"):
            continue

        sources.append(
            {
                "type": "financial_metric",
                "ticker": item.get("ticker"),
                "period": item.get("period"),
                "table_type": item.get("table_type"),
                "metric": item.get("metric"),
                "page": item.get("page"),
                "value": item.get("current_value"),
                "unit": item.get("unit"),
                "reason": item.get("reason", ""),
                "preview": (
                    f"{item.get('label')}: "
                    f"{item.get('metric')} = {item.get('current_value_formatted')}"
                ),
            }
        )

    return sources


def _get_period_code(session: Session, bank: str) -> str:
    report = latest_report(session, bank)

    return report.period_code if report and report.period_code else "guncel_donem"


def select_summary_metrics_with_llm(
    session: Session,
    bank: str,
) -> list[dict[str, Any]]:
    bank_name = get_bank_name(bank)

    candidate_groups: dict[str, list[dict[str, Any]]] = {}
    candidate_rows_by_id: dict[int, FinancialMetric] = {}

    for spec in SUMMARY_METRICS:
        candidates = collect_numeric_candidates(
            session=session,
            bank=bank,
            intent=spec["intent"],
            max_per_bank=12,
        )

        candidate_groups[spec["label"]] = []

        for row in candidates:
            candidate_rows_by_id[row.id] = row
            candidate_groups[spec["label"]].append(row_to_candidate(row))

    fallback_items: list[dict[str, Any]] = []

    for spec in SUMMARY_METRICS:
        label = spec["label"]
        candidates = candidate_groups.get(label, [])

        if not candidates:
            fallback_items.append(
                metric_row_to_summary_item(
                    label=label,
                    row=None,
                    reason="Aday satır bulunamadı.",
                )
            )
            continue

        first_candidate_id = candidates[0]["candidate_id"]
        row = candidate_rows_by_id.get(first_candidate_id)

        fallback_items.append(
            metric_row_to_summary_item(
                label=label,
                row=row,
                reason="Fallback olarak en güçlü aday seçildi.",
            )
        )

    if not settings.OPENAI_API_KEY:
        return fallback_items

    system_prompt = """
Sen banka finansal raporları için metrik eşleştirme asistanısın.

Görevin:
- Her ana başlık için verilen aday satırlar arasından doğru candidate_id seçmek.
- Asla yeni sayı üretme.
- Asla aday listesinde olmayan candidate_id üretme.
- Emin değilsen candidate_id değerini null yap.

Başlık kuralları:
1. "Dönem net kârı" için gelir tablosundaki "DÖNEM NET KARI VEYA ZARARI" satırını seç.
   "Sürdürülen faaliyetler dönem net kârı" toplam dönem net kârı yerine geçmemeli; toplam satır varsa toplamı seç.
2. "Varlıklar toplamı" için bilanço tablosundaki "VARLIKLAR TOPLAMI" satırını seç.
3. "Krediler" için ana bilanço krediler satırını seç.
   Doğru örnekler: "Krediler", "Krediler (5)", "Krediler 5.1.5", "Krediler 1.7".
   Nakit akışındaki "Krediler ve İhraç Edilen Menkul Değerlerden Sağlanan Nakit" satırını seçme.
4. "Mevduat" için ana bilanço pasifindeki "MEVDUAT" satırını seç.
5. "Özkaynaklar" için ana "ÖZKAYNAKLAR" satırını seç.

Sadece geçerli JSON döndür:
{
  "metric_selections": [
    {
      "label": "Dönem net kârı",
      "candidate_id": 123,
      "reason": "Kısa seçim gerekçesi"
    }
  ]
}
""".strip()

    payload = {
        "bank": bank,
        "bank_name": bank_name,
        "period": _get_period_code(session, bank),
        "candidate_groups": candidate_groups,
    }

    data = safe_json_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        default=None,
    )

    if data is None:
        return fallback_items

    selections = data.get("metric_selections")

    if not isinstance(selections, list):
        return fallback_items

    selected_by_label: dict[str, tuple[FinancialMetric | None, str]] = {}

    for item in selections:
        if not isinstance(item, dict):
            continue

        label = str(item.get("label", ""))
        candidate_id = item.get("candidate_id")
        reason = str(item.get("reason", ""))

        if candidate_id is None:
            selected_by_label[label] = (None, reason)
            continue

        try:
            candidate_id_int = int(candidate_id)
        except (TypeError, ValueError):
            selected_by_label[label] = (None, reason)
            continue

        selected_by_label[label] = (
            candidate_rows_by_id.get(candidate_id_int),
            reason,
        )

    result_items: list[dict[str, Any]] = []

    for spec in SUMMARY_METRICS:
        label = spec["label"]
        row, reason = selected_by_label.get(label, (None, ""))

        if row is None:
            # GPT seçememişse fallback item kullan.
            fallback = next(
                item for item in fallback_items if item["label"] == label
            )
            result_items.append(fallback)
        else:
            result_items.append(
                metric_row_to_summary_item(
                    label=label,
                    row=row,
                    reason=reason,
                )
            )

    return result_items


def selected_rows_for_all_banks(
    session: Session,
    intent: str,
    question: str,
) -> list[FinancialMetric]:
    candidates = collect_numeric_candidates(
        session=session,
        bank="ALL",
        intent=intent,
        max_per_bank=12,
    )

    if not candidates:
        return []

    if settings.OPENAI_API_KEY:
        selection_json = llm_select_numeric_candidates(
            question=question,
            bank="ALL",
            intent=intent,
            candidates=candidates,
        )

        selected_ids: set[int] = set()

        for item in selection_json.get("selections", []):
            candidate_id = item.get("candidate_id")

            if candidate_id is None:
                continue

            try:
                selected_ids.add(int(candidate_id))
            except (TypeError, ValueError):
                continue

        selected = [
            row
            for row in candidates
            if row.id in selected_ids
        ]

        if selected:
            selected.sort(key=current_numeric_value, reverse=True)
            return selected

    # GPT yoksa ya da seçim başarısızsa: her banka için ilk güçlü adayı al.
    best_by_ticker: dict[str, FinancialMetric] = {}

    for row in candidates:
        if row.ticker not in best_by_ticker:
            best_by_ticker[row.ticker] = row

    rows = list(best_by_ticker.values())
    rows.sort(key=current_numeric_value, reverse=True)

    return rows


def build_peer_rankings(
    session: Session,
    bank: str,
) -> list[dict[str, Any]]:
    rankings: list[dict[str, Any]] = []
    period_label = get_display_period_label(session, "ALL")

    for spec in PEER_RANKING_METRICS:
        rows = selected_rows_for_all_banks(
            session=session,
            intent=spec["intent"],
            question=spec["question_template"].format(period_label=period_label),
        )

        rank = None
        selected_value = None
        selected_metric = None
        selected_page = None

        for index, row in enumerate(rows, start=1):
            if row.ticker == bank:
                rank = index
                _, value = get_current_total_value(row.values_json)
                selected_value = format_value(value, row.unit)
                selected_metric = row.metric
                selected_page = row.page
                break

        top_items = []

        for row in rows[:5]:
            _, value = get_current_total_value(row.values_json)

            top_items.append(
                {
                    "ticker": row.ticker,
                    "value": format_value(value, row.unit),
                    "metric": row.metric,
                    "page": row.page,
                }
            )

        rankings.append(
            {
                "label": spec["label"],
                "intent": spec["intent"],
                "rank": rank,
                "selected_value": selected_value,
                "selected_metric": selected_metric,
                "selected_page": selected_page,
                "total_banks": len(rows),
                "top_items": top_items,
            }
        )

    return rankings


def get_audit_rag_sources(session: Session, bank: str) -> list[dict[str, Any]]:
    bank_name = get_bank_name(bank)
    period_label = get_display_period_label(session, bank)

    return search_rag(
        question=(
            f"{bank_name} {period_label} denetim sonucu, sınırlı denetim, "
            f"dikkat çekilen hususlar, şartlı görüş, önemli riskler"
        ),
        bank=bank,
        n_results=6,
    )


def build_fallback_summary_answer(
    bank: str,
    period_label: str,
    summary_items: list[dict[str, Any]],
    peer_rankings: list[dict[str, Any]],
    rag_sources: list[dict[str, Any]],
) -> str:
    bank_name = get_bank_name(bank)

    lines = [f"{bank_name} {period_label} Yönetici Özeti", ""]

    lines.append("Ana Finansal Göstergeler:")

    for item in summary_items:
        if not item.get("found"):
            lines.append(f"- {item['label']}: Veri bulunamadı.")
            continue

        lines.append(
            f"- {item['label']}: {item['current_value_formatted']} "
            f"({item['metric']}, sayfa {item['page']}). "
            f"{item['change_text']}"
        )

    if peer_rankings:
        lines.append("")
        lines.append("Rakiplere Göre Konum:")

        for ranking in peer_rankings:
            if ranking["rank"]:
                lines.append(
                    f"- {ranking['label']}: {ranking['total_banks']} banka içinde "
                    f"{ranking['rank']}. sırada."
                )

    if rag_sources:
        lines.append("")
        lines.append("Denetim / Risk Kaynaklarından İlk Bulgu:")
        lines.append(str(rag_sources[0].get("text", ""))[:700])

    return "\n".join(lines)


def build_executive_summary_answer(
    question: str,
    bank: str,
    session: Session,
) -> AskResponse:
    if bank == "ALL":
        return AskResponse(
            answer=(
                "Yönetici özeti için önce belirli bir banka seçmeni öneririm. "
                "Tüm bankalar seçiliyken karşılaştırma soruları daha uygundur. "
                "Örneğin: 'Tüm bankaları net kâra göre sırala' veya "
                "'VakıfBank özetler misin?'"
            ),
            mode="executive_summary_need_bank",
            sources=[],
        )

    bank_name = get_bank_name(bank)
    period_label = get_display_period_label(session, bank)

    summary_items = select_summary_metrics_with_llm(
        session=session,
        bank=bank,
    )

    peer_rankings = build_peer_rankings(
        session=session,
        bank=bank,
    )

    rag_sources = get_audit_rag_sources(session, bank)

    metric_sources = build_metric_sources(summary_items)
    all_sources = metric_sources + rag_sources

    if not settings.OPENAI_API_KEY:
        return AskResponse(
            answer=build_fallback_summary_answer(
                bank=bank,
                period_label=period_label,
                summary_items=summary_items,
                peer_rankings=peer_rankings,
                rag_sources=rag_sources,
            ),
            mode="executive_summary_no_llm",
            sources=all_sources,
        )

    system_prompt = """
Sen banka genel müdürüne rapor hazırlayan kıdemli finansal analiz asistanısın.

Görevin:
- Verilen financial_metrics ve RAG kaynaklarına dayanarak yönetici seviyesinde dönem özeti yazmak.
- Sayıları sadece verilen metric_data içinden kullanmak.
- Kaynakta olmayan bilgi, sebep, yönetim açıklaması, piyasa yorumu veya varsayım uydurma.
- Eğer bir neden kaynaklarda açıkça yoksa "Bu değişimin nedeni kaynaklarda açıkça belirtilmemiştir" de.
- "Yönetim şunu belirtti", "banka bunu şuna bağladı", "stratejik adım atılacağı belirtilmektedir" gibi ifadeleri sadece kaynaklarda açıkça varsa kullan.
- Denetim sonucunu kaynakta geçtiği şekliyle yaz. Emin değilsen "denetim/risk notları için kaynaklarda açık ifade sınırlı görünüyor" de.
- Cevabı Türkçe yaz.
- "bin TL" birimini açıkça belirt.
- Finansal performans, bilanço büyüklüğü, kredi/mevduat dengesi, özkaynak ve denetim/risk notunu kapsa.
- Rakiplere göre konumu sadece verilen peer_rankings verisine göre yorumla.
- En sonda "Analitik Yönetim Yorumu" ekle; bu bölümde de sadece verilen verilere dayanarak yorum yap.

Cevap formatı:

# {bank_name} {period_label} Yönetici Özeti

1. Genel Görünüm
2. Kârlılık
3. Bilanço Büyüklüğü
4. Krediler ve Mevduat
5. Özkaynak ve Sermaye Görünümü
6. Denetim / Risk Notları
7. Rakiplere Göre Konum
8. Analitik Yönetim Yorumu

Yazım kuralları:
- Her bölüm 2-4 cümle olsun.
- Gereksiz uzun yazma ama yüzeysel de kalma.
- Kaynakta olmayan neden-sonuç ilişkisi kurma.
- Önceki dönem karşılaştırması varsa yüzde artış/azalış bilgisini kullan.
""".strip()

    payload = {
        "question": question,
        "bank": bank,
        "bank_name": bank_name,
        "period": _get_period_code(session, bank),
        "metric_data": summary_items,
        "peer_rankings": peer_rankings,
        "rag_sources": [
            {
                "ticker": source.get("ticker"),
                "period": source.get("period"),
                "chunk_type": source.get("chunk_type"),
                "table_type": source.get("table_type"),
                "metric": source.get("metric"),
                "page": source.get("page"),
                "text": str(source.get("text", ""))[:1400],
            }
            for source in rag_sources
        ],
    }

    answer = safe_chat_completion(
        messages=[
            {
                "role": "system",
                "content": system_prompt.replace("{bank_name}", bank_name).replace(
                    "{period_label}", period_label
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ],
    )

    # LLM başarısızsa deterministik fallback özetine düş.
    if not answer:
        return AskResponse(
            answer=build_fallback_summary_answer(
                bank=bank,
                period_label=period_label,
                summary_items=summary_items,
                peer_rankings=peer_rankings,
                rag_sources=rag_sources,
            ),
            mode="executive_summary_no_llm",
            sources=all_sources,
        )

    return AskResponse(
        answer=answer,
        mode="executive_summary_gpt",
        sources=all_sources,
    )