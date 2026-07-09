from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup


def parse_latest_financial_reports_from_html(
    html: str,
    ticker: str,
    limit: int = 1,
) -> list[dict[str, Any]]:
    """
    KAP HTML içindeki tablo satırlarından gerçek 'Finansal Rapor' kayıtlarını ayıklar.

    Sorumluluk Beyanı, Faaliyet Raporu, TSRS Raporu gibi kayıtları almaz.
    """
    soup = BeautifulSoup(html, "lxml")
    ticker = ticker.upper()

    reports: list[dict[str, Any]] = []

    rows = soup.find_all("tr", id=lambda value: value and value.startswith("notification"))

    for row in rows:
        row_text = row.get_text(" ", strip=True)

        if ticker not in row_text:
            continue

        if "Finansal Rapor" not in row_text:
            continue

        if "Sorumluluk Beyanı" in row_text:
            continue

        if "Faaliyet Raporu" in row_text:
            continue

        if "TSRS" in row_text:
            continue

        disclosure_input = row.find("input", {"name": "notification-checkbox"})
        disclosure_id = disclosure_input.get("id") if disclosure_input else None

        cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]

        publish_date = None
        publish_time = None
        stock_code = ticker
        company_title = None
        disclosure_type = None
        subject = None
        year = None
        period = None

        # KAP tablo yapısında genelde:
        # cells[2] = tarih + saat
        # cells[3] = kod
        # cells[4] = şirket
        # cells[5] = tip
        # cells[6] = konu
        # cells[9] = yıl
        # cells[10] = periyot

        if len(cells) > 2:
            date_parts = cells[2].split()
            if len(date_parts) >= 1:
                publish_date = date_parts[0]
            if len(date_parts) >= 2:
                publish_time = date_parts[1]

        if len(cells) > 3:
            stock_code = cells[3]

        if len(cells) > 4:
            company_title = cells[4].replace("Finansal Rapor", "").strip()

        if len(cells) > 5:
            disclosure_type = cells[5]

        if len(cells) > 6:
            subject = cells[6]

        if len(cells) > 9:
            year = cells[9]

        if len(cells) > 10:
            period = cells[10]

        # Güvenlik kontrolü: konu tam olarak Finansal Rapor olmalı
        if subject != "Finansal Rapor":
            continue

        reports.append(
            {
                "ticker": stock_code,
                "publish_date": publish_date,
                "publish_time": publish_time,
                "company_title": company_title,
                "disclosure_type": disclosure_type,
                "subject": subject,
                "year": year,
                "period": period,
                "disclosure_id": disclosure_id,
                "detail_url": (
                    f"https://www.kap.org.tr/tr/Bildirim/{disclosure_id}"
                    if disclosure_id
                    else None
                ),
                "pdf_url": (
                f"https://www.kap.org.tr/tr/api/BildirimPdf/{disclosure_id}"
                if disclosure_id
                else None
            ),
                "raw_cells": cells,
            }
        )

        if len(reports) >= limit:
            break

    return reports