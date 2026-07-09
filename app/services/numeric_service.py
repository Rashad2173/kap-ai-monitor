from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import FinancialMetric
from app.utils.llm_utils import safe_json_completion
from app.schemas.api import AskResponse
from app.services.bank_registry import get_bank_name
from app.services.question_classifier import strip_bank_names_for_metric_detection
from app.services.report_metadata_service import get_latest_report_ids
from app.utils.text_utils import (
    calculate_change_text,
    format_value,
    get_current_total_value,
    get_previous_total_value,
    normalize_text,
    parse_number,
)


MIN_SCORE_BY_INTENT = {
    "krediler": 120,
    "net_kar": 120,
    "varliklar_toplami": 180,
    "mevduat": 120,
    "toplanan_fonlar": 130,
    "ozkaynak": 160,
    "yukumlulukler": 160,
    "nakit": 130,
    "finansal_varliklar": 120,
    "esas_faaliyet_kari": 130,
    "largest_asset_item": 0,
    "unknown_metric": 9999,
}


def current_numeric_value(item: FinancialMetric) -> float:
    _, value = get_current_total_value(item.values_json)
    parsed = parse_number(value)
    return parsed if parsed is not None else float("-inf")


def extract_column_period(column_name: str | None) -> str:
    if not column_name:
        return ""

    match = re.search(r"\(([^)]+)\)", column_name)

    if match:
        return match.group(1)

    return column_name


def has_any_term(question: str, terms: list[str]) -> bool:
    q = normalize_text(question)

    return any(normalize_text(term) in q for term in terms)


