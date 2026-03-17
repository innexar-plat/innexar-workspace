"""SMTP email provider (config from env or constructor)."""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any

FROM_DISPLAY_NAME = "Innexar"


def _env_config() -> dict[str, Any]:
    """Read SMTP config from environment."""
    return {
        "host": os.environ.get("SMTP_HOST", "localhost"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "use_tls": os.environ.get("SMTP_USE_TLS", "1").lower() in ("1", "true", "yes"),
    }


class SMTPProvider:
    """SMTP implementation of EmailProviderProtocol."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
    ) -> None:
        env = _env_config()
        self.host = host or env["host"]
        self.port = port if port is not None else env["port"]
        self.user = user if user is not None else env["user"]
        self.password = password if password is not None else env["password"]
        self.use_tls = use_tls

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        html: str | None = None,
    ) -> None:
        """Send email via SMTP. Raises on failure."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        from_email = self.user or "noreply@innexar.com.br"
        msg["From"] = formataddr((FROM_DISPLAY_NAME, from_email))
        msg["To"] = to
        msg.attach(MIMEText(body, "plain", "utf-8"))
        if html:
            msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP(self.host, self.port) as server:
            if self.use_tls:
                server.starttls()
            if self.user and self.password:
                server.login(self.user, self.password)
            server.sendmail(from_email, to, msg.as_string())
