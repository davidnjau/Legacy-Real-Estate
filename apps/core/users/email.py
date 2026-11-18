import html
import json
import secrets
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _branded_html_email(subject, content):
    safe_subject = html.escape(subject)
    return f"""\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <title>{safe_subject}</title>
  </head>
  <body style="margin:0;padding:0;background-color:#f3f6f4;color:#17231d;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color:#f3f6f4;">
      <tr>
        <td align="center" style="padding:32px 16px;">
          <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:600px;border:1px solid #e2e9e4;border-radius:16px;overflow:hidden;background-color:#ffffff;">
            <tr>
              <td style="padding:24px 32px;background-color:#123b2c;">
                <div style="color:#ffffff;font-size:21px;font-weight:700;letter-spacing:0.2px;">Legacy Real Estate</div>
                <div style="margin-top:5px;color:#c6e4d2;font-size:12px;letter-spacing:1.2px;text-transform:uppercase;">Property management</div>
              </td>
            </tr>
            <tr>
              <td style="padding:32px;font-size:15px;line-height:1.65;color:#33443a;">
                {content}
              </td>
            </tr>
            <tr>
              <td style="padding:18px 32px;border-top:1px solid #e8eee9;background-color:#fbfcfb;color:#718078;font-size:12px;line-height:1.5;">
                This is an automated message from Legacy Real Estate. If you did not expect it, contact your property management team.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def _plain_text_html(text_content):
    paragraphs = (
        f'<p style="margin:0 0 16px;">{html.escape(line)}</p>'
        for line in text_content.splitlines()
        if line.strip()
    )
    return "".join(paragraphs)


def email_delivery_is_configured():
    backend = settings.EMAIL_DELIVERY_BACKEND
    if backend == "mailpit":
        return True
    if backend == "softlink":
        return bool(
            settings.SOFTLINK_EMAIL_API_KEY
            and settings.SOFTLINK_EMAIL_API_URL.startswith("https://")
        )
    if backend == "brevo":
        return bool(settings.BREVO_API_KEY and settings.BREVO_SENDER_EMAIL)
    return False


def send_email(
    recipient,
    subject,
    text_content,
    *,
    html_content=None,
    recipient_name=None,
    sender_name=None,
):
    backend = settings.EMAIL_DELIVERY_BACKEND
    html_content = _branded_html_email(
        subject,
        html_content if html_content else _plain_text_html(text_content),
    )
    if not email_delivery_is_configured():
        if backend == "softlink":
            raise ImproperlyConfigured(
                "SOFTLINK_EMAIL_API_KEY and an HTTPS SOFTLINK_EMAIL_API_URL "
                "are required to send email."
            )
        if backend == "brevo":
            raise ImproperlyConfigured(
                "BREVO_API_KEY and BREVO_SENDER_EMAIL are required to send email."
            )
        raise ImproperlyConfigured("Email delivery is not configured.")

    if backend == "mailpit":
        from django.core.mail import EmailMultiAlternatives

        message = EmailMultiAlternatives(
            subject,
            text_content,
            settings.DEFAULT_FROM_EMAIL,
            [recipient],
        )
        if html_content:
            message.attach_alternative(html_content, "text/html")
        if message.send(fail_silently=False) != 1:
            raise RuntimeError("Email backend did not accept the message.")
        return

    if backend == "brevo":
        payload = {
            "sender": {
                "name": sender_name or settings.BREVO_SENDER_NAME,
                "email": settings.BREVO_SENDER_EMAIL,
            },
            "to": [
                {
                    "email": recipient,
                    **({"name": recipient_name} if recipient_name else {}),
                }
            ],
            "subject": subject,
            "textContent": text_content,
        }
        if html_content:
            payload["htmlContent"] = html_content
        request = Request(
            "https://api.brevo.com/v3/smtp/email",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "accept": "application/json",
                "api-key": settings.BREVO_API_KEY,
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"Brevo rejected the email (HTTP {response.status}).")
        except HTTPError as exc:
            raise RuntimeError(f"Brevo rejected the email (HTTP {exc.code}).") from None
        except (URLError, TimeoutError) as exc:
            raise RuntimeError("Brevo email service could not be reached.") from exc
        return

    payload = json.dumps({
        "recipients": [recipient],
        "subject": subject,
        "body": html_content,
    }).encode("utf-8")
    request = Request(
        settings.SOFTLINK_EMAIL_API_URL,
        data=payload,
        headers={
            "X-Api-Key": settings.SOFTLINK_EMAIL_API_KEY,
            "Idempotency-Key": secrets.token_hex(16),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.SOFTLINK_EMAIL_TIMEOUT_SECONDS) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(
                    f"Softlink rejected the email (HTTP {response.status})."
                )
    except HTTPError as exc:
        raise RuntimeError(
            f"Softlink rejected the email (HTTP {exc.code})."
        ) from None
    except URLError as exc:
        raise RuntimeError("Softlink email service could not be reached.") from exc
    except TimeoutError as exc:
        raise RuntimeError("Softlink email request timed out.") from exc
