from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalize_text(text: str | None) -> str:
    if not text:
        return ""

    text = str(text)
    text = text.replace("İ", "i").replace("I", "i").replace("ı", "i")
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))

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
            "â": "a",
            "Â": "a",
            "î": "i",
            "Î": "i",
            "û": "u",
            "Û": "u",
        }
    )

    text = text.translate(tr_map)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def parse_number(value: Any | None) -> float | None:
    """Türk finansal tablo formatındaki bir değeri float'a çevirir.

    Güçlendirmeler:
    - Zaten sayısal (int/float) gelen değerleri doğrudan döndürür.
    - Parantezli negatifleri ``(1.234)`` => -1234 olarak çözer.
    - Baştaki/sondaki eksi işaretini ( ``-1.234`` / ``1.234-`` ) yakalar.
    - Para birimi/yüzde gibi metinleri ve boşlukları temizler.
    - Türk biçimini varsayar: '.' binlik ayraç, ',' ondalık ayraç.
    """
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    cleaned = str(value).strip()

    if not cleaned:
        return None

    negative = False

    # Parantez içi negatif gösterim: (1.234)
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative = True
        cleaned = cleaned[1:-1].strip()

    # Sondaki eksi: 1.234-
    if cleaned.endswith("-"):
        negative = True
        cleaned = cleaned[:-1].strip()

    # Baştaki eksi: -1.234
    if cleaned.startswith("-"):
        negative = True
        cleaned = cleaned[1:].strip()

    # Sadece rakam, '.' ve ',' bırak (TL, %, boşluk vb. atılır).
    cleaned = re.sub(r"[^\d.,]", "", cleaned)

    if not cleaned:
        return None

    cleaned = cleaned.replace(".", "")
    cleaned = cleaned.replace(",", ".")

    try:
        result = float(cleaned)
    except ValueError:
        return None

    return -result if negative else result


def format_value(value: str | None, unit: str | None = "1.000 TL") -> str:
    if not value:
        return "-"

    if unit in {"1.000 TL", "1000 TL", "Bin TL", "bin TL"}:
        return f"{value} bin TL"

    if unit:
        return f"{value} {unit}"

    return value


def _columns(values_json: dict[str, Any]) -> list[tuple[str, Any]]:
    if not isinstance(values_json, dict):
        return []

    return list(values_json.items())


def _stringify(value: Any) -> str | None:
    if value is None:
        return None

    return str(value)


def _label_date_key(label: str | None) -> tuple[int, int, int] | None:
    """Kolon başlığından sıralanabilir bir tarih anahtarı üretir.

    normalize_text noktayı boşluğa çevirdiği için "31.03.2026" => "31 03 2026"
    olur; bu yüzden gün/ay/yıl üçlüsünü boşlukla ayrılmış olarak arıyoruz.
    Bulunamazsa yalnızca yılı (varsa) kullanıyoruz.
    """
    norm = normalize_text(label)

    if not norm:
        return None

    match = re.search(r"\b(\d{1,2})\s+(\d{1,2})\s+(\d{4})\b", norm)

    if match:
        day, month, year = (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
        return (year, month, day)

    year_match = re.search(r"\b(\d{4})\b", norm)

    if year_match:
        return (int(year_match.group(1)), 0, 0)

    return None


def get_current_total_value(values_json: dict[str, Any]) -> tuple[str | None, str | None]:
    cols = _columns(values_json)

    if not cols:
        return None, None

    # 1) "cari" + "toplam" içeren kolon.
    for key, value in cols:
        key_norm = normalize_text(key)

        if "cari" in key_norm and "toplam" in key_norm:
            return key, _stringify(value)

    # 2) "cari" içeren kolon.
    for key, value in cols:
        if "cari" in normalize_text(key):
            return key, _stringify(value)

    # 3) Etiketler "cari/önceki" yerine tarih ise: en yeni tarihli kolon.
    dated = [
        (key, value, _label_date_key(key))
        for key, value in cols
    ]
    dated = [item for item in dated if item[2] is not None]

    if dated:
        key, value, _ = max(dated, key=lambda item: item[2])
        return key, _stringify(value)

    # 4) Son çare: ilk kolon. (Sıra garantisi olmadığı için güvenilirliği düşük.)
    key, value = cols[0]
    return key, _stringify(value)


def get_previous_total_value(values_json: dict[str, Any]) -> tuple[str | None, str | None]:
    cols = _columns(values_json)

    if not cols:
        return None, None

    # 1) "onceki" + "toplam" içeren kolon.
    for key, value in cols:
        key_norm = normalize_text(key)

        if "onceki" in key_norm and "toplam" in key_norm:
            return key, _stringify(value)

    # 2) "onceki" içeren kolon.
    for key, value in cols:
        if "onceki" in normalize_text(key):
            return key, _stringify(value)

    # 3) Tarihli etiketler: en eski tarihli kolon (en az iki tarih varsa).
    dated = [
        (key, value, _label_date_key(key))
        for key, value in cols
    ]
    dated = [item for item in dated if item[2] is not None]

    if len(dated) >= 2:
        dated.sort(key=lambda item: item[2])
        key, value, _ = dated[0]
        return key, _stringify(value)

    # 4) Tam iki kolon varsa: cari olarak seçilmeyen diğer kolonu önceki kabul et.
    if len(cols) == 2:
        current_key, _ = get_current_total_value(values_json)

        for key, value in cols:
            if key != current_key:
                return key, _stringify(value)

    return None, None


def calculate_change_text(current_value: str | None, previous_value: str | None) -> str:
    current = parse_number(current_value)
    previous = parse_number(previous_value)

    if current is None or previous is None:
        return "Önceki dönemle karşılaştırma yapılamadı."

    if previous == 0:
        return "Önceki dönem değeri 0 olduğu için yüzde değişim hesaplanmadı."

    diff = current - previous
    pct = (diff / abs(previous)) * 100

    direction = "artış" if diff > 0 else "azalış" if diff < 0 else "değişim yok"

    return f"Önceki döneme göre {abs(pct):.2f}% {direction}."