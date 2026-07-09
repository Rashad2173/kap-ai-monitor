from __future__ import annotations

from dataclasses import dataclass

import requests


@dataclass
class KAPClient:
    base_url: str = "https://www.kap.org.tr"
    language: str = "tr"
    timeout: int = 30

    def __post_init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            }
        )

    def fetch_bank_financial_report_page(self, member_oid: str) -> str:
        """
        KAP sonuç sayfasını HTML olarak çeker.
        Eski API POST kullanılmıyor.
        """
        url = f"{self.base_url}/{self.language}/bildirim-sorgu-sonuc"

        params = {
            "disclosureClass": "FR",
            "member": member_oid,
        }

        response = self.session.get(
            url,
            params=params,
            timeout=self.timeout,
        )

        response.raise_for_status()
        return response.text