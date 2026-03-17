"""CRM business logic: deal won -> customer, optional billing/project."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.modules.crm.models import Contact, Deal, Lead


async def on_deal_won(db: AsyncSession, deal: Deal) -> None:
    """
    When deal status is set to 'ganho': create or link Customer from contact/lead.
    Optionally trigger billing/project in a later phase.
    """
    org_id = deal.org_id or "innexar"
    contact: Contact | None = None
    lead: Lead | None = None

    if deal.contato_id:
        r = await db.execute(
            select(Contact).where(Contact.id == deal.contato_id)
        )
        contact = r.scalar_one_or_none()
    if not contact and deal.lead_id:
        r = await db.execute(select(Lead).where(Lead.id == deal.lead_id))
        lead = r.scalar_one_or_none()
        if lead and lead.contact_id:
            r2 = await db.execute(
                select(Contact).where(Contact.id == lead.contact_id)
            )
            contact = r2.scalar_one_or_none()
        if not contact and lead:
            contact = Contact(
                org_id=org_id,
                name=lead.nome,
                email=lead.email,
                phone=lead.telefone,
            )
            db.add(contact)
            await db.flush()
            lead.contact_id = contact.id
            deal.contato_id = contact.id
            await db.flush()

    if not contact:
        return

    if contact.customer_id:
        return

    email = (contact.email or "").strip()
    if not email:
        return

    existing = await db.execute(
        select(Customer).where(
            Customer.org_id == org_id,
            Customer.email == email.lower(),
        ).limit(1)
    )
    customer = existing.scalar_one_or_none()
    if not customer:
        customer = Customer(
            org_id=org_id,
            name=contact.name,
            email=email.lower(),
            phone=contact.phone,
        )
        db.add(customer)
        await db.flush()

    contact.customer_id = customer.id
    await db.flush()
