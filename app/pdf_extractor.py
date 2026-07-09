from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pdfplumber


# ============================================================
# Ayarlar
# ============================================================

TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "edge_min_length": 3,
    "min_words_vertical": 2,
    "min_words_horizontal": 1,
    "intersection_tolerance": 5,
    "text_x_tolerance": 2,
    "text_y_tolerance": 2,
}

NUMBER_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})*(?:,\d+)?$|^-?\d+(?:,\d+)?$")
DATE_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}")

TABLE_TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("KAR VEYA ZARAR VE DİĞER KAPSAMLI", "kapsamli_gelir_tablosu"),
    ("KAR VEYA ZARAR TABLOSU", "gelir_tablosu"),
    ("NAKİT AKIŞ TABLOSU", "nakit_akis_tablosu"),
    ("ÖZKAYNAKLAR DEĞİŞİM TABLOSU", "ozkaynaklar_tablosu"),
    ("NAZIM HESAPLAR", "nazim_hesaplar"),
    ("BİLANÇO DIŞI", "nazim_hesaplar"),
    ("GARANTİ VE KEFALETLER", "nazim_hesaplar"),
    ("TAAHHÜTLER", "nazim_hesaplar"),
    ("TÜREV FİNANSAL ARAÇLAR", "turev_araclar"),
    ("EMANET", "nazim_hesaplar"),
    ("REHİNLİ", "nazim_hesaplar"),
    ("VARLIKLAR", "bilanco"),
    ("YÜKÜMLÜLÜKLER", "bilanco"),
    ("ÖZKAYNAKLAR", "bilanco"),
]

INCOME_CONTINUATION_SIGNALS = [
    "DURDURULAN FAALİYET",
    "DÖNEM NET KARI",
    "HİSSE BAŞINA",
    "AZINLIK PAYLARI",
    "GRUBUN KARI",
]

PERIOD_PATTERNS = [
    (r"(\d{4})\s*-\s*1\.\s*3\s*Aylık", "{year}_Q1"),
    (r"(\d{4})\s*-\s*2\.\s*3\s*Aylık", "{year}_Q2"),
    (r"(\d{4})\s*-\s*3\.\s*3\s*Aylık", "{year}_Q3"),
    (r"(\d{4})\s*-\s*4\.\s*3\s*Aylık", "{year}_YIL"),
    (r"(\d{4})\s*Yıllık", "{year}_YIL"),
]


# ============================================================
# Genel yardımcılar
# ============================================================

