"""PDF invoice and receipt generation service.

Generates simple, printable PDF invoices and payment receipts from
the Invoice and SaaSPayment ORM models.
"""

from __future__ import annotations

import io
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False


def _fmt_amount(amount: Decimal | float | int, currency: str = "USD") -> str:
    return f"{currency} {float(amount):,.2f}"


def _fmt_date(d: date | datetime | str | None) -> str:
    if d is None:
        return "N/A"
    if isinstance(d, str):
        return d
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def generate_invoice_pdf(
    invoice_number: str,
    hospital_name: str,
    plan_name: str,
    amount: Decimal | float | int,
    currency: str,
    due_date: date | datetime | str | None,
    billing_period_start: date | datetime | str | None = None,
    billing_period_end: date | datetime | str | None = None,
    status: str = "Unpaid",
) -> bytes:
    """Generate a printable invoice PDF and return the bytes."""
    if not HAS_REPORTLAB:
        return _fallback_text_pdf(
            title="INVOICE",
            lines=[
                f"Invoice Number: {invoice_number}",
                f"Hospital: {hospital_name}",
                f"Plan: {plan_name}",
                f"Amount: {_fmt_amount(amount, currency)}",
                f"Due Date: {_fmt_date(due_date)}",
                f"Billing Period: {_fmt_date(billing_period_start)} to {_fmt_date(billing_period_end)}",
                f"Status: {status}",
            ],
        )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph(f"<b>INVOICE</b>", styles["Title"]))
    elements.append(Spacer(1, 6 * mm))
    elements.append(Paragraph(f"<b>{hospital_name}</b>", styles["Normal"]))
    elements.append(Spacer(1, 4 * mm))

    data = [
        ["Invoice Number", invoice_number],
        ["Plan", plan_name],
        ["Billing Period", f"{_fmt_date(billing_period_start)} to {_fmt_date(billing_period_end)}"],
        ["Due Date", _fmt_date(due_date)],
        ["Status", status],
        ["Amount Due", _fmt_amount(amount, currency)],
    ]
    table = Table(data, colWidths=[120, 300])
    table.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 0), (0, -1), colors.Color(0.95, 0.95, 0.95)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])
    )
    elements.append(table)

    doc.build(elements)
    return buf.getvalue()


def generate_receipt_pdf(
    invoice_number: str,
    hospital_name: str,
    amount: Decimal | float | int,
    currency: str,
    payment_method: str,
    reference_number: str | None,
    paid_at: datetime | None = None,
) -> bytes:
    """Generate a printable payment receipt PDF and return the bytes."""
    if not HAS_REPORTLAB:
        return _fallback_text_pdf(
            title="PAYMENT RECEIPT",
            lines=[
                f"Invoice: {invoice_number}",
                f"Hospital: {hospital_name}",
                f"Amount Paid: {_fmt_amount(amount, currency)}",
                f"Payment Method: {payment_method}",
                f"Reference: {reference_number or 'N/A'}",
                f"Paid At: {_fmt_date(paid_at)}",
                "Status: PAID",
            ],
        )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("<b>PAYMENT RECEIPT</b>", styles["Title"]))
    elements.append(Spacer(1, 6 * mm))
    elements.append(Paragraph(f"<b>{hospital_name}</b>", styles["Normal"]))
    elements.append(Spacer(1, 4 * mm))

    data = [
        ["Invoice Number", invoice_number],
        ["Amount Paid", _fmt_amount(amount, currency)],
        ["Payment Method", payment_method],
        ["Reference Number", reference_number or "N/A"],
        ["Paid At", _fmt_date(paid_at)],
        ["Status", "PAID"],
    ]
    table = Table(data, colWidths=[120, 300])
    table.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 0), (0, -1), colors.Color(0.95, 0.95, 0.95)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])
    )
    elements.append(table)

    doc.build(elements)
    return buf.getvalue()


def _fallback_text_pdf(title: str, lines: list[str]) -> bytes:
    """Generate a minimal plain-text PDF when reportlab is unavailable."""
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    content = f"{title}\n" + "\n".join(f"  {l}" for l in lines)
    stream = content.encode("utf-8")
    obj_num = 1
    objects = []
    objects.append(f"{obj_num} 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj")
    obj_num += 1
    objects.append(f"{obj_num} 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj")
    obj_num += 1
    stream_obj = f"{obj_num} 0 obj\n<< /Length {len(stream)} >>\nstream\n{content}\nendstream"
    objects.append(stream_obj)
    obj_num += 1
    objects.append(f"{obj_num} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 3 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj")
    obj_num += 1
    objects.append(f"{obj_num} 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>\nendobj")

    xref_offset = None
    body = "\n".join(objects)
    output = f"{body}\ntrailer\n<< /Size {obj_num + 1} /Root 1 0 R >>\nstartxref\n{0}\n%%EOF"
    buf.write(output.encode("utf-8"))
    buf.seek(0)
    return buf.read()
