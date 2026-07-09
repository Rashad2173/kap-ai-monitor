from __future__ import annotations

import re

from app.services.bank_registry import bank_alias_pattern, get_bank_strip_aliases
from app.utils.text_utils import normalize_text


UNKNOWN_METRIC_ALIASES = [
    "kripto",
    "kripto varlik",
    "kripto varliklar",
    "kripto varliklar toplami",
    "dijital varlik",
    "dijital varliklar",
    "dijital varliklar toplami",
]


METRIC_ALIASES = {
    "largest_asset_item": [
        "bilancosunda varlik tarafindaki en buyuk",
        "varlik tarafindaki en buyuk ana kalem",
        "varlik tarafindaki en buyuk",
        "varlik tarafinda en buyuk",
        "en buyuk ana kalem",
        "en buyuk kalem",
    ],
    "nakit": [
        "nakit ve nakit benzerleri",
        "nakit degerler",
        "merkez bankasi",
    ],
    "net_kar": [
        "son 3 ay kar",
        "son uc ay kar",
        # "net donem kar" kökü hem tekil ("net donem kari") hem de çoğul/iyelik
        # ("net donem karlari", "net donem karlarini") biçimleri yakalar.
        "net donem kar",
        "net donem kari",
        "donem net kar",
        "donem net kari",
        "net kar",
        "kar zarar",
        "kari ne kadar",
    ],
    "esas_faaliyet_kari": [
        "esas faaliyet kari",
        "net faaliyet kari",
        "faaliyet kari",
    ],
    "yukumlulukler": [
        "yukumlulukler toplami",
        "yukumlulukler ve ozkaynaklar toplami",
        "toplam yukumlulukler",
        "toplam yukumlulukleri",
        "yukumlulukleri",
        "yukumlulukler",
        "borclar toplami",
        "pasif toplami",
    ],
    "varliklar_toplami": [
        "varliklar toplami",
        "varliklar toplamina",
        "varliklar toplamini",
        "varliklar toplamindan",
        "varlik toplami",
        "varlik toplamina",
        "aktif toplami",
        "toplam aktif",
        "aktifler",
    ],
    "ozkaynak": [
        "ozkaynak",
        "oz kaynak",
        "ozkaynaklar",
        "toplam ozkaynak",
    ],
    "mevduat": [
        "toplam mevduat",
        "mevduat",
    ],
    "toplanan_fonlar": [
        "toplanan fonlar toplami",
        "toplanan fonlar",
        "toplanan fon",
        "katilim fonu",
        "katilim fonlari",
        "katilma hesaplari",
        "albaraka mevduat",
        "katilim bankasi mevduat",
    ],
    "finansal_varliklar": [
        "finansal varliklar net",
        "finansal varliklar",
    ],
    "krediler": [
        "krediler toplami",
        "toplam kredi",
        "krediler",
    ],
}


def strip_bank_aliases(question_norm: str) -> str:
    cleaned = f" {question_norm} "

    aliases = [normalize_text(alias) for alias in get_bank_strip_aliases()]

    for alias in sorted(set(aliases), key=len, reverse=True):
        if not alias:
            continue

        cleaned = re.sub(bank_alias_pattern(alias), " ", cleaned)

    cleaned = re.sub(r"\s+", " ", cleaned)

    return cleaned.strip()


def strip_bank_names_for_metric_detection(question: str) -> str:
    return strip_bank_aliases(normalize_text(question))


def has_alias(question_norm: str, aliases: list[str]) -> bool:
    return any(normalize_text(alias) in question_norm for alias in aliases)


def is_ratio_question(question: str) -> bool:
    q = strip_bank_names_for_metric_detection(question)
    ratio_terms = ["yuzde kaci", "yuzde kac", "oran", "orani", "olusturuyor", "payi"]
    has_credit = "krediler" in q or "toplam kredi" in q or "kredi" in q
    has_assets = "varliklar toplami" in q or "aktif toplami" in q

    return any(term in q for term in ratio_terms) and has_credit and has_assets


def is_balance_check_question(question: str) -> bool:
    q = strip_bank_names_for_metric_detection(question)

    return (
        ("bilanco dengesi" in q or "denge" in q)
        and "varlik" in q
        and "yukumluluk" in q
        and "ozkaynak" in q
    )


def detect_metric_intent(question: str) -> str | None:
    q = strip_bank_names_for_metric_detection(question)

    if has_alias(q, UNKNOWN_METRIC_ALIASES):
        return "unknown_metric"

    if is_ratio_question(question):
        return "krediler"

    for intent, aliases in METRIC_ALIASES.items():
        for alias in aliases:
            if normalize_text(alias) in q:
                return intent

    return None


def is_numeric_question(question: str) -> bool:
    q = strip_bank_names_for_metric_detection(question)

    if is_ratio_question(question) or is_balance_check_question(question):
        return True

    # NOT: "hangi sayfa" / "hangi finansal tablo" buradan çıkarıldı; bunlar
    # konum (document_location) sinyalidir, sayısal değer sorusu değildir.
    numeric_words = [
        "kac",
        "ne kadar",
        "tutar",
        "toplam",
        "toplami",
        "kar",
        "krediler",
        "mevduat",
        "varlik",
        "aktif",
        "ozkaynak",
        "yukumluluk",
        "oran",
        "yuzde",
        "degisim",
        "degeri",
        "karsilastir",
        "daha yuksek",
        "daha dusuk",
        "en buyuk",
    ]

    return any(normalize_text(word) in q for word in numeric_words)


