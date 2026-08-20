"""Unit tests for pdf_generator.py in master-service.
"""
from unittest.mock import MagicMock, patch
import pytest
from datetime import date, datetime, timezone
from decimal import Decimal

import app.services.pdf_generator as pdf_gen
from app.services.pdf_generator import (
    _fmt_amount,
    _fmt_date,
    generate_invoice_pdf,
    generate_receipt_pdf,
    _fallback_text_pdf,
)

def test_fmt_amount_and_date():
    assert _fmt_amount(100.5, "EUR") == "EUR 100.50"
    assert _fmt_amount(Decimal("250.75"), "USD") == "USD 250.75"

    assert _fmt_date(None) == "N/A"
    assert _fmt_date("2026-08-20") == "2026-08-20"
    dt = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
    assert _fmt_date(dt) == "2026-08-20"
    d = date(2026, 8, 20)
    assert _fmt_date(d) == "2026-08-20"
    
    class CustomObj:
        def __str__(self):
            return "custom"
    assert _fmt_date(CustomObj()) == "custom"

def test_generate_invoice_pdf_reportlab_real():
    res = generate_invoice_pdf(
        invoice_number="INV-100",
        hospital_name="St. Jude Hospital",
        plan_name="Standard",
        amount=500.0,
        currency="USD",
        due_date=date(2026, 9, 1),
        billing_period_start=date(2026, 8, 1),
        billing_period_end=date(2026, 8, 31),
        status="Unpaid",
    )
    assert isinstance(res, bytes)
    assert len(res) > 0

def test_generate_receipt_pdf_reportlab_real():
    res = generate_receipt_pdf(
        invoice_number="INV-100",
        hospital_name="St. Jude Hospital",
        amount=500.0,
        currency="USD",
        payment_method="Credit Card",
        reference_number="REF-999",
        paid_at=datetime.now(timezone.utc),
    )
    assert isinstance(res, bytes)
    assert len(res) > 0

def test_generate_invoice_and_receipt_pdf_reportlab_mocked():
    mock_doc = MagicMock()
    mock_doc.build = MagicMock()
    
    with patch.object(pdf_gen, "HAS_REPORTLAB", True):
        with patch.object(pdf_gen, "SimpleDocTemplate", return_value=mock_doc, create=True):
            with patch.object(pdf_gen, "Paragraph", return_value=MagicMock(), create=True):
                with patch.object(pdf_gen, "Spacer", return_value=MagicMock(), create=True):
                    with patch.object(pdf_gen, "Table", return_value=MagicMock(), create=True):
                        with patch.object(pdf_gen, "TableStyle", return_value=MagicMock(), create=True):
                            with patch.object(pdf_gen, "getSampleStyleSheet", return_value={"Title": MagicMock(), "Normal": MagicMock()}, create=True):
                                with patch.object(pdf_gen, "A4", (595.27, 841.89), create=True):
                                    with patch.object(pdf_gen, "mm", 2.83, create=True):
                                        with patch.object(pdf_gen, "colors", MagicMock(), create=True):
                                            pdf_inv = pdf_gen.generate_invoice_pdf(
                                                invoice_number="INV-001",
                                                hospital_name="St. Jude Hospital",
                                                plan_name="Enterprise",
                                                amount=500.00,
                                                currency="USD",
                                                due_date=date(2026, 9, 1),
                                                billing_period_start=date(2026, 8, 1),
                                                billing_period_end=date(2026, 8, 31),
                                                status="Unpaid",
                                            )
                                            assert isinstance(pdf_inv, bytes)

                                            pdf_rec = pdf_gen.generate_receipt_pdf(
                                                invoice_number="INV-001",
                                                hospital_name="St. Jude Hospital",
                                                amount=500.00,
                                                currency="USD",
                                                payment_method="stripe",
                                                reference_number="ch_123456",
                                                paid_at=datetime.now(timezone.utc),
                                            )
                                            assert isinstance(pdf_rec, bytes)

def test_generate_invoice_and_receipt_pdf_fallback():
    with patch.object(pdf_gen, "HAS_REPORTLAB", False):
        pdf_inv = pdf_gen.generate_invoice_pdf(
            invoice_number="INV-002",
            hospital_name="City Hospital",
            plan_name="Standard",
            amount=200.00,
            currency="USD",
            due_date=None,
        )
        assert b"INVOICE" in pdf_inv
        assert pdf_inv.startswith(b"%PDF")

        pdf_rec = pdf_gen.generate_receipt_pdf(
            invoice_number="INV-002",
            hospital_name="City Hospital",
            amount=200.00,
            currency="USD",
            payment_method="bank_transfer",
            reference_number=None,
            paid_at=None,
        )
        assert b"PAYMENT RECEIPT" in pdf_rec
        assert pdf_rec.startswith(b"%PDF")

def test_fallback_text_pdf_directly():
    buf = _fallback_text_pdf("TEST TITLE", ["Line 1", "Line 2"])
    assert b"TEST TITLE" in buf
    assert b"Line 1" in buf
    assert buf.startswith(b"%PDF-1.4")
