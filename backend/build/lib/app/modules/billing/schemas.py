"""Billing Pydantic schemas."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


# ----- Product -----
class ProductBase(BaseModel):
    name: str
    description: str | None = None
    is_active: bool = True
    provisioning_type: str | None = None  # e.g. "hestia_hosting"
    hestia_package: str | None = None  # Hestia package name when provisioning_type is hestia_hosting


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None
    provisioning_type: str | None = None
    hestia_package: str | None = None


class ProductResponse(ProductBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    org_id: str
    created_at: datetime


# ----- PricePlan -----
class PricePlanBase(BaseModel):
    name: str
    interval: str
    amount: float
    currency: str = "BRL"


class PricePlanCreate(PricePlanBase):
    product_id: int


class PricePlanUpdate(BaseModel):
    name: str | None = None
    interval: str | None = None
    amount: float | None = None
    currency: str | None = None


class PricePlanResponse(PricePlanBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    created_at: datetime


class ProductWithPlansResponse(ProductResponse):
    """Product with price plans (preço e período)."""
    price_plans: list[PricePlanResponse] = []


# ----- Subscription -----
class SubscriptionBase(BaseModel):
    status: str = "inactive"
    start_date: datetime | None = None
    end_date: datetime | None = None
    next_due_date: datetime | None = None
    external_id: str | None = None  # MP preapproval_id when paid via Assinaturas


class SubscriptionCreate(BaseModel):
    customer_id: int
    product_id: int
    price_plan_id: int
    status: str = "inactive"
    start_date: datetime | None = None
    next_due_date: datetime | None = None


class SubscriptionUpdate(BaseModel):
    status: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    next_due_date: datetime | None = None


class LinkHestiaBody(BaseModel):
    """Link an existing Hestia user to a subscription (no provisioning run)."""

    hestia_username: str
    domain: str
    invoice_id: int | None = None


class SubscriptionResponse(SubscriptionBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int
    product_id: int
    price_plan_id: int
    created_at: datetime
    updated_at: datetime | None = None


# ----- Invoice -----
class InvoiceBase(BaseModel):
    status: str = "draft"
    due_date: datetime
    total: float
    currency: str = "BRL"
    line_items: dict[str, Any] | list[Any] | None = None


class InvoiceCreate(BaseModel):
    customer_id: int
    subscription_id: int | None = None
    due_date: datetime
    total: float
    currency: str = "BRL"
    line_items: dict[str, Any] | list[Any] | None = None


class InvoiceUpdate(BaseModel):
    status: str | None = None
    due_date: datetime | None = None
    total: float | None = None


class InvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int
    subscription_id: int | None
    status: str
    due_date: datetime
    paid_at: datetime | None
    total: float
    currency: str
    line_items: dict[str, Any] | list[Any] | None
    external_id: str | None
    created_at: datetime


# ----- Payment -----
class PayRequest(BaseModel):
    """Portal pay: Checkout Pro (success_url/cancel_url) or Bricks (payment_method_id + token)."""

    success_url: str = ""
    cancel_url: str = ""

    # Bricks (cartão/Pix): when present, pay inline instead of redirect to Checkout Pro
    token: str | None = None
    payment_method_id: str | None = None
    issuer_id: str | None = None
    installments: int = 1
    payer_email: str | None = None
    customer_name: str | None = None


class PayResponse(BaseModel):
    """payment_url + attempt_id for Checkout Pro; for Bricks also payment_status, payment_id, error_message, Pix fields."""

    payment_url: str = ""
    attempt_id: int = 0
    payment_status: str | None = None
    payment_id: str | None = None
    error_message: str | None = None
    qr_code_base64: str | None = None
    qr_code: str | None = None
    ticket_url: str | None = None


class PayBricksRequest(BaseModel):
    """Workspace: pay invoice with Bricks (staff initiates, payer_email required)."""

    token: str | None = None
    payment_method_id: str
    issuer_id: str | None = None
    installments: int = 1
    payer_email: str
    customer_name: str | None = None
