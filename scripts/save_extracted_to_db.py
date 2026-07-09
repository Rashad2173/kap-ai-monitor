from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

from app.db.connection import SessionLocal
from app.db.models import FinancialMetric, RagChunk, Report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract edilen KAP finansal rapor çıktılarını PostgreSQL'e kaydeder."
    )

    parser.add_argument(
        "--extracted-root",
        default="data/extracted_financial/banks",
        help="Extract edilmiş chunks/structured dosyalarının bulunduğu ana klasör.",
    )

    parser.add_argument(
        "--banks",
        nargs="*",
        default=None,
        help="Sadece belirli bankalar için çalıştır. Örn: AKBNK GARAN VAKBN",
    )

    parser.add_argument(
        "--clear-existing",
        action="store_true",
        help="Aynı rapor için eski financial_metrics ve rag_chunks kayıtlarını silip yeniden yazar.",
    )

    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if not path.exists():
        return rows

    with path.open("r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSONL okuma hatası: {path} satır {line_no}: {exc}") from exc

    return rows


def find_structured_files(extracted_root: Path, banks: list[str] | None = None) -> list[Path]:
    if banks:
        files: list[Path] = []

        for bank in banks:
            bank_dir = extracted_root / bank.upper()

            if not bank_dir.exists():
                print(f"UYARI: Extract klasörü yok: {bank_dir}")
                continue

            files.extend(sorted(bank_dir.glob("*_structured.jsonl")))

        return files

    return sorted(extracted_root.glob("*/*_structured.jsonl"))


def get_stem_from_structured_path(path: Path) -> str:
    # Örn:
    # AKBNK_1598264_structured.jsonl -> AKBNK_1598264
    name = path.name

    if not name.endswith("_structured.jsonl"):
        raise ValueError(f"Beklenmeyen structured dosya adı: {path}")

    return name.replace("_structured.jsonl", "")


def parse_ticker_and_disclosure_id(stem: str) -> tuple[str, str]:
    # Örn: AKBNK_1598264
    match = re.match(r"^([A-Z0-9]+)_(\d+)$", stem.upper())

    if not match:
        raise ValueError(f"Dosya adından ticker/disclosure_id çözülemedi: {stem}")

    ticker = match.group(1)
    disclosure_id = match.group(2)

    return ticker, disclosure_id


def as_int_or_none(value: Any) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_chunks_path_from_structured_path(structured_path: Path) -> Path:
    return structured_path.with_name(
        structured_path.name.replace("_structured.jsonl", "_chunks.jsonl")
    )


def find_report(session, disclosure_id: str) -> Report | None:
    return (
        session.query(Report)
        .filter(Report.disclosure_id == str(disclosure_id))
        .one_or_none()
    )


def clear_existing_report_data(session, report_id: int) -> None:
    session.query(FinancialMetric).filter(
        FinancialMetric.report_id == report_id
    ).delete(synchronize_session=False)

    session.query(RagChunk).filter(
        RagChunk.report_id == report_id
    ).delete(synchronize_session=False)


def save_financial_metrics(
    session,
    report: Report,
    ticker: str,
    structured_rows: list[dict[str, Any]],
) -> int:
    count = 0

    for row in structured_rows:
        values_json = row.get("values")

        if not isinstance(values_json, dict):
            # Eski format ihtimaline karşı:
            # cari_TOPLAM gibi alanları values_json içine toparla.
            values_json = {
                key: value
                for key, value in row.items()
                if isinstance(key, str)
                and (
                    key.startswith("cari_")
                    or key.startswith("onceki_")
                    or "TOPLAM" in key
                    or "TP" in key
                    or "YP" in key
                )
            }

        metric = row.get("metric")

        if not metric:
            continue

        item = FinancialMetric(
            report_id=report.id,
            ticker=ticker,
            period=row.get("period") or report.period_code,
            table_type=row.get("table_type"),
            metric=str(metric),
            parent_section=row.get("parent_section"),
            unit=row.get("unit"),
            page=as_int_or_none(row.get("page")),
            all_zero=bool(row.get("all_zero", False)),
            values_json=values_json,
            raw_json=row,
        )

        session.add(item)
        count += 1

    return count


