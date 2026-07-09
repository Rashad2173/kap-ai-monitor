from __future__ import annotations

import os
import unittest
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from app.services.question_classifier import detect_metric_intent, detect_question_type


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


CLASSIFIER_CASES = [
    (
        "Yapı Kredi'nin 2026 Q1 nakit ve nakit benzerleri önceki döneme göre yüzde kaç değişmiştir?",
        "numeric_metric",
        "nakit",
    ),
    (
        "Yapı Kredi'nin 2026 Q1 varlıklar toplamı hangi finansal tablo ve sayfadan alınmıştır?",
        "numeric_metric",
        "varliklar_toplami",
    ),
    (
        "Garanti BBVA'nın 2026 Q1 net dönem kârı, 2025 Q1 dönemine göre nasıl değişmiştir?",
        "numeric_metric",
        "net_kar",
    ),
    (
        "Akbank için kripto varlıklar toplamı nedir?",
        "numeric_metric",
        "unknown_metric",
    ),
    (
        "Akbank'ın 2026 Q1 bilançosunda krediler varlıklar toplamının yüzde kaçını oluşturuyor?",
        "numeric_metric",
        "krediler",
    ),
    (
        "Akbank mı Garanti BBVA mı daha yüksek varlık toplamına sahiptir?",
        "numeric_metric",
        "varliklar_toplami",
    ),
    (
        "Akbank'ın 2026 Q1 bilançosuna göre varlıklar, yükümlülükler ve özkaynaklar birlikte değerlendirildiğinde bilanço dengesi doğru kurulmuş mu?",
        "numeric_metric",
        "yukumlulukler",
    ),
    (
        "Garanti BBVA'nın 2026 Q1 finansal raporu KAP'ta hangi tarihte yayımlanmıştır?",
        "report_metadata",
        None,
    ),
    (
        "İş Bankası'nın 2026 Q1 finansal tablosunda cari dönem ve önceki dönem hangi tarihlere karşılık geliyor?",
        "period_dates",
        None,
    ),
    (
        "Garanti BBVA'nin 2026 Q1 verisi konsolide finansal tablodan mi yoksa solo finansal tablodan mi alinmistir?",
        "report_metadata",
        None,
    ),
]


ROUTE_CASES = [
    (
        "Yapı Kredi'nin 2026 Q1 nakit ve nakit benzerleri önceki döneme göre yüzde kaç değişmiştir?",
        "numeric_period_compare",
    ),
    (
        "Yapı Kredi'nin 2026 Q1 varlıklar toplamı hangi finansal tablo ve sayfadan alınmıştır?",
        "numeric_metric_match",
    ),
    (
        "Garanti BBVA'nın 2026 Q1 net dönem kârı, 2025 Q1 dönemine göre nasıl değişmiştir?",
        "numeric_period_compare",
    ),
    (
        "Akbank için kripto varlıklar toplamı nedir?",
        "numeric_unknown_metric",
    ),
    (
        "Akbank'ın 2026 Q1 bilançosunda krediler varlıklar toplamının yüzde kaçını oluşturuyor?",
        "numeric_ratio",
    ),
    (
        "Akbank mı Garanti BBVA mı daha yüksek varlık toplamına sahiptir?",
        "numeric_cross_bank_compare",
    ),
    (
        "Akbank'ın 2026 Q1 bilançosuna göre varlıklar, yükümlülükler ve özkaynaklar birlikte değerlendirildiğinde bilanço dengesi doğru kurulmuş mu?",
        "numeric_balance_check",
    ),
    (
        "Garanti BBVA'nın 2026 Q1 finansal raporu KAP'ta hangi tarihte yayımlanmıştır?",
        "report_metadata",
    ),
    (
        "İş Bankası'nın 2026 Q1 finansal tablosunda cari dönem ve önceki dönem hangi tarihlere karşılık geliyor?",
        "period_dates",
    ),
    (
        "Garanti BBVA'nin 2026 Q1 verisi konsolide finansal tablodan mi yoksa solo finansal tablodan mi alinmistir?",
        "consolidation_status",
    ),
]


class QuestionClassifierRegressionTests(unittest.TestCase):
    def test_metric_questions_get_expected_type_and_intent(self) -> None:
        for question, expected_type, expected_intent in CLASSIFIER_CASES:
            with self.subTest(question=question):
                self.assertEqual(detect_question_type(question), expected_type)
                self.assertEqual(detect_metric_intent(question), expected_intent)

    def test_yapi_kredi_cash_does_not_become_credit_intent(self) -> None:
        question = (
            "Yapı ve Kredi Bankası A.Ş. 2026 Q1 nakit ve nakit benzerleri "
            "önceki döneme göre yüzde kaç değişmiştir?"
        )

        self.assertEqual(detect_question_type(question), "numeric_metric")
        self.assertEqual(detect_metric_intent(question), "nakit")
        self.assertNotEqual(detect_metric_intent(question), "krediler")


