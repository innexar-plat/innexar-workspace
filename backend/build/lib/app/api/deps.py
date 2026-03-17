"""Shared API dependencies (re-exports for convenience)."""
from app.core.auth_customer import get_current_customer
from app.core.auth_staff import get_current_staff
from app.core.database import get_db

__all__ = ["get_db", "get_current_staff", "get_current_customer"]