def save_rag_chunks(
    session,
    report: Report,
    ticker: str,
    chunk_rows: list[dict[str, Any]],
) -> int:
    count = 0

    for row in chunk_rows:
        text = row.get("text")

        if not text or not str(text).strip():
            continue

        metadata = row.get("metadata") or {}

        if not isinstance(metadata, dict):
            metadata = {}

        item = RagChunk(
            report_id=report.id,
            ticker=ticker,
            period=metadata.get("period") or report.period_code,
            chunk_type=metadata.get("chunk_type"),
            table_type=metadata.get("table_type"),
            metric=metadata.get("metric"),
            parent_section=metadata.get("parent_section"),
            page=as_int_or_none(metadata.get("page")),
            text=str(text).strip(),
            metadata_json=metadata,
            embedding_status="pending",
        )

        session.add(item)
        count += 1

    return count


def process_one_report(
    session,
    structured_path: Path,
    clear_existing: bool,
) -> dict[str, Any]:
    stem = get_stem_from_structured_path(structured_path)
    ticker, disclosure_id = parse_ticker_and_disclosure_id(stem)

    chunks_path = get_chunks_path_from_structured_path(structured_path)

    report = find_report(session, disclosure_id)

    if not report:
        return {
            "ticker": ticker,
            "disclosure_id": disclosure_id,
            "status": "missing_report",
            "message": "reports tablosunda bu disclosure_id bulunamadı.",
            "structured_path": str(structured_path),
            "chunks_path": str(chunks_path),
        }

    structured_rows = read_jsonl(structured_path)
    chunk_rows = read_jsonl(chunks_path)

    if clear_existing:
        clear_existing_report_data(session, report.id)

    metric_count = save_financial_metrics(
        session=session,
        report=report,
        ticker=ticker,
        structured_rows=structured_rows,
    )

    chunk_count = save_rag_chunks(
        session=session,
        report=report,
        ticker=ticker,
        chunk_rows=chunk_rows,
    )

    return {
        "ticker": ticker,
        "disclosure_id": disclosure_id,
        "report_id": report.id,
        "status": "ok",
        "structured_path": str(structured_path),
        "chunks_path": str(chunks_path),
        "financial_metric_count": metric_count,
        "rag_chunk_count": chunk_count,
    }


def main() -> None:
    args = parse_args()

    extracted_root = Path(args.extracted_root)
    structured_files = find_structured_files(extracted_root, args.banks)

    if not structured_files:
        print(f"Structured JSONL bulunamadı: {extracted_root}")
        return

    print("Extract edilmiş veriler PostgreSQL'e kaydediliyor")
    print(f"Structured dosya sayısı: {len(structured_files)}")
    print(f"Clear existing: {args.clear_existing}")
    print("-" * 80)

    session = SessionLocal()

    results: list[dict[str, Any]] = []

    total_metrics = 0
    total_chunks = 0

    try:
        for structured_path in structured_files:
            print(f"İşleniyor: {structured_path}")

            result = process_one_report(
                session=session,
                structured_path=structured_path,
                clear_existing=args.clear_existing,
            )

            results.append(result)

            if result["status"] == "ok":
                total_metrics += result["financial_metric_count"]
                total_chunks += result["rag_chunk_count"]

                print(
                    f"  OK | metric: {result['financial_metric_count']} | "
                    f"chunk: {result['rag_chunk_count']}"
                )
            else:
                print(f"  UYARI | {result['status']} | {result['message']}")

        session.commit()

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()

    summary_path = Path("data/extracted_financial/db_save_summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("-" * 80)
    print("DB kayıt işlemi tamamlandı.")
    print(f"Toplam financial_metrics: {total_metrics}")
    print(f"Toplam rag_chunks: {total_chunks}")
    print(f"Özet: {summary_path}")


if __name__ == "__main__":
    main()