@unittest.skipUnless(os.getenv("DATABASE_URL"), "DATABASE_URL is required for route tests")
class QuestionRouterRegressionTests(unittest.TestCase):
    def test_metric_routes_do_not_fall_back_to_rag(self) -> None:
        from app.schemas.api import AskRequest
        from app.services.question_router import route_question

        for question, expected_mode in ROUTE_CASES:
            with self.subTest(question=question):
                response = route_question(AskRequest(bank="ALL", question=question))
                self.assertEqual(response.mode, expected_mode)
                self.assertNotIn("rag", response.mode)

    def test_remaining_benchmark_regressions(self) -> None:
        from app.schemas.api import AskRequest
        from app.services.question_router import route_question

        consolidation = route_question(
            AskRequest(
                bank="ALL",
                question=(
                    "Garanti BBVA'nin 2026 Q1 verisi konsolide finansal tablodan mi "
                    "yoksa solo finansal tablodan mi alinmistir?"
                ),
            )
        )
        self.assertEqual(consolidation.mode, "consolidation_status")
        self.assertIn("Konsolide Olmayan", consolidation.answer)

        balance = route_question(
            AskRequest(
                bank="ALL",
                question=(
                    "Akbank'in 2026 Q1 bilancosuna gore varliklar, yukumlulukler ve "
                    "ozkaynaklar birlikte degerlendirildiginde bilanco dengesi dogru "
                    "kurulmus mu?"
                ),
            )
        )
        self.assertEqual(balance.mode, "numeric_balance_check")
        self.assertIn("doğru kurulmuş", balance.answer)
        self.assertNotIn("tutarsız", balance.answer)

        liabilities = route_question(
            AskRequest(
                bank="ALL",
                question=(
                    "Is Bankasi'nin 2026 Q1 toplam yukumlulukleri onceki doneme gore "
                    "artmis mi? Degisim oranini hesapla."
                ),
            )
        )
        self.assertEqual(liabilities.mode, "numeric_liabilities")
        self.assertIn("7.30%", liabilities.answer)
        self.assertNotIn("Özkaynak hariç", liabilities.answer)

        document_location = route_question(
            AskRequest(
                bank="ALL",
                question=(
                    "Akbank'in 2026 Q1 finansal raporunda onemli muhasebe "
                    "politikalarina iliskin aciklama hangi bolumde yer aliyor?"
                ),
            )
        )
        self.assertEqual(document_location.mode, "document_location")
        self.assertIn("önemli muhasebe politikalarının özeti", document_location.answer)
        self.assertNotIn("Bağımsız Denetçi Görüşü / Giriş", document_location.answer)


@unittest.skipUnless(os.getenv("DATABASE_URL"), "DATABASE_URL is required for route tests")
class LatestReportFilterRegressionTests(unittest.TestCase):
    def test_collect_numeric_candidates_only_uses_latest_report(self) -> None:
        from app.db.connection import SessionLocal
        from app.db.models import Bank, FinancialMetric, Report
        from app.services.numeric_service import collect_numeric_candidates

        session = SessionLocal()

        try:
            ticker = "ZZTEST01"

            bank = Bank(ticker=ticker, company_name="Test Bank")
            session.add(bank)
            session.flush()

            old_report = Report(
                bank_id=bank.id,
                ticker=ticker,
                disclosure_id=f"{ticker}_OLD",
                publish_date=date(2026, 3, 31),
                period_code="2026_Q1",
            )
            new_report = Report(
                bank_id=bank.id,
                ticker=ticker,
                disclosure_id=f"{ticker}_NEW",
                publish_date=date(2026, 6, 30),
                period_code="2026_Q2",
            )
            session.add_all([old_report, new_report])
            session.flush()

            # Eski dönemin değeri kasıtlı olarak daha büyük: skor eşitliğinde
            # tie-break değere değil, en güncel rapora göre yapılmalı.
            session.add_all(
                [
                    FinancialMetric(
                        report_id=old_report.id,
                        ticker=ticker,
                        period="2026_Q1",
                        table_type="gelir_tablosu",
                        metric="DÖNEM NET KARI VEYA ZARARI",
                        unit="1.000 TL",
                        values_json={"cari_TOPLAM (31.03.2026)": "9.000.000"},
                    ),
                    FinancialMetric(
                        report_id=new_report.id,
                        ticker=ticker,
                        period="2026_Q2",
                        table_type="gelir_tablosu",
                        metric="DÖNEM NET KARI VEYA ZARARI",
                        unit="1.000 TL",
                        values_json={"cari_TOPLAM (30.06.2026)": "1.000.000"},
                    ),
                ]
            )
            session.flush()

            candidates = collect_numeric_candidates(
                session=session,
                bank=ticker,
                intent="net_kar",
            )

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].report_id, new_report.id)

        finally:
            # Gerçek DB'yi kalıcı olarak değiştirmemek için hiçbir zaman commit
            # etmiyoruz; test verisi rollback ile tamamen geri alınır.
            session.rollback()
            session.close()


if __name__ == "__main__":
    unittest.main()
