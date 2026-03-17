"""Hosting provider protocol (e.g. Hestia)."""
from typing import Protocol


class HostingProviderProtocol(Protocol):
    """Protocol for hosting providers (create account, domain, mail, suspend/unsuspend)."""

    def create_user(
        self,
        user: str,
        password: str,
        email: str,
        package: str = "default",
    ) -> None:
        """Create system user (account)."""
        ...

    def ensure_domain(self, user: str, domain: str, **kwargs: str) -> None:
        """Ensure web domain exists for user. Idempotent where possible."""
        ...

    def ensure_mail(self, user: str, domain: str, enabled: bool = True) -> None:
        """Ensure mail is configured for domain. When enabled=False, no-op or disable."""
        ...

    def suspend_user(self, user: str, reason: str = "yes") -> None:
        """Suspend user (e.g. overdue)."""
        ...

    def unsuspend_user(self, user: str) -> None:
        """Unsuspend user (e.g. after payment)."""
        ...

    def healthcheck(self) -> bool:
        """Test connection (e.g. list users or lightweight command). Returns True if OK."""
        ...
