import smtplib
from email.message import EmailMessage

from app.core.config import settings


class EmailNotConfigured(RuntimeError):
    pass


class EmailDeliveryError(RuntimeError):
    pass


class EmailDeliveryRejected(EmailDeliveryError):
    pass


def send_email(
    *,
    recipient: str,
    subject: str,
    body: str,
    message_id: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    if not settings.smtp_host or not settings.smtp_from_email:
        raise EmailNotConfigured("Email sending is disabled. Configure SMTP_HOST and SMTP_FROM_EMAIL.")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from_email
    message["To"] = recipient
    message["Message-ID"] = message_id
    message.set_content(body)
    for filename, content, content_type in attachments or []:
        maintype, _, subtype = (content_type or "application/octet-stream").partition("/")
        message.add_attachment(content, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=filename)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.ehlo()
            if settings.smtp_starttls:
                smtp.starttls()
                smtp.ehlo()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            refused = smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailDeliveryError("The SMTP provider did not confirm the delivery outcome.") from exc
    if refused:
        raise EmailDeliveryRejected("The SMTP provider rejected the recipient.")