def candidate_match_score(
    metric: str,
    intent: str,
    table_type: str | None,
    parent_section: str | None = None,
) -> int:
    metric_norm = normalize_text(metric)
    table_norm = normalize_text(table_type or "").replace(" ", "_")
    parent_norm = normalize_text(parent_section or "")

    score = 0

    if table_norm == "bilanco":
        score += 50
    elif table_norm in {"gelir_tablosu", "kapsamli_gelir_tablosu"}:
        score += 30
    elif table_norm == "nakit_akis_tablosu":
        score -= 120
    elif table_norm in {"nazim_hesaplar", "diger"}:
        score -= 80

    if intent == "krediler":
        if "kredi" not in metric_norm and "krediler" not in metric_norm:
            return -999

        # Ana kredi kalemi varlıklar tarafında olmalı.
        # Sermaye benzeri borçlanma araçları altındaki "Krediler" ana kredi kalemi değildir.
        bad_parent_terms = [
            "sermaye benzeri",
            "borclanma araclari",
            "yukumlulukler",
            "yukumluluk",
        ]

        if any(term in parent_norm for term in bad_parent_terms):
            return -999

        bad_metric_terms = [
            "alinan krediler",
            "alınan krediler",
            "nakdi krediler",
            "gayrinakdi",
            "kredi kart",
            "kredi riski",
            "beklenen zarar",
            "kredilere iliskin",
            "kredilere ilişkin",
            "kullanilan krediler",
            "kullanılan krediler",
            "kullandirma garantili",
            "kullandırma garantili",
            "sermaye benzeri",
            "ihrac edilen",
            "ihraç edilen",
            "saglanan nakit",
            "sağlanan nakit",
            "net artis",
            "net artış",
            "net azalis",
            "net azalış",
        ]

        if any(term in metric_norm for term in bad_metric_terms):
            return -999

        if table_norm != "bilanco":
            score -= 120

        if metric_norm == "krediler":
            score += 140
        elif metric_norm.startswith("krediler "):
            score += 130
        elif "krediler" in metric_norm:
            score += 90
        elif "kredi" in metric_norm:
            score += 40

        return score

    if intent == "net_kar":
        if "kar" not in metric_norm and "zarar" not in metric_norm:
            return -999

        bad_terms = [
            "net artis",
            "net azalis",
            "artis azalis",
            "finansal varliklarda",
            "gercege uygun deger farki",
            "kar veya zarara yansitilan finansal varlik",
            "nakit akis",
            "turev finansal",
        ]

        if any(term in metric_norm for term in bad_terms):
            return -999

        if table_norm in {"gelir_tablosu", "kapsamli_gelir_tablosu"}:
            score += 100
        else:
            score -= 80

        good_terms = [
            "donem net kari",
            "donem net kar",
            "donem net kari veya zarari",
            "donem net kar veya zarari",
            "net donem kari",
        ]

        if metric_norm in good_terms or metric_norm.startswith("donem net kari"):
            score += 190
        elif metric_norm.startswith("donem net kar"):
            score += 180
        elif any(term in metric_norm for term in good_terms):
            score += 120
        elif "net kar" in metric_norm:
            score += 70

        return score

    if intent == "varliklar_toplami":
        if (
            "varliklar toplami" not in metric_norm
            and "aktif toplami" not in metric_norm
            and "toplam aktif" not in metric_norm
        ):
            return -999

        if table_norm == "bilanco":
            score += 120
        else:
            score -= 120

        if metric_norm == "varliklar toplami":
            score += 160
        else:
            score += 100

        return score

    if intent == "mevduat":
        # Katılım bankalarında "mevduat" karşılığı genelde "Toplanan Fonlar"dır.
        is_normal_deposit = "mevduat" in metric_norm
        is_participation_deposit = (
            "toplanan fon" in metric_norm
            or "toplanan fonlar" in metric_norm
            or "katilim fon" in metric_norm
            or "katilma hesap" in metric_norm
        )

        if not is_normal_deposit and not is_participation_deposit:
            return -999

        bad_terms = [
            "net artis",
            "net artış",
            "net azalis",
            "net azalış",
            "nakit akis",
            "nakit akış",
            "kar payi gider",
            "kâr payı gider",
            "verilen kar pay",
            "verilen kâr pay",
        ]

        if any(term in metric_norm for term in bad_terms):
            return -999

        if table_norm == "bilanco":
            score += 120
        else:
            score -= 150

        if metric_norm == "mevduat":
            score += 160
        elif metric_norm.startswith("mevduat "):
            score += 140
        elif "toplanan fonlar" in metric_norm:
            score += 135
        elif "toplanan fon" in metric_norm:
            score += 120
        else:
            score += 60

        return score

    if intent == "toplanan_fonlar":
        is_toplanan_fon = (
            "toplanan fon" in metric_norm
            or "toplanan fonlar" in metric_norm
            or "katilim fon" in metric_norm
            or "katilma hesap" in metric_norm
        )

        if not is_toplanan_fon:
            return -999

        bad_terms = [
            "net artis",
            "net artış",
            "net azalis",
            "net azalış",
            "nakit akis",
            "nakit akış",
            "kar payi gider",
            "kâr payı gider",
            "verilen kar pay",
            "verilen kâr pay",
            "bankalardan toplanan fonlarda",
            "diger toplanan fonlarda",
            "diğer toplanan fonlarda",
        ]

        if any(term in metric_norm for term in bad_terms):
            return -999

        if table_norm == "bilanco":
            score += 150
        else:
            score -= 200

        if metric_norm == "toplanan fonlar":
            score += 180
        elif metric_norm.startswith("toplanan fonlar "):
            score += 160
        elif "toplanan fonlar" in metric_norm:
            score += 140
        elif "toplanan fon" in metric_norm:
            score += 120

        return score

    if intent == "ozkaynak":
        if "ozkaynak" not in metric_norm and "oz kaynak" not in metric_norm:
            return -999

        bad_terms = [
            "kar veya zararda yeniden siniflandirilacaklar",
            "diger kapsamli gelir",
            "yeniden degerleme",
            "nakit akis",
        ]

        if any(term in metric_norm for term in bad_terms):
            return -999

        if table_norm == "bilanco":
            score += 120
        else:
            score -= 100

        if metric_norm in {"ozkaynak", "ozkaynaklar"}:
            score += 160
        elif metric_norm.startswith("ozkaynaklar"):
            score += 150
        else:
            score += 80

        return score

    if intent == "yukumlulukler":
        is_passive_total = (
            "yukumlulukler ve ozkaynaklar" in metric_norm
            or "pasif toplami" in metric_norm
            or "borclar toplami" in metric_norm
        )

        if "yukumluluk" not in metric_norm and not is_passive_total:
            return -999

        if "bilanco disi" in metric_norm or table_norm == "nazim_hesaplar":
            return -999

        if table_norm == "bilanco":
            score += 140
        else:
            score -= 120

        if metric_norm == "yukumlulukler toplami":
            score += 180
        elif metric_norm.startswith("yukumlulukler toplami"):
            score += 170
        elif "yukumlulukler toplami" in metric_norm:
            score += 150
        elif is_passive_total:
            score += 80
        elif parent_norm == "yukumlulukler":
            score += 40

        return score

    if intent == "nakit":
        if "nakit" not in metric_norm and "merkez bankasi" not in metric_norm:
            return -999

        if "kredi" in metric_norm or "krediler" in metric_norm:
            return -999

        if table_norm == "bilanco":
            score += 100
        else:
            score -= 100

        if "nakit ve nakit benzerleri" in metric_norm:
            score += 140
        elif "nakit degerler" in metric_norm:
            score += 90
        elif "merkez bankasi" in metric_norm:
            score += 60

        return score

    if intent == "finansal_varliklar":
        if "finansal varliklar" not in metric_norm:
            return -999

        if table_norm == "bilanco":
            score += 100
        else:
            score -= 100

        if "finansal varliklar net" in metric_norm:
            score += 140
        else:
            score += 80

        return score

    if intent == "esas_faaliyet_kari":
        if "faaliyet" not in metric_norm or (
            "kar" not in metric_norm and "zarar" not in metric_norm
        ):
            return -999

        bad_terms = [
            "durdurulan faaliyet",
            "nakit akis",
            "varlik ve yukumluluklerdeki degisim",
            "beklenen zarar",
        ]

        if any(term in metric_norm for term in bad_terms):
            return -999

        if table_norm == "gelir_tablosu":
            score += 140
        else:
            score -= 120

        if metric_norm.startswith("net faaliyet kari"):
            score += 190
        elif "net faaliyet kari" in metric_norm:
            score += 170
        elif metric_norm.startswith("faaliyet brut kari"):
            score += 90
        elif "faaliyet kari" in metric_norm:
            score += 80

        return score

    return -999


