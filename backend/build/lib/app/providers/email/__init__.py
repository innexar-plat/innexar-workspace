"""Email providers."""
from app.providers.email.base import EmailProviderProtocol
from app.providers.email.smtp import SMTPProvider

__all__ = ["EmailProviderProtocol", "SMTPProvider"]