def is_summary_question(question: str) -> bool:
    q = strip_bank_names_for_metric_detection(question)

    # "finansal durum tablosu" bilançonun resmi adıdır; özet sorusu değildir.
    if "finansal durum tablo" in q:
        return False

    summary_words = [
        "ozet",
        "ozetle",
        "genel durum",
        "genel ozet",
        "raporu ozet",
        "q1 ozet",
        "finansal durumu",
        "performansini ozet",
        "kisa analiz",
        "genel analiz",
        "yonetici ozeti",
    ]

    return any(normalize_text(word) in q for word in summary_words)


def is_comparison_question(question: str) -> bool:
    q = strip_bank_names_for_metric_detection(question)

    comparison_words = [
        "karsilastir",
        "rakip",
        "rakiplerine gore",
        "sirala",
        "siralama",
        "en yuksek",
        "en buyuk",
        "en dusuk",
        "hangisi daha",
        "daha yuksek",
        "daha dusuk",
        "kim onde",
    ]

    return any(normalize_text(word) in q for word in comparison_words)


def is_report_metadata_question(question: str) -> bool:
    q = strip_bank_names_for_metric_detection(question)

    # NOT: Yalın "konsolide/solo finansal tablo" ifadeleri buradan çıkarıldı.
    # Aksi halde "konsolide finansal tablodan varlıklar toplamı" gibi sayısal
    # sorular yanlışlıkla metadata'ya gidiyordu. Burada yalnızca raporun
    # niteliğini/türünü/tarihini soran kalıpları yakalıyoruz.
    metadata_words = [
        "kap bildirim turu",
        "bildirim turu",
        "hangi tarihte yayimlan",
        "kap ta hangi tarihte",
        "kapta hangi tarihte",
        "yayin tarihi",
        "yayimlanma tarihi",
        "yayimlandigi tarih",
        "konsolide mi",
        "solo mu",
        "konsolide mi solo mu",
        "finansal tablo tipi",
        "finansal tablo niteligi",
        "konsolide olmayan",
        "consolidated",
        "unconsolidated",
    ]

    return any(normalize_text(word) in q for word in metadata_words)


def is_period_date_question(question: str) -> bool:
    q = strip_bank_names_for_metric_detection(question)

    asks_date = (
        "hangi tarih" in q
        or "tarihlere" in q
        or "karsilik geliyor" in q
        or "hangi gune" in q
        or "tarih araligi" in q
    )

    mentions_period = "cari donem" in q or "onceki donem" in q or "donem" in q

    # "dönem" tek başına çok genel olduğundan mutlaka bir tarih sorusuyla birlikte
    # olmalı. Yayın tarihi soruları is_report_metadata tarafından önce yakalanır.
    return asks_date and mentions_period


def is_period_dates_question(question: str) -> bool:
    return is_period_date_question(question)


def is_document_location_question(question: str) -> bool:
    q = strip_bank_names_for_metric_detection(question)

    location_words = [
        "hangi bolum",
        "hangi sayfa",
        "hangi finansal tablo",
        "hangi tablo",
        "sayfadan alin",
        "nerede",
        "yer aliyor",
        "yer almakta",
        "bulunuyor",
    ]
    document_terms = [
        "muhasebe politika",
        "onemli muhasebe",
        "aciklama",
        "dipnot",
        "bolum",
        "finansal tablo",
        "tablo",
        "varliklar toplami",
        "net donem kari",
        "krediler",
        "nakit ve nakit benzerleri",
    ]

    return any(word in q for word in location_words) and any(
        term in q for term in document_terms
    )


def is_risk_audit_question(question: str) -> bool:
    q = strip_bank_names_for_metric_detection(question)

    risk_words = [
        "denetci",
        "denetim",
        "sartli",
        "olumlu gorus",
        "dikkat cekilen",
        "risk",
        "husus",
        "suphe",
    ]

    return any(normalize_text(word) in q for word in risk_words)


def detect_question_type(question: str) -> str:
    metric_intent = detect_metric_intent(question)

    if is_summary_question(question):
        return "executive_summary"

    if is_report_metadata_question(question):
        return "report_metadata"

    if is_period_dates_question(question):
        return "period_dates"

    # Konum sorusu (hangi sayfa / hangi bölüm / nerede) sayısal değerden ÖNCE
    # değerlendirilir. is_document_location_question zaten bir konum kelimesi
    # şart koştuğu için "krediler ne kadar" gibi saf değer sorularını çalmaz.
    if is_document_location_question(question):
        return "document_location"

    if metric_intent and (
        metric_intent == "unknown_metric"
        or is_numeric_question(question)
        or is_comparison_question(question)
    ):
        return "numeric_metric"

    if is_comparison_question(question):
        return "comparison"

    if is_risk_audit_question(question):
        return "risk_audit"

    return "general_rag"