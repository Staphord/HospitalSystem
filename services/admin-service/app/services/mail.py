from __future__ import annotations

import html
import logging
from urllib.parse import urlparse

from fastapi import Request

from app.config import settings

logger = logging.getLogger("admin_service.mail")


def login_url_from_request(request: Request) -> str:
    referer = request.headers.get("referer")
    origin = request.headers.get("origin")
    if referer:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}/login"
    if origin:
        return origin.rstrip("/") + "/login"
    configured = (settings.frontend_url or "https://gilgali.com/login").rstrip("/")
    if configured.endswith("/login"):
        return configured
    return configured + "/login"


async def send_staff_welcome_email(
    *,
    email: str,
    full_name: str,
    username: str,
    password: str | None,
    role: str,
    hospital_name: str,
    login_url: str,
) -> None:
    import aiosmtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    display_name = full_name.strip() or username
    role_label = (role or "staff").replace("_", " ").title()
    password_html = (
        f"<li><strong>Temporary password:</strong> <code>{html.escape(password)}</code></li>"
        if password
        else ""
    )
    password_text = f"Temporary password: {password}\n" if password else ""

    subject = f"Your {hospital_name} staff account"
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
      <h2 style="color: #0052cc;">Welcome to {html.escape(hospital_name)}</h2>
      <p>Dear {html.escape(display_name)},</p>
      <p>A staff account has been created for you. Use the details below to sign in.
         You will be asked to change your password on first login.</p>
      <hr/>
      <ul>
        <li><strong>Hospital:</strong> {html.escape(hospital_name)}</li>
        <li><strong>Role:</strong> {html.escape(role_label)}</li>
        <li><strong>Username:</strong> {html.escape(username)}</li>
        {password_html}
        <li><strong>Login:</strong> <a href="{html.escape(login_url)}">{html.escape(login_url)}</a></li>
      </ul>
      <p>If you were not expecting this email, contact your hospital administrator.</p>
    </body>
    </html>
    """
    text_body = f"""Welcome to {hospital_name}

A staff account has been created for you.

Hospital: {hospital_name}
Role: {role_label}
Username: {username}
{password_text}Login: {login_url}

You will be asked to change your password on first login.
"""

    if not settings.smtp_user or not settings.smtp_password:
        logger.info("[MOCK STAFF WELCOME EMAIL TO %s]", email)
        logger.info("Hospital: %s | Username: %s | Role: %s | Login: %s", hospital_name, username, role_label, login_url)
        if password:
            logger.info("Temporary password: %s", password)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            start_tls=settings.smtp_port == 587,
            use_tls=settings.smtp_port == 465,
        )
        logger.info("Sent staff welcome email to %s", email)
    except Exception:
        logger.exception("Failed to send staff welcome email to %s", email)
