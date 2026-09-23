import os
import smtplib
import ssl
from email.message import EmailMessage


class EmailDeliveryError(RuntimeError):
    """Raised when an OTP email cannot be delivered."""


class EmailService:
    def send_otp(self, recipient: str, otp: str, purpose: str) -> None:
        host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        port = int(os.getenv("SMTP_PORT", "465"))
        username = os.getenv("SMTP_USERNAME", "").strip()
        password = os.getenv("SMTP_PASSWORD", "").replace(" ", "")
        sender = os.getenv("SMTP_FROM_EMAIL", username).strip()
        use_ssl = os.getenv("SMTP_USE_SSL", "true").lower() == "true"

        if not username or not password or not sender:
            raise EmailDeliveryError(
                "Email delivery is not configured. Add the SMTP settings to .env."
            )

        action = "complete registration" if purpose == "register" else "complete login"
        message = EmailMessage()
        message["Subject"] = "Your Renewable AI Coordinator verification code"
        message["From"] = sender
        message["To"] = recipient
        message.set_content(
            f"Your verification code is: {otp}\n\n"
            f"Use this code to {action}. It expires in 5 minutes.\n\n"
            "If you did not request this code, you can ignore this email."
        )
        message.add_alternative(
            f"""
            <div style="font-family:Arial,sans-serif;max-width:520px;margin:auto">
              <h2 style="color:#1677c8">Renewable AI Coordinator</h2>
              <p>Use this verification code to {action}:</p>
              <div style="font-size:34px;font-weight:700;letter-spacing:8px;
                          padding:18px;background:#f1f5f9;border-radius:10px;
                          text-align:center">{otp}</div>
              <p>This code expires in <strong>5 minutes</strong>.</p>
              <p style="color:#64748b">If you did not request this code, ignore this email.</p>
            </div>
            """,
            subtype="html",
        )

        try:
            context = ssl.create_default_context()
            if use_ssl:
                with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as smtp:
                    smtp.login(username, password)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(host, port, timeout=15) as smtp:
                    smtp.starttls(context=context)
                    smtp.login(username, password)
                    smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            raise EmailDeliveryError("The verification email could not be sent.") from error


email_service = EmailService()
