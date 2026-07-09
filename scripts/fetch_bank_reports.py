from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

from app.bank_members import BANK_MEMBERS
from app.downloader import KAPPdfDownloader
from app.kap_client import KAPClient
from app.kap_parser import parse_latest_financial_reports_from_html


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bankaların KAP sonuç sayfasından son Finansal Rapor bildirimlerini çeker."
    )

    parser.add_argument(
        "--banks",
        nargs="*",
        default=list(BANK_MEMBERS.keys()),
        help="Banka kodları. Örn: AKBNK GARAN VAKBN",
    )

    parser.add_argument(
        "--limit-per-bank",
        type=int,
        default=1,
        help="Her banka için kaç Finansal Rapor alınsın?",
    )

    parser.add_argument(
        "--download-pdf",
        action="store_true",
        help="Bildirim ID bulunursa KAP PDF çıktısını indirir.",
    )

    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Bankalar arası bekleme süresi.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    client = KAPClient()
    downloader = KAPPdfDownloader()

    latest_reports: dict[str, list[dict[str, Any]]] = {}

    print("KAP HTML yöntemiyle banka finansal rapor çekme başladı")
    print(f"Her banka için rapor sayısı: {args.limit_per_bank}")
    print("-" * 80)

    for ticker in args.banks:
        ticker = ticker.upper()
        member_oid = BANK_MEMBERS.get(ticker)

        if not member_oid:
            print(f"{ticker}: member_oid bulunamadı, atlandı.")
            continue

        print(f"{ticker}: KAP sonuç sayfası çekiliyor...")

        try:
            html = client.fetch_bank_financial_report_page(member_oid=member_oid)

            html_path = Path("data/raw/kap_pages") / f"{ticker}.html"
            save_text(html_path, html)

            reports = parse_latest_financial_reports_from_html(
                html=html,
                ticker=ticker,
                limit=args.limit_per_bank,
            )

            latest_reports[ticker] = reports

            if not reports:
                print(f"  BULUNAMADI: Finansal Rapor satırı yakalanamadı. HTML: {html_path}")
            else:
                for report in reports:
                    print(
                        f"  BULUNDU: {report['publish_date']} {report['publish_time']} | "
                        f"{report['period']} | {report['subject']}"
                    )

                    if report.get("disclosure_id"):
                        print(f"  Bildirim ID: {report['disclosure_id']}")
                    else:
                        print("  UYARI: Bildirim ID HTML içinden yakalanamadı, PDF indirme atlanabilir.")

                    if args.download_pdf and report.get("disclosure_id"):
                        try:
                            pdf_path = downloader.download_notification_pdf(
                                disclosure_id=report["disclosure_id"],
                                ticker=ticker,
                            )
                            print(f"  PDF OK: {pdf_path}")
                        except Exception as pdf_exc:
                            print(f"  PDF HATA: {pdf_exc}")

        except Exception as exc:
            print(f"  HATA: {ticker} alınamadı -> {exc}")

        time.sleep(args.sleep)

    latest_path = Path("data/raw/kap_responses") / "latest_bank_reports.json"
    save_json(latest_path, latest_reports)

    print("-" * 80)
    print("İşlem tamamlandı.")
    print(f"Son rapor özeti: {latest_path}")


if __name__ == "__main__":
    main()