def collect_numeric_candidates(
    session: Session,
    bank: str,
    intent: str,
    max_per_bank: int = 12,
    banks: list[str] | None = None,
) -> list[FinancialMetric]:
    query = session.query(FinancialMetric)

    if bank != "ALL":
        query = query.filter(FinancialMetric.ticker == bank)
    elif banks:
        query = query.filter(FinancialMetric.ticker.in_(banks))

    # Bir ticker için birden fazla dönem (Q1, Q2, ...) DB'de birikince, skor
    # eşitliğinde tie-break değere bakıyor, döneme değil; bu yüzden adayları
    # her ticker'ın en güncel raporuyla sınırlıyoruz.
    latest_tickers = [bank] if bank != "ALL" else banks
    latest_report_ids = get_latest_report_ids(session, tickers=latest_tickers)

    if latest_report_ids:
        query = query.filter(FinancialMetric.report_id.in_(latest_report_ids.values()))

    rows = query.all()

    grouped: dict[str, list[tuple[int, float, FinancialMetric]]] = {}
    min_score = MIN_SCORE_BY_INTENT.get(intent, 100)

    for row in rows:
        score = candidate_match_score(
            row.metric,
            intent,
            row.table_type,
            getattr(row, "parent_section", None),
        )

        if score < min_score:
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


