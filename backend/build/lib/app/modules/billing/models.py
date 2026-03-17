"""Billing models: Product, PricePlan, Subscription, Invoice, PaymentAttempt, WebhookEvent, ProvisioningRecord, ProvisioningJob."""
from datetime import datetime
from typing import Any, TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.billing.enums import InvoiceStatus, SubscriptionStatus

if TYPE_CHECKING:
    from app.models.customer import Customer


class Product(Base):
    """Catalog product."""

    __tablename__ = "billing_products"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    org_id: Mapped[str] = mapped_column(String(64), default="innexar", index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    provisioning_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    hestia_package: Mapped[str | None] = mapped_column(String(128), nullable=True)

    price_plans: Mapped[list["PricePlan"]] = relationship(
        "PricePlan", back_populates="product", cascade="all, delete-orphan"
    )


class PricePlan(Base):
    """Price plan for a product (e.g. monthly/yearly)."""

    __tablename__ = "billing_price_plans"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("billing_products.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    interval: Mapped[str] = mapped_column(String(32), nullable=False)  # monthly, yearly
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="BRL")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    product: Mapped["Product"] = relationship("Product", back_populates="price_plans")


class Subscription(Base):
    """Customer subscription to a product/plan."""

    __tablename__ = "billing_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("billing_products.id"), nullable=False, index=True)
    price_plan_id: Mapped[int] = mapped_column(ForeignKey("billing_price_plans.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default=SubscriptionStatus.INACTIVE.value, index=True)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)  # MP preapproval_id
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    customer: Mapped["Customer"] = relationship("Customer", backref="subscriptions")
    product: Mapped["Product"] = relationship("Product", backref="subscriptions")
    price_plan: Mapped["PricePlan"] = relationship("PricePlan", backref="subscriptions")
    invoices: Mapped[list["Invoice"]] = relationship(
        "Invoice", back_populates="subscription", cascade="all, delete-orphan"
    )
    provisioning_records: Mapped[list["ProvisioningRecord"]] = relationship(
        "ProvisioningRecord", back_populates="subscription", cascade="all, delete-orphan"
    )


class Invoice(Base):
    """Invoice (single or from subscription)."""

    __tablename__ = "billing_invoices"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("billing_subscriptions.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default=InvoiceStatus.DRAFT.value, index=True)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="BRL")
    line_items: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    customer: Mapped["Customer"] = relationship("Customer", backref="invoices")
    subscription: Mapped["Subscription | None"] = relationship("Subscription", back_populates="invoices")
    payment_attempts: Mapped[list["PaymentAttempt"]] = relationship(
        "PaymentAttempt", back_populates="invoice", cascade="all, delete-orphan"
    )


class MPSubscriptionCheckout(Base):
    """Links an invoice to a Mercado Pago preapproval_plan for subscription checkout. Webhook uses mp_plan_id to find invoice."""

    __tablename__ = "billing_mp_subscription_checkouts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("billing_invoices.id"), nullable=False, index=True)
    mp_plan_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class PaymentAttempt(Base):
    """Single payment link / attempt for an invoice."""

    __tablename__ = "billing_payment_attempts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("billing_invoices.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    payment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="payment_attempts")


class WebhookEvent(Base):
    """Idempotency: processed webhook events."""

    __tablename__ = "billing_webhook_events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event_id"),)


class ProvisioningRecord(Base):
    """Record of provisioned hosting (e.g. Hestia user/domain) for a subscription."""

    __tablename__ = "provisioning_records"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("billing_subscriptions.id"), nullable=False, index=True
    )
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("billing_invoices.id"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    external_user: Mapped[str] = mapped_column(String(128), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    site_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    panel_login: Mapped[str | None] = mapped_column(String(128), nullable=True)
    panel_password_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
    panel_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    provisioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    subscription: Mapped["Subscription"] = relationship("Subscription", back_populates="provisioning_records")


class ProvisioningJob(Base):
    """Track provisioning run: steps, logs, status (queued → running → success/failed)."""

    __tablename__ = "billing_provisioning_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("billing_subscriptions.id"), nullable=False, index=True
    )
    invoice_id: Mapped[int] = mapped_column(ForeignKey("billing_invoices.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)  # queued|running|success|failed|retrying
    step: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # create_user|add_domain|enable_ssl|create_mail|finalize
    logs: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
