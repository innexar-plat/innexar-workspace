"""Stripe payment provider."""
import os
from typing import Any

from app.providers.payments.base import PaymentLinkResult, WebhookResult

try:
    import stripe
except ImportError:
    stripe = None  # type: ignore[assignment]


def _get_api_key() -> str | None:
    return os.environ.get("STRIPE_SECRET_KEY") or os.environ.get("STRIPE_API_KEY")


def _get_webhook_secret() -> str | None:
    return os.environ.get("STRIPE_WEBHOOK_SECRET")


class StripeProvider:
    """Stripe implementation of PaymentProviderProtocol."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or _get_api_key()
        if self._api_key and stripe is not None:
            stripe.api_key = self._api_key

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
        if stripe is None:
            raise RuntimeError("stripe package not installed")
        key = self._api_key or _get_api_key()
        if not key:
            raise ValueError("STRIPE_SECRET_KEY not configured")
        stripe.api_key = key
        amount_cents = int(round(amount * 100))
        session: Any = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": (currency or "brl").lower()[:3],
                        "product_data": {
                            "name": description or f"Invoice #{invoice_id}",
                        },
                        "unit_amount": amount_cents,
                    },
                    "quantity": 1,
                }
            ],
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=customer_email or None,
            metadata={"invoice_id": str(invoice_id)},
        )
        url = session.get("url") or ""
        return PaymentLinkResult(payment_url=url, external_id=session.get("id"))

    def handle_webhook(self, body: bytes, headers: dict[str, str]) -> WebhookResult:
        if stripe is None:
            raise RuntimeError("stripe package not installed")
        secret = _get_webhook_secret()
        if not secret:
            return WebhookResult(processed=False, message="webhook secret not configured")
        sig = headers.get("stripe-signature", "")
        try:
            event = stripe.Webhook.construct_event(body, sig, secret)
        except (ValueError, getattr(stripe, "SignatureVerificationError", ValueError)) as e:
            return WebhookResult(processed=False, message=str(e))
        event_id = event.get("id", "")
        event_type = event.get("type", "")
        if event_type == "checkout.session.completed":
            session = event.get("data", {}).get("object", {})
            metadata = session.get("metadata", {}) or {}
            invoice_id_str = metadata.get("invoice_id")
            invoice_id = int(invoice_id_str) if invoice_id_str else None
            return WebhookResult(processed=True, invoice_id=invoice_id, message=event_id)
        return WebhookResult(processed=True, message=event_id)
