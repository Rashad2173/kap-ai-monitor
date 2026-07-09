from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

from app.db.connection import SessionLocal
from app.db.models import Bank, Report, ReportFile


LATEST_REPORTS_PATH = Path("data/raw/kap_responses/latest_bank_reports.json")
PDF_ROOT = Path("data/pdfs/banks")


def parse_date(value: str | None):
    if not value:
        return None

    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue

    return None


def parse_time(value: str | None):
    if not value:
        return None

    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value.strip(), fmt).time()
        except ValueError:
            continue

    return None


def build_period_code(year: str | None, period_label: str | None) -> str | None:
    if not year or not period_label:
        return None

    label = period_label.lower()

    if "3 aylık" in label:
        return f"{year}_Q1"

    if "6 aylık" in label:
        return f"{year}_Q2"

    if "9 aylık" in label:
        return f"{year}_Q3"

    if "12 aylık" in label or "yıllık" in label:
        return f"{year}_YIL"

    return f"{year}_{period_label}"


def fix_pdf_url(disclosure_id: str | None) -> str | None:
    if not disclosure_id:
        return None

    return f"https://www.kap.org.tr/tr/api/BildirimPdf/{disclosure_id}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


def find_local_pdf(ticker: str, disclosure_id: str) -> Path | None:
    ticker = ticker.upper()

    expected = PDF_ROOT / ticker / f"{ticker}_{disclosure_id}.pdf"

    if expected.exists():
        return expected

    bank_dir = PDF_ROOT / ticker

    if not bank_dir.exists():
        return None

    candidates = list(bank_dir.glob(f"*{disclosure_id}*.pdf"))

    if candidates:
        return candidates[0]

    return None


def load_latest_reports() -> dict[str, list[dict[str, Any]]]:
    if not LATEST_REPORTS_PATH.exists():
        raise FileNotFoundError(f"JSON bulunamadı: {LATEST_REPORTS_PATH}")

    return json.loads(LATEST_REPORTS_PATH.read_text(encoding="utf-8"))


def upsert_bank(session, ticker: str, company_name: str | None) -> Bank:
    ticker = ticker.upper()

    bank = session.query(Bank).filter(Bank.ticker == ticker).one_or_none()

    if bank:
        if company_name and bank.company_name != company_name:
            bank.company_name = company_name
        return bank

    bank = Bank(
        ticker=ticker,
        company_name=company_name,
    )

    session.add(bank)
    session.flush()

    return bank


def upsert_report(
    session,
    bank: Bank,
    item: dict[str, Any],
    ticker_override: str,
) -> Report:
    ticker = ticker_override.upper()
    disclosure_id = str(item["disclosure_id"])

    report = (
        session.query(Report)
        .filter(Report.disclosure_id == disclosure_id)
        .one_or_none()
    )

    if not report:
        report = Report(
            bank_id=bank.id,
            ticker=ticker,
            disclosure_id=disclosure_id,
        )
        session.add(report)

    report.bank_id = bank.id
    report.ticker = ticker
    report.disclosure_id = disclosure_id

    report.publish_date = parse_date(item.get("publish_date"))
    report.publish_time = parse_time(item.get("publish_time"))

    report.year = item.get("year")
    report.period_label = item.get("period")
    report.period_code = build_period_code(
        year=item.get("year"),
        period_label=item.get("period"),
    )

    report.subject = item.get("subject")
    report.disclosure_type = item.get("disclosure_type")

    report.detail_url = item.get("detail_url")
    report.pdf_url = fix_pdf_url(disclosure_id)

    report.raw_json = item

    session.flush()

    return report


def upsert_report_file(
    session,
    report: Report,
    ticker: str,
    disclosure_id: str,
) -> ReportFile | None:
    pdf_path = find_local_pdf(ticker, disclosure_id)

    if not pdf_path:
        print(f"  UYARI: PDF bulunamadı: {ticker} / {disclosure_id}")
        return None

    local_path = str(pdf_path)
    file_hash = sha256_file(pdf_path)

    existing = (
        session.query(ReportFile)
        .filter(
            ReportFile.report_id == report.id,
            ReportFile.file_type == "bildirim_pdf",
            ReportFile.local_path == local_path,
        )
        .one_or_none()
    )

    if existing:
        existing.file_hash = file_hash
        existing.file_name = pdf_path.name
        return existing

    report_file = ReportFile(
        report_id=report.id,
        file_type="bildirim_pdf",
        file_name=pdf_path.name,
        local_path=local_path,
        file_hash=file_hash,
    )

    session.add(report_file)
    session.flush()

    return report_file


def main() -> None:
    data = load_latest_reports()

    session = SessionLocal()

    total_reports = 0
    total_files = 0

    try:
        for ticker, reports in data.items():
            ticker = ticker.upper()

            print(f"{ticker}: DB kaydı başlıyor...")

            for item in reports:
                company_name = item.get("company_title")

                bank = upsert_bank(
                    session=session,
                    ticker=ticker,
                    company_name=company_name,
                )

                report = upsert_report(
                    session=session,
                    bank=bank,
                    item=item,
                    ticker_override=ticker,
                )

                total_reports += 1

                disclosure_id = str(item["disclosure_id"])

                report_file = upsert_report_file(
                    session=session,
                    report=report,
                    ticker=ticker,
                    disclosure_id=disclosure_id,
                )

                if report_file:
                    total_files += 1

        session.commit()

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()

    print("-" * 80)
    print("DB kayıt işlemi tamamlandı.")
    print(f"Rapor sayısı: {total_reports}")
    print(f"PDF dosya kaydı: {total_files}")


if __name__ == "__main__":
    main()  