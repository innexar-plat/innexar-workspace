"""Billing enums for Invoice and Subscription state machines."""
import enum


class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"
    ISSUED = "issued"
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    CANCELED = "canceled"
    EXPIRED = "expired"


class SubscriptionStatus(str, enum.Enum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    OVERDUE = "overdue"
    SUSPENDED = "suspended"
    CANCELED = "canceled"


class PaymentProvider(str, enum.Enum):
    STRIPE = "stripe"
    MERCADOPAGO = "mercadopago"
