"""Email provider protocol."""
from typing import Protocol


class EmailProviderProtocol(Protocol):
    """Protocol for email providers (SMTP, etc.)."""

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        html: str | None = None,
    ) -> None:
        """Send email to recipient. Raises on failure."""
        ...
