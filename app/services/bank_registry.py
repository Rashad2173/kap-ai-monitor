from __future__ import annotations

import re

from app.utils.text_utils import normalize_text


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


BANK_ALIASES = {
    "AKBNK": ["akbank", "akbnk"],
    "GARAN": ["garanti", "garan", "garanti bbva"],
    "VAKBN": ["vakifbank", "vakif bank", "vakbn", "vakıfbank", "vakıf bank"],
    "ISCTR": ["is bankasi", "isbank", "isctr", "iş bankası", "işbank"],
    "YKBNK": ["yapi kredi", "yapikredi", "ykbnk", "yapı kredi"],
    "HALKB": ["halkbank", "halk bankasi", "halk bankası", "halkb"],
    "ALBRK": ["albaraka", "albaraka turk", "albrk", "albaraka türk"],
    "TSKB": ["tskb"],
    "SKBNK": ["sekerbank", "şekerbank", "skbnk"],
    "ICBCT": ["icbc", "icbct"],
    "KLNMA": ["kalkinma", "kalkınma", "klnma"],
    "QNBTR": ["qnb", "qnbtr", "qnb turkiye", "qnb türkiye"],
}


BANK_COMPANY_ALIASES = {
    "AKBNK": [
        "akbank t.a.s.",
        "akbank tas",
        "akbank turk anonim sirketi",
    ],
    "GARAN": [
        "turkiye garanti bankasi a.s.",
        "turkiye garanti bankasi",
        "garanti bankasi a.s.",
        "garanti bankasi",
    ],
    "VAKBN": [
        "turkiye vakiflar bankasi t.a.o.",
        "turkiye vakiflar bankasi",
        "vakiflar bankasi",
        "vakif bankasi",
    ],
    "ISCTR": [
        "turkiye is bankasi a.s.",
        "turkiye is bankasi",
        "is bankasi a.s.",
        "is bankasi",
    ],
    "YKBNK": [
        "yapi ve kredi bankasi a.s.",
        "yapi ve kredi bankasi",
        "yapi kredi bankasi a.s.",
        "yapi kredi bankasi",
        "yapi ve kredi",
        "yapi kredi",
        "yapikredi",
    ],
    "HALKB": [
        "turkiye halk bankasi a.s.",
        "turkiye halk bankasi",
        "halk bankasi a.s.",
        "halk bankasi",
    ],
    "ALBRK": [
        "albaraka turk katilim bankasi a.s.",
        "albaraka turk katilim bankasi",
        "albaraka turk",
    ],
    "TSKB": [
        "turkiye sinai kalkinma bankasi a.s.",
        "turkiye sinai kalkinma bankasi",
        "sinai kalkinma bankasi",
    ],
    "SKBNK": [
        "sekerbank t.a.s.",
        "sekerbank tas",
    ],
    "ICBCT": [
        "icbc turkey bank a.s.",
        "icbc turkey bank",
        "icbc turkey",
    ],
    "KLNMA": [
        "turkiye kalkinma ve yatirim bankasi a.s.",
        "turkiye kalkinma ve yatirim bankasi",
        "kalkinma ve yatirim bankasi",
    ],
    "QNBTR": [
        "qnb bank a.s.",
        "qnb bank",
        "qnb turkiye",
    ],
}


_BANK_ALIAS_SUFFIXES = (
    "larindan",
    "lerinden",
    "larinin",
    "lerinin",
    "larina",
    "lerine",
    "ndan",
    "nden",
    "ntan",
    "nten",
    "nin",
    "nun",
    "dan",
    "den",
    "tan",
    "ten",
    "na",
    "ne",
    "ya",
    "ye",
    "yi",
    "yu",
    "ni",
    "nu",
    "da",
    "de",
    "ta",
    "te",
    "in",
    "un",
    "i",
    "u",
    "a",
    "e",
)


def bank_alias_pattern(alias_norm: str) -> str:
    suffix_pattern = "|".join(_BANK_ALIAS_SUFFIXES)

    return rf"\b{re.escape(alias_norm)}(?:{suffix_pattern})?\b"


def get_bank_detection_aliases(ticker: str) -> list[str]:
    aliases = [
        *BANK_COMPANY_ALIASES.get(ticker, []),
        *BANK_ALIASES.get(ticker, []),
    ]

    return sorted(set(aliases), key=lambda alias: len(normalize_text(alias)), reverse=True)


def get_bank_strip_aliases() -> list[str]:
    aliases: list[str] = []

    for bank_aliases in BANK_ALIASES.values():
        aliases.extend(bank_aliases)

    for company_aliases in BANK_COMPANY_ALIASES.values():
        aliases.extend(company_aliases)

    return sorted(set(aliases), key=lambda alias: len(normalize_text(alias)), reverse=True)


def clean_bank(bank: str | None) -> str:
    if not bank:
        return "ALL"

    bank = bank.upper().strip()

    if bank not in BANK_NAMES:
        return "ALL"

    return bank


def get_bank_name(bank: str) -> str:
    return BANK_NAMES.get(bank, bank)


def detect_bank_from_question(question: str) -> str | None:
    q = normalize_text(question)

    for ticker in BANK_ALIASES:
        for alias in get_bank_detection_aliases(ticker):
            alias_norm = normalize_text(alias)
            if alias_norm and re.search(bank_alias_pattern(alias_norm), q):
                return ticker

    return None

def detect_banks_from_question(question: str) -> list[str]:
    q = normalize_text(question)

    found: list[str] = []

    for ticker in BANK_ALIASES:
        for alias in get_bank_detection_aliases(ticker):
            alias_norm = normalize_text(alias)
            if alias_norm and re.search(bank_alias_pattern(alias_norm), q):
                if ticker not in found:
                    found.append(ticker)
                break

    return found
