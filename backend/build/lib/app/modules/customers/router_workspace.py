"""Workspace routes: list/create/update/delete customers, send portal credentials, generate password."""
import secrets
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.rbac import RequirePermission
from app.core.security import hash_password
from app.models.customer import Customer
from app.models.customer_user import CustomerUser
from app.models.user import User
from app.modules.billing.models import Invoice, ProvisioningJob, ProvisioningRecord, Subscription
from app.modules.customers.schemas import (
    CustomerCreate,
    CustomerResponse,
    CustomerUpdate,
    GeneratePasswordResponse,
    SendCredentialsResponse,
)
from app.providers.email.loader import get_email_provider

router = APIRouter(prefix="/customers", tags=["workspace-customers"])


def _customer_to_response(c: Customer, has_portal_access: bool = False) -> dict:
    """Build response dict with safe serialization for JSON (address must be dict or None)."""
    address = c.address if isinstance(c.address, dict) else None
    return {
        "id": c.id,
        "org_id": str(c.org_id),
        "name": str(c.name),
        "email": str(c.email),
        "phone": c.phone,
        "address": address,
        "created_at": c.created_at,
        "has_portal_access": has_portal_access,
    }


@router.get("", response_model=list[CustomerResponse])
async def list_customers(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:read"))],
):
    """List all customers. has_portal_access is true when at least one CustomerUser exists."""
    r = await db.execute(
        select(Customer).options(selectinload(Customer.users)).order_by(Customer.id.desc())
    )
    customers = list(r.scalars().unique().all())
    return [
        _customer_to_response(c, has_portal_access=len(c.users) > 0)
        for c in customers
    ]


