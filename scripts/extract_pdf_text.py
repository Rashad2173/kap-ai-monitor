from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

from app.pdf_extractor import extract_all, save_chunks


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="İndirilen KAP PDF raporlarından v3 extractor ile structured/chunk çıkarır."
    )

    parser.add_argument(
        "--pdf-root",
        default="data/pdfs/banks",
        help="PDF dosyalarının bulunduğu ana klasör.",
    )

    parser.add_argument(
        "--output-root",
        default="data/extracted_financial/banks",
        help="Çıkarılan structured/chunk dosyalarının kaydedileceği ana klasör.",
    )

    parser.add_argument(
        "--banks",
        nargs="*",
        default=None,
        help="Sadece belirli bankalar için çalıştır. Örn: AKBNK GARAN VAKBN",
    )

    return parser.parse_args()


def find_pdf_files(pdf_root: Path, banks: list[str] | None = None) -> list[Path]:
    if banks:
        pdf_files: list[Path] = []

        for bank in banks:
            bank_dir = pdf_root / bank.upper()

            if not bank_dir.exists():
                print(f"UYARI: Banka klasörü yok: {bank_dir}")
                continue

            pdf_files.extend(sorted(bank_dir.glob("*.pdf")))

        return pdf_files

    return sorted(pdf_root.glob("*/*.pdf"))


def main() -> None:
    args = parse_args()

    pdf_root = Path(args.pdf_root)
    output_root = Path(args.output_root)

    pdf_files = find_pdf_files(pdf_root, args.banks)

    if not pdf_files:
        print(f"PDF bulunamadı: {pdf_root}")
        return

    print("V3 finansal extraction başladı")
    print(f"PDF sayısı: {len(pdf_files)}")
    print("-" * 80)

    summary: list[dict[str, Any]] = []

    for pdf_path in pdf_files:
        ticker = pdf_path.parent.name.upper()
        stem = pdf_path.stem

        output_dir = output_root / ticker
        output_dir.mkdir(parents=True, exist_ok=True)

        chunks_path = output_dir / f"{stem}_chunks.jsonl"
        structured_path = output_dir / f"{stem}_structured.jsonl"
        metadata_path = output_dir / f"{stem}_metadata.json"
        warnings_path = output_dir / f"{stem}_warnings.json"

        print(f"{ticker}: işleniyor -> {pdf_path}")

        try:
            result = extract_all(
                pdf_path=pdf_path,
                ticker=ticker,
            )

            chunks = result["chunks"]
            structured = result["structured"]
            metadata = result["metadata"]
            warnings = result.get("warnings", [])

            save_chunks(chunks, chunks_path)
            save_chunks(structured, structured_path)
            save_json(metadata_path, metadata)
            save_json(warnings_path, warnings)

            summary_item = {
                "ticker": ticker,
                "pdf_path": str(pdf_path),
                "chunks_path": str(chunks_path),
                "structured_path": str(structured_path),
                "metadata_path": str(metadata_path),
                "warnings_path": str(warnings_path),
                "chunk_count": len(chunks),
                "structured_count": len(structured),
                "warning_count": len(warnings),
                "warnings": warnings,
                "status": "ok",
            }

            summary.append(summary_item)

            print(
                f"  OK | chunk: {len(chunks)} | "
                f"structured: {len(structured)} | "
                f"warning: {len(warnings)}"
            )

            if warnings:
                for warning in warnings:
                    print(f"  UYARI: {warning}")

        except Exception as exc:
            summary.append(
                {
                    "ticker": ticker,
                    "pdf_path": str(pdf_path),
                    "status": "error",
                    "error": str(exc),
                }
            )

            print(f"  HATA: {exc}")

        print("-" * 80)

    summary_path = Path("data/extracted_financial") / "extraction_summary.json"
    save_json(summary_path, summary)

    print("V3 finansal extraction tamamlandı.")
    print(f"Özet dosyası: {summary_path}")


if __name__ == "__main__":
    main()