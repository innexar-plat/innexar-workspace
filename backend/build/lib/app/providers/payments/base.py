"""Payment provider protocol."""
from dataclasses import dataclass
from typing import Protocol


@dataclass
class PaymentLinkResult:
    """Result of creating a payment link."""

    payment_url: str
    external_id: str | None = None


@dataclass
class WebhookResult:
    """Result of processing a webhook."""

    processed: bool
    invoice_id: int | None = None
    message: str = ""
    mp_preapproval_id: str | None = None
    mp_plan_id: str | None = None


class PaymentProviderProtocol(Protocol):
    """Protocol for payment providers (Stripe, Mercado Pago)."""

    def create_payment_link(
        self,
        invoice_id: int,
        amount: float,
        currency: str,
        success_url: str,
        cancel_url: str,
        customer_email: str | None = None,
        customer_name: str | None = None,
        customer_phone: str | None = None,
        description: str | None = None,
    ) -> PaymentLinkResult:
        """Create checkout/link and return URL + external id."""
        ...

    def handle_webhook(self, body: bytes, headers: dict[str, str]) -> WebhookResult:
        """Verify signature, parse event, return result (caller updates DB)."""
        ...