def row_source(row: FinancialMetric, reason: str = "") -> dict[str, Any]:
    current_col, current_value = get_current_total_value(row.values_json)
    previous_col, previous_value = get_previous_total_value(row.values_json)

    return {
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


def format_computed_value(value: float, unit: str | None = "1.000 TL") -> str:
    amount = f"{int(round(value)):,}".replace(",", ".")

    if unit in {"1.000 TL", "1000 TL", "Bin TL", "bin TL"}:
        return f"{amount} bin TL"

    if unit:
        return f"{amount} {unit}"

    return amount


def values_close(left: float, right: float, tolerance: float = 0.5) -> bool:
    return abs(left - right) <= tolerance


def calculate_change_parts(
    current_value: str | None,
    previous_value: str | None,
) -> tuple[float | None, float | None, float | None, str | None]:
    current = parse_number(current_value)
    previous = parse_number(previous_value)

    if current is None or previous is None or previous == 0:
        return current, previous, None, None

    diff = current - previous
    pct = (diff / abs(previous)) * 100
    direction = "artış" if diff > 0 else "azalış" if diff < 0 else "değişim yok"

    return current, previous, pct, direction


def deterministic_select_numeric_candidates(
    intent: str,
    candidates: list[FinancialMetric],
) -> dict[str, Any]:
    best_by_ticker: dict[str, tuple[tuple[int, float, int, int], FinancialMetric]] = {}

    for row in candidates:
        score = candidate_match_score(
            row.metric,
            intent,
            row.table_type,
            getattr(row, "parent_section", None),
        )
        sort_key = (
            score,
            current_numeric_value(row),
            row.page or -1,
            row.id,
        )

        existing = best_by_ticker.get(row.ticker)

        if existing is None or sort_key > existing[0]:
            best_by_ticker[row.ticker] = (sort_key, row)

    return {
        "selections": [
            {
                "ticker": row.ticker,
                "candidate_id": row.id,
                "reason": "Skorlanan adaylar içinde en uygun ana finansal tablo satırı seçildi.",
            }
            for _, row in best_by_ticker.values()
        ]
    }


def select_best_rows(
    session: Session,
    bank: str,
    intent: str,
    banks: list[str] | None = None,
) -> list[FinancialMetric]:
    candidates = collect_numeric_candidates(
        session=session,
        bank=bank,
        intent=intent,
        max_per_bank=50,
        banks=banks,
    )

    selection_json = deterministic_select_numeric_candidates(intent, candidates)
    candidate_map = {row.id: row for row in candidates}
    rows: list[FinancialMetric] = []

    for item in selection_json.get("selections", []):
        candidate_id = item.get("candidate_id")

        if candidate_id in candidate_map:
            rows.append(candidate_map[candidate_id])

    return rows


def answer_unknown_metric_question(question: str, bank: str) -> AskResponse:
    bank_name = get_bank_name(bank)
    requested = "kripto varlıklar toplamı" if "kripto" in normalize_text(question) else "ilgili kalem"

    return AskResponse(
        answer=(
            f"{bank_name} için {requested} kalemine karşılık gelen güvenilir bir "
            "financial_metrics satırı bulunamadı. Bu durumda sistem yakın bir ana kaleme "
            "atlamadan, kalemin bulunamadığını söylemelidir."
        ),
        mode="numeric_unknown_metric",
        sources=[],
    )


def answer_ratio_question(
    session: Session,
    question: str,
    bank: str,
    banks: list[str] | None,
) -> AskResponse | None:
    q = strip_bank_names_for_metric_detection(question)

    is_ratio = (
        "yuzde kaci" in q
        or "yuzde kac" in q
        or "oran" in q
        or "olusturuyor" in q
    )

    if not (is_ratio and "kredi" in q and "varliklar toplami" in q):
        return None

    target_banks = banks or ([] if bank == "ALL" else [bank])

    if not target_banks:
        return None

    lines: list[str] = []
    sources: list[dict[str, Any]] = []

    for target_bank in target_banks:
        credit_rows = select_best_rows(session, target_bank, "krediler")
        asset_rows = select_best_rows(session, target_bank, "varliklar_toplami")

        if not credit_rows or not asset_rows:
            continue

        credit_row = credit_rows[0]
        asset_row = asset_rows[0]
        _, credit_value = get_current_total_value(credit_row.values_json)
        _, asset_value = get_current_total_value(asset_row.values_json)
        credit = parse_number(credit_value)
        assets = parse_number(asset_value)

        if credit is None or assets in (None, 0):
            continue

        pct = (credit / assets) * 100
        bank_name = get_bank_name(target_bank)

        lines.append(
            f"{bank_name}: {credit_row.metric} {format_value(credit_value, credit_row.unit)}, "
            f"{asset_row.metric} {format_value(asset_value, asset_row.unit)}; oran %{pct:.2f}."
        )
        sources.extend(
            [
                row_source(credit_row, "Oran hesabının payı olarak seçildi."),
                row_source(asset_row, "Oran hesabının paydası olarak seçildi."),
            ]
        )

    if not lines:
        return None

    return AskResponse(
        answer="\n".join(lines),
        mode="numeric_ratio",
        sources=sources,
    )


def answer_cross_bank_higher_question(
    session: Session,
    question: str,
    intent: str,
    banks: list[str] | None,
) -> AskResponse | None:
    q = normalize_text(question)

    if not banks or len(banks) < 2:
        return None

    if not (
        "hangisi daha" in q
        or "daha yuksek" in q
        or "daha dusuk" in q
        or "kim onde" in q
        or " mi " in f" {q} "
    ):
        return None

    rows = select_best_rows(
        session=session,
        bank="ALL",
        intent=intent,
        banks=banks,
    )

    if len(rows) < 2:
        return None

    is_lower_question = "daha dusuk" in q or "en dusuk" in q
    rows.sort(key=current_numeric_value, reverse=not is_lower_question)
    winner = rows[0]
    _, winner_value = get_current_total_value(winner.values_json)
    metric_label = winner.metric
    comparison_label = "daha düşük" if is_lower_question else "daha yüksek"

    lines = [
        (
            f"{get_bank_name(winner.ticker)} {comparison_label} {metric_label} değerine sahip: "
            f"{format_value(winner_value, winner.unit)}."
        )
    ]

    for row in rows:
        _, value = get_current_total_value(row.values_json)
        lines.append(f"- {get_bank_name(row.ticker)}: {format_value(value, row.unit)}")

    return AskResponse(
        answer="\n".join(lines),
        mode="numeric_cross_bank_compare",
        sources=[row_source(row, "Banka karşılaştırması için seçildi.") for row in rows],
    )


def answer_largest_asset_item(
    session: Session,
    bank: str,
    banks: list[str] | None,
) -> AskResponse | None:
    target_banks = banks or ([] if bank == "ALL" else [bank])

    if not target_banks:
        return None

    lines: list[str] = []
    sources: list[dict[str, Any]] = []

    for target_bank in target_banks:
        asset_total_rows = select_best_rows(session, target_bank, "varliklar_toplami")

        if not asset_total_rows:
            continue

        asset_total = asset_total_rows[0]
        rows = (
            session.query(FinancialMetric)
            .filter(
                FinancialMetric.report_id == asset_total.report_id,
                FinancialMetric.ticker == target_bank,
                FinancialMetric.table_type == "bilanco",
            )
            .all()
        )

        candidates: list[FinancialMetric] = []

        for row in rows:
            metric_norm = normalize_text(row.metric)

            if row.id >= asset_total.id:
                continue

            if row.parent_section:
                continue

            if "varliklar toplami" in metric_norm or "aktif toplami" in metric_norm:
                continue

            value = current_numeric_value(row)

            if value <= 0:
                continue

            candidates.append(row)

        if not candidates:
            continue

        candidates.sort(key=current_numeric_value, reverse=True)
        selected = candidates[0]
        _, selected_value = get_current_total_value(selected.values_json)

        lines.append(
            f"{get_bank_name(target_bank)} için varlık tarafındaki en büyük ana kalem "
            f"**{selected.metric}**: {format_value(selected_value, selected.unit)}."
        )
        sources.append(row_source(selected, "Varlık toplamı öncesindeki en yüksek ana bilanço kalemi."))

    if not lines:
        return None

    return AskResponse(
        answer="\n".join(lines),
        mode="numeric_largest_asset_item",
        sources=sources,
    )


def answer_balance_check_question(
    session: Session,
    question: str,
    bank: str,
    banks: list[str] | None,
) -> AskResponse | None:
    q = normalize_text(question)

    if not (
        ("bilanco dengesi" in q or "denge" in q)
        and "varlik" in q
        and "yukumluluk" in q
        and "ozkaynak" in q
    ):
        return None

    target_banks = banks or ([] if bank == "ALL" else [bank])

    if not target_banks:
        return None

    lines: list[str] = []
    sources: list[dict[str, Any]] = []

    for target_bank in target_banks:
        asset_rows = select_best_rows(session, target_bank, "varliklar_toplami")
        passive_rows = select_best_rows(session, target_bank, "yukumlulukler")
        equity_rows = select_best_rows(session, target_bank, "ozkaynak")

        if not asset_rows or not passive_rows or not equity_rows:
            continue

        asset_row = asset_rows[0]
        passive_row = passive_rows[0]
        equity_row = equity_rows[0]
        _, asset_value = get_current_total_value(asset_row.values_json)
        _, passive_value = get_current_total_value(passive_row.values_json)
        _, equity_value = get_current_total_value(equity_row.values_json)
        assets = parse_number(asset_value)
        passive_total = parse_number(passive_value)
        equity = parse_number(equity_value)

        if assets is None or passive_total is None or equity is None:
            continue

        passive_metric = normalize_text(passive_row.metric)
        label_says_combined_passive_total = (
            "yukumlulukler ve ozkaynaklar" in passive_metric
            or "pasif toplami" in passive_metric
        )

        if values_close(assets, passive_total):
            liability_basis = (
                "Seçilen satır değeri varlıklar toplamına eşit olduğu için pasif toplam "
                "(yükümlülükler + özkaynaklar) gibi değerlendirildi; özkaynak tekrar eklenmedi."
            )
            liabilities = passive_total - equity
            recomputed_total = liabilities + equity
        elif values_close(assets, passive_total + equity):
            liability_basis = (
                "Seçilen satır gerçek yükümlülük toplamı gibi değerlendirildi; "
                "üzerine özkaynak eklenerek bilanço denklemi kontrol edildi."
            )
            liabilities = passive_total
            recomputed_total = passive_total + equity
        elif label_says_combined_passive_total:
            liability_basis = (
                "Satır etiketi pasif toplamı gösterdiği için özkaynak tekrar eklenmedi; "
                "ancak değerler tam dengelenmedi."
            )
            liabilities = passive_total - equity
            recomputed_total = liabilities + equity
        else:
            lines.append(
                f"{get_bank_name(target_bank)} için bilanço dengesi hesap türü belirsiz: "
                f"seçilen **{passive_row.metric}** satırının gerçek yükümlülük mü yoksa "
                "pasif toplamı mı olduğu değerlerle güvenilir şekilde ayrıştırılamadı. "
                "Bu nedenle yanlış tutarsızlık sonucu üretilmedi."
            )
            sources.extend(
                [
                    row_source(asset_row, "Bilanço denklemi için varlık toplamı."),
                    row_source(passive_row, "Bilanço denklemi için yükümlülük/pasif satırı."),
                    row_source(equity_row, "Bilanço denklemi için özkaynak."),
                ]
            )
            continue

        diff = assets - recomputed_total
        is_balanced = values_close(assets, recomputed_total)
        status = "doğru kurulmuş" if is_balanced else "tutarsız görünüyor"

        lines.append(
            f"{get_bank_name(target_bank)} için bilanço dengesi {status}: "
            f"varlıklar toplamı {format_value(asset_value, asset_row.unit)}, "
            f"özkaynak hariç yükümlülük {format_computed_value(liabilities, passive_row.unit)}, "
            f"özkaynak {format_value(equity_value, equity_row.unit)}."
        )
        lines.append(
            f"Yükümlülük + özkaynak = {format_computed_value(recomputed_total, passive_row.unit)}. "
            f"{liability_basis}"
        )
        sources.extend(
            [
                row_source(asset_row, "Bilanço denklemi için varlık toplamı."),
                row_source(passive_row, "Bilanço pasif toplamı; özkaynak düşülerek yükümlülük hesaplandı."),
                row_source(equity_row, "Bilanço denklemi için özkaynak."),
            ]
        )

    if not lines:
        return None

    return AskResponse(
        answer="\n".join(lines),
        mode="numeric_balance_check",
        sources=sources,
    )


def answer_liabilities_question(
    session: Session,
    bank: str,
    banks: list[str] | None,
) -> AskResponse | None:
    target_banks = banks or ([] if bank == "ALL" else [bank])

    if not target_banks:
        return None

    lines: list[str] = []
    sources: list[dict[str, Any]] = []

    for target_bank in target_banks:
        passive_rows = select_best_rows(session, target_bank, "yukumlulukler")

        if not passive_rows:
            continue

        passive_row = passive_rows[0]
        current_col, current_value = get_current_total_value(passive_row.values_json)
        previous_col, previous_value = get_previous_total_value(passive_row.values_json)
        change_text = calculate_change_text(current_value, previous_value)

        line = (
            f"{get_bank_name(target_bank)} için rapordaki **{passive_row.metric}** satırı "
            f"{extract_column_period(current_col)} itibarıyla {format_value(current_value, passive_row.unit)}."
        )

        if previous_value:
            line += (
                f" Önceki dönem ({extract_column_period(previous_col)}) "
                f"{format_value(previous_value, passive_row.unit)}; {change_text}"
            )

        lines.append(line)
        sources.append(row_source(passive_row, "Yükümlülükler toplamı sorusu için seçildi."))

    if not lines:
        return None

    return AskResponse(
        answer="\n".join(lines),
        mode="numeric_liabilities",
        sources=sources,
    )


def answer_period_compare_question(
    session: Session,
    question: str,
    bank: str,
    intent: str,
    banks: list[str] | None,
) -> AskResponse | None:
    if bank == "ALL" or banks:
        return None

    q = normalize_text(question)
    triggers = [
        "hangi donemde",
        "karsilastir",
        "artmis mi",
        "azalmis mi",
        "nasil degismis",
        "degisim",
        "onceki doneme gore",
        "onceki yilin ayni donemine gore",
    ]

    if not any(trigger in q for trigger in triggers):
        return None

    rows = select_best_rows(session, bank, intent)

    if not rows:
        return None

    row = rows[0]
    current_col, current_value = get_current_total_value(row.values_json)
    previous_col, previous_value = get_previous_total_value(row.values_json)
    current, previous, pct, direction = calculate_change_parts(current_value, previous_value)

    if current is None or previous is None:
        return None

    current_period = extract_column_period(current_col) or "cari dönem"
    previous_period = extract_column_period(previous_col) or "önceki dönem"

    if current > previous:
        higher_text = f"{current_period} daha yüksektir"
    elif current < previous:
        higher_text = f"{previous_period} daha yüksektir"
    else:
        higher_text = "iki dönem değeri eşittir"

    answer = (
        f"{get_bank_name(bank)} için **{row.metric}** {current_period} döneminde "
        f"{format_value(current_value, row.unit)}, {previous_period} döneminde "
        f"{format_value(previous_value, row.unit)}. {higher_text}."
    )

    if pct is not None and direction:
        answer += f" Değişim: %{abs(pct):.2f} {direction}."

    answer += f" Kaynak: {row.table_type}, sayfa {row.page}."

    return AskResponse(
        answer=answer,
        mode="numeric_period_compare",
        sources=[row_source(row, "Cari ve önceki dönem karşılaştırması için seçildi.")],
    )


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

Kurallar:
1. "Krediler toplamı" sorulursa ana bilanço kalemi olan Krediler satırını seç.
   Doğruya örnekler: "Krediler", "Krediler (5)", "Krediler 5.1.5", "Krediler 1.7".
   Yanlışa örnekler: "Krediler ve İhraç Edilen Menkul Değerlerden Sağlanan Nakit",
   "Alınan krediler", "kredi riski", "beklenen zarar karşılıkları", "gayrinakdi krediler".
2. "Net kâr" sorulursa gelir tablosundaki dönem net kârı/zararı satırını seç.
3. "Varlıklar toplamı / aktif toplamı" sorulursa bilanço tablosundaki VARLIKLAR TOPLAMI satırını seç.
4. "Mevduat" sorulursa bilanço pasifindeki ana MEVDUAT satırını seç.
5. "Özkaynak" sorulursa ana ÖZKAYNAKLAR satırını seç.
6. Aynı metrik birden fazla tabloda geçerse bilanço/gelir tablosu ana kalemini, nakit akış satırlarına tercih et.
7. "Krediler toplamı" sorularında "SERMAYE BENZERİ BORÇLANMA ARAÇLARI" veya "YÜKÜMLÜLÜKLER" altında geçen "Krediler" satırını seçme. Bu ana kredi kalemi değildir. Ana kredi kalemi genellikle VARLIKLAR tarafındaki "İTFA EDİLMİŞ MALİYETİ İLE ÖLÇÜLEN FİNANSAL VARLIKLAR" altında yer alan "Krediler" satırıdır.
8. Katılım bankalarında "mevduat" veya "toplanan fonlar" sorulursa bilanço tablosundaki "TOPLANAN FONLAR" satırını seç. Nakit akış tablosundaki "toplanan fonlarda net artış/azalış" satırlarını seçme. 

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

    data = safe_json_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        default={"selections": []},
    )

    if not isinstance(data, dict) or not isinstance(data.get("selections"), list):
        return {"selections": []}

    return data


