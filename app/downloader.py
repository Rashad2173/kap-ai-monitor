from __future__ import annotations

from pathlib import Path

import requests


class KAPPdfDownloader:
    def __init__(self, base_url: str = "https://www.kap.org.tr", timeout: int = 60) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept": "application/pdf,*/*",
            }
        )

    def download_notification_pdf(
        self,
        disclosure_id: int | str,
        ticker: str,
        output_root: str | Path = "data/pdfs/banks",
    ) -> Path:
        output_dir = Path(output_root) / ticker.upper()
        output_dir.mkdir(parents=True, exist_ok=True)

        url = f"{self.base_url}/tr/api/BildirimPdf/{disclosure_id}"
        output_path = output_dir / f"{ticker.upper()}_{disclosure_id}.pdf"

        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()

        content = response.content

        if not content.startswith(b"%PDF"):
            debug_path = output_dir / f"{ticker.upper()}_{disclosure_id}_not_pdf.html"
            debug_path.write_bytes(content)
            raise ValueError(f"PDF değil. Debug dosyası: {debug_path}")

        output_path.write_bytes(content)
        return output_path