def _clean_cell(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)
    text = text.replace("\x00", " ")
    text = text.replace("\r\n", " ")
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def _clean_text(text: str | None) -> str:
    if not text:
        return ""

    text = text.replace("\x00", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _extract_dates_from_text(text: str) -> list[str]:
    found = DATE_RE.findall(text)
    result: list[str] = []
    seen: set[str] = set()

    for item in found:
        if item not in seen:
            seen.add(item)
            result.append(item)

    return result


def _parse_period(text: str) -> str:
    for pattern, fmt in PERIOD_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if match:
            return fmt.format(year=match.group(1))

    match = re.search(r"(20\d{2})", text)

    if match:
        return match.group(1)

    return "BILINMIYOR"


def _period_dates(period: str, table_type: str) -> tuple[str, str]:
    """
    Header olmayan devam sayfalarında dönem tarihlerini tahmin etmek için.
    Bilanço: cari tarih / önceki yıl sonu.
    Gelir, nakit akış: cari dönem aralığı / önceki yıl aynı dönem.
    """
    match = re.match(r"^(20\d{2})_(Q1|Q2|Q3|YIL)$", period or "")

    if not match:
        return "cari_donem", "onceki_donem"

    year = int(match.group(1))
    code = match.group(2)

    if code == "Q1":
        end = "31.03"
    elif code == "Q2":
        end = "30.06"
    elif code == "Q3":
        end = "30.09"
    else:
        end = "31.12"

    if table_type in ("bilanco", "nazim_hesaplar", "turev_araclar"):
        return f"{end}.{year}", f"31.12.{year - 1}"

    return (
        f"01.01.{year} - {end}.{year}",
        f"01.01.{year - 1} - {end}.{year - 1}",
    )


def _safe_jsonl_write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ============================================================
# Tablo temizleme
# ============================================================

def _normalize_table(raw: list[list[Any]]) -> list[list[str]]:
    rows: list[list[str]] = []

    for row in raw:
        cleaned = [_clean_cell(cell) for cell in row]

        if any(cleaned):
            rows.append(cleaned)

    if not rows:
        return []

    max_cols = max(len(row) for row in rows)
    rows = [row + [""] * (max_cols - len(row)) for row in rows]

    keep_cols = [
        col_index
        for col_index in range(max_cols)
        if any(row[col_index] for row in rows)
    ]

    return [[row[col_index] for col_index in keep_cols] for row in rows]


def _is_useful_table(table: list[list[str]]) -> bool:
    if len(table) < 2:
        return False

    if max(len(row) for row in table) < 2:
        return False

    number_count = sum(
        1
        for row in table
        for cell in row
        if NUMBER_RE.match(cell)
    )

    return number_count >= 2


def _is_fragmented_table(table: list[list[str]]) -> bool:
    """
    'Bilanç | o | F | ina | ns | al' gibi parçalanmış duplicate tabloları eler.
    Geniş ama düzgün özkaynak tablosunu yanlışlıkla silmemek için sadece
    çok kısa kelime parçalarının oranına bakıyoruz.
    """
    if not table:
        return True

    col_count = max(len(row) for row in table)

    non_empty_cells: list[str] = [
        cell.strip()
        for row in table
        for cell in row
        if cell and cell.strip()
    ]

    if not non_empty_cells:
        return True

    short_alpha_cells = [
        cell
        for cell in non_empty_cells
        if len(cell) <= 2
        and any(ch.isalpha() for ch in cell)
        and not NUMBER_RE.match(cell)
    ]

    short_ratio = len(short_alpha_cells) / max(len(non_empty_cells), 1)

    if col_count >= 12 and short_ratio >= 0.20:
        return True

    if col_count >= 16 and short_ratio >= 0.14:
        return True

    return False


def _table_signature(table: list[list[str]]) -> str:
    """
    Aynı tablonun tekrarını yakalamak için ilk anlamlı sayısal satırlardan imza üretir.
    """
    sig_rows: list[str] = []

    for row in table:
        numbers = [cell for cell in row if NUMBER_RE.match(cell)]
        label = " ".join(cell for cell in row[:3] if cell)

        if numbers:
            sig_rows.append(label + "|" + "|".join(numbers[:4]))

        if len(sig_rows) >= 5:
            break

    return "\n".join(sig_rows)


def _dedupe_tables(tables: list[list[list[str]]]) -> list[list[list[str]]]:
    clean_tables: list[list[list[str]]] = []
    seen: set[str] = set()

    for table in tables:
        if not _is_useful_table(table):
            continue

        if _is_fragmented_table(table):
            continue

        signature = _table_signature(table)

        if signature and signature in seen:
            continue

        if signature:
            seen.add(signature)

        clean_tables.append(table)

    return clean_tables


# ============================================================
# Metadata
# ============================================================

def _parse_metadata(pages: list[dict]) -> dict:
    first_page_text = pages[0]["text"] if pages else ""

    company = None
    pub_date = None
    stmt_type = None
    period = _parse_period(first_page_text)

    for line in first_page_text.splitlines():
        line = line.strip()

        if "KAP'ta yayınlanma tarihi" in line:
            pub_date = line.split(":", 1)[-1].strip()

        if line.endswith(("A.Ş.", "T.A.Ş.", "T.A.O.")):
            company = line

        if "Konsolide Olmayan" in line:
            stmt_type = "Konsolide Olmayan"
        elif line == "Konsolide":
            stmt_type = "Konsolide"

    return {
        "ticker": "BILINMIYOR",
        "company": company or "BILINMIYOR",
        "period": period,
        "pub_date": pub_date,
        "stmt_type": stmt_type or "Konsolide Olmayan",
        "unit": "1.000 TL",
    }


def _parse_audit(pages: list[dict]) -> dict:
    for page in pages:
        text = page["text"]

        if "Bağımsız Denetçi Görüşü" not in text and "SINIRLI DENETİM" not in text:
            continue

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        result: dict[str, str] = {}

        for index, line in enumerate(lines):
            if line == "Bağımsız Denetim Kuruluşu" and index + 1 < len(lines):
                result["audit_company"] = lines[index + 1]

            if line == "Denetim Türü" and index + 1 < len(lines):
                result["audit_type"] = lines[index + 1]

            if line == "Denetim Sonucu" and index + 1 < len(lines):
                result["audit_result"] = lines[index + 1]

        if result:
            return result

    return {}


# ============================================================
# Tablo tipi
# ============================================================

def _detect_table_type(
    page_text: str,
    first_rows: list[list[str]],
    previous_table_type: str | None = None,
) -> str:
    combined = page_text + " " + " ".join(
        cell
        for row in first_rows
        for cell in row
    )

    combined_upper = combined.upper()

    for keyword, table_type in TABLE_TYPE_KEYWORDS:
        if keyword in combined_upper:
            return table_type

    if any(signal in combined_upper for signal in INCOME_CONTINUATION_SIGNALS):
        if previous_table_type in ("gelir_tablosu", "kapsamli_gelir_tablosu"):
            return previous_table_type

        return "gelir_tablosu"

    if previous_table_type and previous_table_type not in (
        "metin",
        "rapor_bilgisi",
        "diger",
    ):
        return previous_table_type

    return "diger"


# ============================================================
# Kolon şeması
# ============================================================

class ColumnSchema:
    def __init__(
        self,
        table: list[list[str]],
        table_type: str,
        page_text: str,
        period: str,
    ) -> None:
        self.table_type = table_type
        self.period = period
        self.col_labels: dict[int, str] = {}
        self.header_end: int = 0

        self._parse(table, page_text)

    def _parse(self, table: list[list[str]], page_text: str) -> None:
        for index, row in enumerate(table):
            if any(NUMBER_RE.match(cell) for cell in row):
                self.header_end = index
                break
        else:
            self.header_end = min(3, len(table))

        header_rows = table[:self.header_end]
        numeric_cols = self._find_numeric_cols(table)

        page_dates = _extract_dates_from_text(page_text)
        fallback_cari, fallback_onceki = _period_dates(
            period=self.period,
            table_type=self.table_type,
        )

        date_by_col: dict[int, str] = {}

        for row in header_rows:
            for col_index, cell in enumerate(row):
                for date_item in DATE_RE.findall(cell):
                    if col_index not in date_by_col:
                        date_by_col[col_index] = date_item

        if date_by_col:
            sorted_dates = sorted(date_by_col.items())
            cari_date = sorted_dates[0][1]
            onceki_date = sorted_dates[-1][1] if len(sorted_dates) > 1 else fallback_onceki
        elif len(page_dates) >= 2:
            cari_date = page_dates[0]
            onceki_date = page_dates[1]
        elif len(page_dates) == 1:
            cari_date = page_dates[0]
            onceki_date = fallback_onceki
        else:
            cari_date = fallback_cari
            onceki_date = fallback_onceki

        tp_cols: list[int] = []
        yp_cols: list[int] = []
        toplam_cols: list[int] = []

        for row in header_rows:
            for col_index, cell in enumerate(row):
                upper_cell = cell.strip().upper()

                if upper_cell == "TP":
                    tp_cols.append(col_index)
                elif upper_cell == "YP":
                    yp_cols.append(col_index)
                elif upper_cell in ("TOPLAM", "TOTAL"):
                    toplam_cols.append(col_index)

        balance_like = self.table_type in ("bilanco", "nazim_hesaplar", "turev_araclar")

        if balance_like and tp_cols and yp_cols:
            self._assign_tp_yp_total(
                tp_cols=tp_cols,
                yp_cols=yp_cols,
                toplam_cols=toplam_cols,
                cari_date=cari_date,
                onceki_date=onceki_date,
            )
            return

        if balance_like and len(numeric_cols) >= 6:
            self.col_labels[numeric_cols[0]] = f"cari_TP ({cari_date})"
            self.col_labels[numeric_cols[1]] = f"cari_YP ({cari_date})"
            self.col_labels[numeric_cols[2]] = f"cari_TOPLAM ({cari_date})"
            self.col_labels[numeric_cols[3]] = f"onceki_TP ({onceki_date})"
            self.col_labels[numeric_cols[4]] = f"onceki_YP ({onceki_date})"
            self.col_labels[numeric_cols[5]] = f"onceki_TOPLAM ({onceki_date})"
            return

        if len(numeric_cols) >= 2:
            self.col_labels[numeric_cols[0]] = f"cari_TOPLAM ({cari_date})"
            self.col_labels[numeric_cols[1]] = f"onceki_TOPLAM ({onceki_date})"
        elif len(numeric_cols) == 1:
            self.col_labels[numeric_cols[0]] = f"cari_TOPLAM ({cari_date})"

    def _assign_tp_yp_total(
        self,
        tp_cols: list[int],
        yp_cols: list[int],
        toplam_cols: list[int],
        cari_date: str,
        onceki_date: str,
    ) -> None:
        pairs = list(zip(tp_cols, yp_cols))

        if len(pairs) >= 2:
            self.col_labels[pairs[0][0]] = f"cari_TP ({cari_date})"
            self.col_labels[pairs[0][1]] = f"cari_YP ({cari_date})"
            self.col_labels[pairs[1][0]] = f"onceki_TP ({onceki_date})"
            self.col_labels[pairs[1][1]] = f"onceki_YP ({onceki_date})"

        elif pairs:
            self.col_labels[pairs[0][0]] = f"cari_TP ({cari_date})"
            self.col_labels[pairs[0][1]] = f"cari_YP ({cari_date})"

        if len(toplam_cols) >= 2:
            self.col_labels[toplam_cols[0]] = f"cari_TOPLAM ({cari_date})"
            self.col_labels[toplam_cols[1]] = f"onceki_TOPLAM ({onceki_date})"
        elif len(toplam_cols) == 1:
            self.col_labels[toplam_cols[0]] = f"cari_TOPLAM ({cari_date})"

    def _find_numeric_cols(self, table: list[list[str]]) -> list[int]:
        data_rows = table[self.header_end:]

        if not data_rows:
            return []

        col_count = max(len(row) for row in data_rows)
        result: list[int] = []

        for col_index in range(1, col_count):
            if any(
                col_index < len(row) and NUMBER_RE.match(row[col_index])
                for row in data_rows
            ):
                result.append(col_index)

        return result

    def label(self, col_index: int) -> str | None:
        return self.col_labels.get(col_index)


# ============================================================
# Satır / metrik çıkarma
# ============================================================

def _is_note_cell(cell: str) -> bool:
    cell = cell.strip()

    if not cell:
        return False

    return bool(
        re.match(r"^\(?[IVX]+(?:-[a-zçğıöşü0-9]+)*(?:,\s*[a-zçğıöşü0-9-]+)*\)?$", cell, flags=re.IGNORECASE)
    )


def _extract_metric_name(row: list[str], first_numeric_col: int | None) -> str:
    if first_numeric_col is None:
        candidate_cells = row
    else:
        candidate_cells = row[:first_numeric_col]

    label_cells: list[str] = []

    for cell in candidate_cells:
        cell = cell.strip()

        if not cell:
            continue

        if _is_note_cell(cell):
            continue

        if NUMBER_RE.match(cell):
            continue

        label_cells.append(cell)

    metric = " ".join(label_cells)
    metric = re.sub(r"\s+", " ", metric).strip()

    return metric


def _looks_like_section_header(label: str) -> bool:
    label = label.strip()

    if not label:
        return False

    if len(label) < 4:
        return False

    upper = label.upper()

    letter_count = sum(1 for char in upper if char.isalpha())

    if letter_count < 4:
        return False

    return label == upper


# ============================================================
# Tablo → chunks + structured
# ============================================================

def _table_to_output(
    table: list[list[str]],
    table_type: str,
    meta: dict,
    page_no: int,
    page_text: str,
) -> tuple[list[dict], list[dict]]:
    schema = ColumnSchema(
        table=table,
        table_type=table_type,
        page_text=page_text,
        period=meta["period"],
    )

    chunks: list[dict] = []
    records: list[dict] = []

    data_rows = table[schema.header_end:]
    current_parent = ""

    for row in data_rows:
        numeric_positions = [
            col_index
            for col_index, cell in enumerate(row)
            if NUMBER_RE.match(cell.strip())
        ]

        first_numeric_col = min(numeric_positions) if numeric_positions else None
        metric = _extract_metric_name(row, first_numeric_col)

        if not metric:
            continue

        labeled_values: list[tuple[str, str]] = []

        for col_index, cell in enumerate(row):
            cell = cell.strip()

            if not NUMBER_RE.match(cell):
                continue

            label = schema.label(col_index)

            if label is None:
                continue

            labeled_values.append((label, cell))

        if not labeled_values:
            if _looks_like_section_header(metric):
                current_parent = metric
            continue

        values_dict = {label: value for label, value in labeled_values}
        all_zero = all(value in ("0", "-0", "0,0", "0,00") for value in values_dict.values())

        record = {
            "ticker": meta["ticker"],
            "company": meta["company"],
            "period": meta["period"],
            "stmt_type": meta["stmt_type"],
            "table_type": table_type,
            "metric": metric,
            "parent_section": current_parent,
            "unit": meta["unit"],
            "page": page_no,
            "all_zero": all_zero,
            "pub_date": meta.get("pub_date"),
            "values": values_dict,
        }

        records.append(record)

        if all_zero:
            continue

        metric_full = f"{current_parent} > {metric}" if current_parent else metric

        value_str = " | ".join(
            f"{label}: {value}"
            for label, value in labeled_values
        )

        chunk_text = (
            f"Banka: {meta['ticker']} ({meta['company']}) | "
            f"Dönem: {meta['period']} | "
            f"Tablo: {table_type} | "
            f"Kalem: {metric_full} | "
            f"{value_str} | "
            f"Birim: {meta['unit']} | "
            f"Sayfa: {page_no}"
        )

        chunks.append(
            {
                "text": chunk_text,
                "metadata": {
                    "chunk_type": "table_row",
                    "ticker": meta["ticker"],
                    "company": meta["company"],
                    "period": meta["period"],
                    "stmt_type": meta["stmt_type"],
                    "table_type": table_type,
                    "metric": metric,
                    "parent_section": current_parent,
                    "unit": meta["unit"],
                    "page": page_no,
                    "pub_date": meta.get("pub_date"),
                    "values": values_dict,
                },
            }
        )

    return chunks, records


# ============================================================
# Metin chunk
# ============================================================

def _text_to_chunks(text: str, page_no: int, meta: dict) -> list[dict]:
    text = text.strip()

    if len(text) < 80:
        return []

    words = text.split()
    chunks: list[dict] = []
    start = 0

    while start < len(words):
        end = min(start + 500, len(words))
        chunk_body = " ".join(words[start:end])

        chunk_text = (
            f"Banka: {meta['ticker']} ({meta['company']}) | "
            f"Dönem: {meta['period']} | "
            f"Sayfa: {page_no} | "
            f"Metin: {chunk_body}"
        )

        chunks.append(
            {
                "text": chunk_text,
                "metadata": {
                    "chunk_type": "text",
                    "ticker": meta["ticker"],
                    "company": meta["company"],
                    "period": meta["period"],
                    "table_type": "metin",
                    "page": page_no,
                    "pub_date": meta.get("pub_date"),
                },
            }
        )

        if end == len(words):
            break

        start = end - 80

    return chunks


# ============================================================
# PDF okuma
# ============================================================

def _extract_pages(pdf_path: Path) -> list[dict]:
    pages: list[dict] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            text = _clean_text(
                page.extract_text(
                    x_tolerance=1,
                    y_tolerance=3,
                    layout=False,
                )
            )

            try:
                raw_tables = page.extract_tables(TABLE_SETTINGS) or []
            except Exception:
                raw_tables = []

            normalized_tables: list[list[list[str]]] = []

            for raw_table in raw_tables:
                normalized = _normalize_table(raw_table)

                if normalized:
                    normalized_tables.append(normalized)

            clean_tables = _dedupe_tables(normalized_tables)

            pages.append(
                {
                    "page": page_index,
                    "text": text,
                    "tables": clean_tables,
                    "table_count": len(clean_tables),
                }
            )

    return pages


# ============================================================
# Ana extraction
# ============================================================

def extract_all(
    pdf_path: str | Path,
    ticker: str | None = None,
    include_text_on_table_pages: bool = False,
) -> dict[str, Any]:
    pdf_path = Path(pdf_path)
    pages = _extract_pages(pdf_path)

    meta = _parse_metadata(pages)

    if ticker:
        meta["ticker"] = ticker.upper()
    elif meta["ticker"] == "BILINMIYOR":
        meta["ticker"] = pdf_path.stem.split("_")[0].upper()

    audit = _parse_audit(pages)
    meta = {**meta, **audit}

    summary_text = (
        f"Rapor bilgisi | "
        f"Banka: {meta['ticker']} ({meta['company']}) | "
        f"Dönem: {meta['period']} | "
        f"Finansal tablo niteliği: {meta['stmt_type']} | "
        f"Sunum para birimi: {meta['unit']} | "
        f"KAP yayın tarihi: {meta.get('pub_date', '-')}"
    )

    if audit:
        summary_text += (
            f" | Denetim kuruluşu: {audit.get('audit_company', '-')} | "
            f"Denetim türü: {audit.get('audit_type', '-')} | "
            f"Denetim sonucu: {audit.get('audit_result', '-')}"
        )

    all_chunks: list[dict] = [
        {
            "text": summary_text,
            "metadata": {
                "chunk_type": "report_info",
                "ticker": meta["ticker"],
                "company": meta["company"],
                "period": meta["period"],
                "table_type": "rapor_bilgisi",
                "page": 0,
                "pub_date": meta.get("pub_date"),
                **audit,
            },
        }
    ]

    all_structured: list[dict] = []
    warnings: list[str] = []
    previous_table_type: str | None = None

    for page in pages:
        page_no = page["page"]
        text = page["text"]
        tables = page["tables"]

        if tables:
            table_type = _detect_table_type(
                page_text=text,
                first_rows=tables[0][:4],
                previous_table_type=previous_table_type,
            )

            previous_table_type = table_type

            for table in tables:
                chunks, records = _table_to_output(
                    table=table,
                    table_type=table_type,
                    meta=meta,
                    page_no=page_no,
                    page_text=text,
                )

                all_chunks.extend(chunks)
                all_structured.extend(records)

            if include_text_on_table_pages:
                all_chunks.extend(
                    _text_to_chunks(
                        text=text,
                        page_no=page_no,
                        meta=meta,
                    )
                )

        else:
            if page_no > 1:
                all_chunks.extend(
                    _text_to_chunks(
                        text=text,
                        page_no=page_no,
                        meta=meta,
                    )
                )

    unique_chunks: list[dict] = []
    seen: set[str] = set()

    for chunk in all_chunks:
        key = chunk["text"][:500]

        if key in seen:
            continue

        seen.add(key)
        unique_chunks.append(chunk)

    fragmented_terms = ["| o", "| ina", "| ns", "Bilanç |", "F | ina"]
    fragmented_hits = [
        chunk
        for chunk in unique_chunks
        if any(term in chunk["text"] for term in fragmented_terms)
    ]

    diger_records = [
        record
        for record in all_structured
        if record.get("table_type") == "diger"
    ]

    fallback_records = [
        record
        for record in all_structured
        if any(
            "(cari_donem)" in key or "(onceki_donem)" in key
            for key in record.get("values", {}).keys()
        )
    ]

    if fragmented_hits:
        warnings.append(
            f"{len(fragmented_hits)} chunk içinde parçalanmış tablo izi olabilir."
        )

    if diger_records:
        sample_metrics = ", ".join(
            sorted(
                {
                    str(record.get("metric"))
                    for record in diger_records[:5]
                }
            )
        )
        warnings.append(
            f"{len(diger_records)} structured kayıt table_type='diger' kaldı. Örnek: {sample_metrics}"
        )

    if fallback_records:
        warnings.append(
            f"{len(fallback_records)} structured kayıtta tarih fallback etiketi var."
        )

    return {
        "chunks": unique_chunks,
        "structured": all_structured,
        "metadata": meta,
        "warnings": warnings,
        "pages": pages,
    }


# ============================================================
# Output builder
# ============================================================

def _build_full_raw_text(pages: list[dict]) -> str:
    """
    Debug dosyasıdır.
    Sadece ham sayfa metnini koyar.
    RAG'e gönderilmez.
    """
    parts: list[str] = []

    for page in pages:
        page_no = page.get("page")
        text = page.get("text", "")

        parts.append(f"===== SAYFA {page_no} =====")
        parts.append(text.strip())
        parts.append("")

    return "\n\n".join(parts).strip()


def _build_rag_ready_text(result: dict[str, Any]) -> str:
    """
    RAG-ready dosyasıdır.
    Sadece temiz chunk textlerinden oluşur.
    Markdown tablo dump'ı, bozuk duplicate tablo ve metin açıklaması içermez.
    """
    metadata = result.get("metadata", {})
    chunks = result.get("chunks", [])
    warnings = result.get("warnings", [])

    parts: list[str] = []

    parts.append("# KAP Finansal Raporu - Temiz RAG Ready")
    parts.append("")

    parts.append("## Rapor Bilgileri")
    parts.append(f"Banka: {metadata.get('ticker')}")
    parts.append(f"Şirket: {metadata.get('company')}")
    parts.append(f"Dönem: {metadata.get('period')}")
    parts.append(f"Finansal tablo niteliği: {metadata.get('stmt_type')}")
    parts.append(f"Sunum para birimi: {metadata.get('unit')}")
    parts.append(f"KAP yayın tarihi: {metadata.get('pub_date')}")
    parts.append("")

    if metadata.get("audit_company") or metadata.get("audit_result"):
        parts.append("## Denetim Bilgileri")
        parts.append(f"Denetim kuruluşu: {metadata.get('audit_company')}")
        parts.append(f"Denetim türü: {metadata.get('audit_type')}")
        parts.append(f"Denetim sonucu: {metadata.get('audit_result')}")
        parts.append("")

    if warnings:
        parts.append("## Extractor Uyarıları")
        for warning in warnings:
            parts.append(f"- {warning}")
        parts.append("")

    parts.append("## Temiz İçerik")
    parts.append(
        "Not: Bu dosya sadece temiz chunk textlerinden oluşur. "
        "Sayısal cevaplarda structured JSON/DB kayıtları esas alınmalıdır."
    )
    parts.append("")

    for index, chunk in enumerate(chunks, start=1):
        text = chunk.get("text", "").strip()
        md = chunk.get("metadata", {}) or {}

        if not text:
            continue

        chunk_type = md.get("chunk_type")
        page = md.get("page")
        table_type = md.get("table_type")
        metric = md.get("metric")
        parent_section = md.get("parent_section")

        header_parts = [f"Chunk {index:04d}"]

        if chunk_type:
            header_parts.append(f"Tip: {chunk_type}")

        if page is not None:
            header_parts.append(f"Sayfa: {page}")

        if table_type:
            header_parts.append(f"Tablo: {table_type}")

        if parent_section:
            header_parts.append(f"Üst başlık: {parent_section}")

        if metric:
            header_parts.append(f"Kalem: {metric}")

        parts.append("### " + " | ".join(header_parts))
        parts.append(text)
        parts.append("")

    return "\n".join(parts).strip()


# ============================================================
# Dışarıdan çağrılacak save fonksiyonları
# ============================================================

def save_chunks(
    data: list[dict],
    output_path: str | Path,
    fmt: str = "jsonl",
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "jsonl":
        _safe_jsonl_write(output_path, data)
    else:
        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return output_path


def save_extracted_text(
    pdf_path: str | Path,
    output_root: str | Path = "data/extracted_text/banks",
) -> dict[str, Any]:
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF bulunamadı: {pdf_path}")

    ticker = pdf_path.parent.name.upper()
    stem = pdf_path.stem

    output_dir = Path(output_root) / ticker
    output_dir.mkdir(parents=True, exist_ok=True)

    full_raw_path = output_dir / f"{stem}_full_raw.txt"
    rag_ready_path = output_dir / f"{stem}_rag_ready.txt"
    chunks_path = output_dir / f"{stem}_chunks.jsonl"
    structured_path = output_dir / f"{stem}_structured.jsonl"
    metadata_path = output_dir / f"{stem}_metadata.json"
    warnings_path = output_dir / f"{stem}_warnings.json"
    pages_json_path = output_dir / f"{stem}_pages.json"

    result = extract_all(
        pdf_path=pdf_path,
        ticker=ticker,
        include_text_on_table_pages=False,
    )

    pages = result.get("pages", [])
    chunks = result.get("chunks", [])
    structured = result.get("structured", [])
    metadata = result.get("metadata", {})
    warnings = result.get("warnings", [])

    full_raw_text = _build_full_raw_text(pages)
    rag_ready_text = _build_rag_ready_text(result)

    full_raw_path.write_text(full_raw_text, encoding="utf-8")
    rag_ready_path.write_text(rag_ready_text, encoding="utf-8")

    save_chunks(chunks, chunks_path)
    save_chunks(structured, structured_path)

    _write_json(metadata_path, metadata)
    _write_json(warnings_path, warnings)

    _write_json(
        pages_json_path,
        {
            "ticker": ticker,
            "source_pdf": str(pdf_path),
            "page_count": len(pages),
            "pages": pages,
        },
    )

    return {
        "ticker": ticker,
        "pdf_path": str(pdf_path),

        "txt_path": str(rag_ready_path),
        "rag_ready_path": str(rag_ready_path),
        "full_raw_path": str(full_raw_path),

        "chunks_path": str(chunks_path),
        "structured_path": str(structured_path),
        "metadata_path": str(metadata_path),
        "warnings_path": str(warnings_path),
        "pages_json_path": str(pages_json_path),

        "page_count": len(pages),
        "total_char_count": len(rag_ready_text),
        "raw_total_char_count": len(full_raw_text),
        "chunk_count": len(chunks),
        "structured_count": len(structured),
        "warning_count": len(warnings),
        "warnings": warnings,
    }


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Kullanım: python app/pdf_extractor.py <pdf_path> [output_root]")
        sys.exit(1)

    pdf_file = sys.argv[1]
    output_dir_arg = sys.argv[2] if len(sys.argv) > 2 else "data/extracted_text/banks"

    result = save_extracted_text(
        pdf_path=pdf_file,
        output_root=output_dir_arg,
    )

    print("Extraction tamamlandı.")
    print(json.dumps(result, ensure_ascii=False, indent=2)) 