"""Checkout public API schemas."""
from pydantic import BaseModel, EmailStr


class CheckoutStartRequest(BaseModel):
    """Start checkout: product/plan + customer email + URLs. For hosting products, domain is required."""

    product_id: int
    price_plan_id: int
    customer_email: EmailStr
    customer_name: str | None = None
    customer_phone: str | None = None
    success_url: str
    cancel_url: str
    domain: str | None = None  # Required when product.provisioning_type == hestia_hosting

    # Bricks Payment Brick fields (token is optional for Pix)
    token: str | None = None
    payment_method_id: str | None = None
    issuer_id: str | None = None
    installments: int = 1
    payer_email: str | None = None  # fallback to customer_email if not set

    # Contrato de fidelidade 12 meses (assinatura site)
    fidelity_12_months_accepted: bool | None = None


class CheckoutStartResponse(BaseModel):
    """Checkout start result."""

    payment_url: str | None = None  # For Checkout Pro redirect (legacy)
    payment_status: str | None = None  # For Bricks: approved, rejected, pending, etc.
    payment_id: str | None = None  # MP payment id
    existing_customer: bool = False
    error_message: str | None = None  # User-friendly error if payment rejected
    
    # Pix / Ticket response fields
    qr_code_base64: str | None = None
    qr_code: str | None = None
    ticket_url: str | None = None