def build_numeric_answer_from_selection(
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
                "Aday satırlar arasından güvenilir bir seçim yapılamadı. "
                "Adayları genişletmemiz veya eşleştirme kurallarını güncellememiz gerekebilir."
            ),
            mode="numeric_metric_match",
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
            mode="numeric_metric_match",
            sources=sources,
        )

    row, reason = selected_rows[0]
    current_col, current_value = get_current_total_value(row.values_json)
    previous_col, previous_value = get_previous_total_value(row.values_json)

    bank_name = get_bank_name(bank)

    answer = (
        f"{bank_name} için {row.period} döneminde **{row.metric}** kaleminin "
        f"cari toplam değeri **{format_value(current_value, row.unit)}** olarak görünüyor."
    )

    if previous_value:
        answer += f"\nÖnceki dönem toplam değeri ise **{format_value(previous_value, row.unit)}**."

        change_text = calculate_change_text(current_value, previous_value)

        if change_text:
            answer += f"\nDeğişim: {change_text}"

    answer += f"\nKaynak: {row.table_type}, sayfa {row.page}."

    if reason:
        answer += f"\nEşleştirme gerekçesi: {reason}"

    return AskResponse(
        answer=answer,
        mode="numeric_metric_match",
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


def answer_numeric_question(
    session: Session,
    question: str,
    bank: str,
    intent: str,
    banks: list[str] | None = None,
) -> AskResponse:
    if intent == "unknown_metric":
        return answer_unknown_metric_question(question, bank)

    special_answer = (
        answer_ratio_question(session, question, bank, banks)
        or answer_balance_check_question(session, question, bank, banks)
        or answer_cross_bank_higher_question(session, question, intent, banks)
    )

    if special_answer:
        return special_answer

    if intent == "largest_asset_item":
        largest_answer = answer_largest_asset_item(session, bank, banks)

        if largest_answer:
            return largest_answer

    if intent == "yukumlulukler":
        liabilities_answer = answer_liabilities_question(session, bank, banks)

        if liabilities_answer:
            return liabilities_answer

    period_compare_answer = answer_period_compare_question(
        session=session,
        question=question,
        bank=bank,
        intent=intent,
        banks=banks,
    )

    if period_compare_answer:
        return period_compare_answer

    candidates = collect_numeric_candidates(
        session=session,
        bank=bank,
        intent=intent,
        max_per_bank=12,
        banks=banks,
    )

    if not candidates:
        return AskResponse(
            answer="Bu soru için financial_metrics tablosunda aday sayısal satır bulamadım.",
            mode="numeric_no_candidates",
            sources=[],
        )

    selection_json = deterministic_select_numeric_candidates(
        intent=intent,
        candidates=candidates,
    )

    return build_numeric_answer_from_selection(
        bank=bank,
        candidates=candidates,
        selection_json=selection_json,
    )
