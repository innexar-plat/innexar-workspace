"""Customer service: send portal credentials (e.g. after first payment)."""
import secrets

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.customer import Customer
from app.models.customer_user import CustomerUser
from app.modules.customers.email_templates import portal_credentials_email
from app.providers.email.loader import get_email_provider
from sqlalchemy import select


async def send_portal_credentials_after_payment(customer_id: int, org_id: str = "innexar") -> None:
    """Create or update CustomerUser with temp password and send email with portal URL and credentials. Call from background task (e.g. after webhook marks invoice paid)."""
    async with AsyncSessionLocal() as db:
        try:
            r = await db.execute(
                select(Customer).where(Customer.id == customer_id).limit(1)
            )
            customer = r.scalar_one_or_none()
            if not customer or not customer.email:
                return
            email = customer.email
            cu_r = await db.execute(
                select(CustomerUser).where(CustomerUser.customer_id == customer_id).limit(1)
            )
            cu = cu_r.scalar_one_or_none()
            temporary_password = secrets.token_urlsafe(12)
            if cu:
                cu.password_hash = hash_password(temporary_password)
                await db.flush()
            else:
                cu = CustomerUser(
                    customer_id=customer_id,
                    email=email,
                    password_hash=hash_password(temporary_password),
                    email_verified=False,
                )
                db.add(cu)
                await db.flush()
            await db.commit()
            portal_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000").rstrip("/")
            login_url = f"{portal_url}/pt/login" if "portal." in portal_url else f"{portal_url}/portal/login"
            briefing_url = f"{portal_url}/pt/site-briefing" if "portal." in portal_url else f"{portal_url}/portal/site-briefing"
            subject, body_plain, body_html = portal_credentials_email(
                login_url=login_url,
                recipient_email=email,
                temporary_password=temporary_password,
                after_payment=True,
                briefing_url=briefing_url,
            )
            provider = await get_email_provider(db, org_id=org_id)
            if provider:
                provider.send(email, subject, body_plain, body_html)
        except Exception:
            await db.rollback()
            raise