@router.post("", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def create_customer(
    body: CustomerCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:write"))],
):
    """Create a customer. Does not create portal user; use send-credentials to invite."""
    email_lower = body.email.lower().strip()
    existing = (
        await db.execute(select(Customer).where(Customer.email == email_lower).limit(1))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Customer with this email already exists",
        )
    customer = Customer(
        org_id="innexar",
        name=body.name.strip(),
        email=email_lower,
        phone=body.phone.strip() if body.phone else None,
        address=body.address,
    )
    db.add(customer)
    await db.flush()
    await db.refresh(customer)
    return _customer_to_response(customer, has_portal_access=False)


@router.get("/{customer_id}", response_model=CustomerResponse)
async def get_customer(
    customer_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:read"))],
):
    r = await db.execute(
        select(Customer)
        .options(selectinload(Customer.users))
        .where(Customer.id == customer_id)
        .limit(1)
    )
    c = r.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    return _customer_to_response(c, has_portal_access=len(c.users) > 0)


@router.patch("/{customer_id}", response_model=CustomerResponse)
async def update_customer(
    customer_id: int,
    body: CustomerUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:write"))],
):
    r = await db.execute(
        select(Customer)
        .options(selectinload(Customer.users))
        .where(Customer.id == customer_id)
        .limit(1)
    )
    c = r.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    if body.name is not None:
        c.name = body.name.strip()
    if body.email is not None:
        email_lower = body.email.lower().strip()
        existing = (
            await db.execute(
                select(Customer).where(Customer.email == email_lower, Customer.id != customer_id).limit(1)
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Outro cliente já usa este e-mail",
            )
        c.email = email_lower
    if body.phone is not None:
        c.phone = body.phone.strip() or None
    if body.address is not None:
        c.address = body.address
    await db.flush()
    await db.refresh(c)
    return _customer_to_response(c, has_portal_access=len(c.users) > 0)


@router.post("/cleanup-test", status_code=status.HTTP_200_OK)
async def cleanup_test_customers(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:write"))],
):
    """Delete all test customers (email @test.innexar.com, name 'Test Customer' or 'Acme Corp'), keeping INSTITUTO LASER OCULAR TOUFIC SLEIMAN."""
    keep_name = "INSTITUTO LASER OCULAR TOUFIC SLEIMAN"
    is_test = or_(
        Customer.email.like("%@test.innexar.com"),
        Customer.name == "Test Customer",
        Customer.name == "Acme Corp",
    )
    not_toufic = ~Customer.name.like(f"%{keep_name}%")
    r = await db.execute(
        select(Customer.id).where(and_(is_test, not_toufic))
    )
    ids_to_delete = [row[0] for row in r.scalars().all()]
    deleted = 0
    for customer_id in ids_to_delete:
        sub_ids = [
            s[0]
            for s in (
                await db.execute(
                    select(Subscription.id).where(Subscription.customer_id == customer_id)
                )
            ).scalars().all()
        ]
        for j in (
            await db.execute(
                select(ProvisioningJob).where(
                    ProvisioningJob.subscription_id.in_(sub_ids)
                )
            )
        ).scalars().all():
            await db.delete(j)
        for inv in (
            await db.execute(
                select(Invoice).where(Invoice.customer_id == customer_id)
            )
        ).scalars().all():
            await db.delete(inv)
        for rec in (
            await db.execute(
                select(ProvisioningRecord).where(
                    ProvisioningRecord.subscription_id.in_(sub_ids)
                )
            )
        ).scalars().all():
            await db.delete(rec)
        for sub in (
            await db.execute(
                select(Subscription).where(Subscription.customer_id == customer_id)
            )
        ).scalars().all():
            await db.delete(sub)
        for cu in (
            await db.execute(
                select(CustomerUser).where(CustomerUser.customer_id == customer_id)
            )
        ).scalars().all():
            await db.delete(cu)
        cust = (await db.execute(select(Customer).where(Customer.id == customer_id))).scalar_one_or_none()
        if cust:
            await db.delete(cust)
            deleted += 1
    await db.flush()
    return {"deleted": deleted, "message": f"Removidos {deleted} cliente(s) de teste."}


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer(
    customer_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:write"))],
):
    """Delete customer and related data: invoices, subscriptions, portal user."""
    r = await db.execute(
        select(Customer)
        .where(Customer.id == customer_id)
        .limit(1)
    )
    customer = r.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    sub_ids = [
        s[0]
        for s in (
            await db.execute(
                select(Subscription.id).where(Subscription.customer_id == customer_id)
            )
        ).scalars().all()
    ]

    for j in (
        await db.execute(
            select(ProvisioningJob).where(ProvisioningJob.subscription_id.in_(sub_ids))
        )
    ).scalars().all():
        await db.delete(j)
    for inv in (
        await db.execute(select(Invoice).where(Invoice.customer_id == customer_id))
    ).scalars().all():
        await db.delete(inv)
    for rec in (
        await db.execute(
            select(ProvisioningRecord).where(
                ProvisioningRecord.subscription_id.in_(sub_ids)
            )
        )
    ).scalars().all():
        await db.delete(rec)
    for sub in (
        await db.execute(select(Subscription).where(Subscription.customer_id == customer_id))
    ).scalars().all():
        await db.delete(sub)
    for cu in (
        await db.execute(select(CustomerUser).where(CustomerUser.customer_id == customer_id))
    ).scalars().all():
        await db.delete(cu)
    await db.delete(customer)
    await db.flush()


@router.post(
    "/{customer_id}/generate-password",
    response_model=GeneratePasswordResponse,
)
async def generate_password(
    customer_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:write"))],
):
    """Generate a new temporary password for the customer portal user. Returns password (admin can copy); does not send email. Use send-credentials to email it."""
    r = await db.execute(
        select(Customer)
        .options(selectinload(Customer.users))
        .where(Customer.id == customer_id)
        .limit(1)
    )
    customer = r.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    temporary_password = secrets.token_urlsafe(12)
    cu = (
        await db.execute(
            select(CustomerUser).where(CustomerUser.customer_id == customer_id).limit(1)
        )
    ).scalar_one_or_none()
    if cu:
        cu.password_hash = hash_password(temporary_password)
        await db.flush()
    else:
        cu = CustomerUser(
            customer_id=customer_id,
            email=customer.email,
            password_hash=hash_password(temporary_password),
            email_verified=False,
        )
        db.add(cu)
        await db.flush()
    return GeneratePasswordResponse(password=temporary_password)


async def _send_credentials_email(
    recipient_email: str,
    temporary_password: str,
    org_id: str,
) -> None:
    """Send email with portal URL, login and password. Uses new DB session for provider lookup."""
    from app.core.database import AsyncSessionLocal

    from app.modules.customers.email_templates import portal_credentials_email

    portal_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000").rstrip("/")
    login_url = f"{portal_url}/pt/login" if "portal." in portal_url else f"{portal_url}/portal/login"
    subject, body_plain, body_html = portal_credentials_email(
        login_url=login_url,
        recipient_email=recipient_email,
        temporary_password=temporary_password,
        after_payment=False,
    )
    async with AsyncSessionLocal() as db:
        provider = await get_email_provider(db, org_id=org_id)
        if provider:
            provider.send(recipient_email, subject, body_plain, body_html)


@router.post("/{customer_id}/send-credentials", response_model=SendCredentialsResponse)
async def send_credentials(
    customer_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    background_tasks: BackgroundTasks,
    current: Annotated[User, Depends(RequirePermission("billing:write"))],
):
    """Create or ensure CustomerUser for portal login and send email with URL, login and temporary password."""
    r = await db.execute(
        select(Customer)
        .options(selectinload(Customer.users))
        .where(Customer.id == customer_id)
        .limit(1)
    )
    customer = r.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    email = customer.email
    cu = (
        await db.execute(
            select(CustomerUser).where(CustomerUser.customer_id == customer_id).limit(1)
        )
    ).scalar_one_or_none()

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

    org_id = current.org_id or "innexar"
    background_tasks.add_task(
        _send_credentials_email,
        email,
        temporary_password,
        org_id,
    )
    return SendCredentialsResponse()
