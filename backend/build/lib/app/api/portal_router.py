"""Portal API: customer-only routes."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_customer import get_current_customer
from app.core.database import get_db
from app.core.feature_flags import get_flag
from app.core.security import hash_password, verify_password
from app.models.customer import Customer
from app.models.customer_user import CustomerUser
from app.models.project_request import ProjectRequest
from app.modules.projects.models import Project
from app.schemas.auth import ChangePasswordRequest, CustomerMeResponse
from app.modules.billing.enums import InvoiceStatus, SubscriptionStatus
from app.modules.billing.models import (
    Invoice,
    PricePlan,
    Product,
    ProvisioningRecord,
    Subscription,
)
from app.modules.support.models import Ticket, TicketMessage
from app.models.notification import Notification
from app.modules.files.models import ProjectFile

router = APIRouter()


class NewProjectRequest(BaseModel):
    """Body for POST /new-project (portal)."""

    project_name: str
    project_type: str
    description: str | None = None
    budget: str | None = None
    timeline: str | None = None


class SiteBriefingRequest(BaseModel):
    """Body for POST /site-briefing (portal): dados do site para criar projeto + ticket."""

    company_name: str
    services: str | None = None
    city: str | None = None
    whatsapp: str | None = None
    domain: str | None = None
    logo_url: str | None = None
    colors: str | None = None
    photos: str | None = None


class ProfileRead(BaseModel):
    """Portal customer profile (GET /me/profile)."""

    name: str
    email: str
    phone: str | None
    address: dict[str, Any] | None


class ProfileUpdate(BaseModel):
    """Body for PATCH /me/profile (name, phone, address only; email read-only)."""

    name: str | None = None
    phone: str | None = None
    address: dict[str, Any] | None = None


@router.post("/new-project", status_code=201)
async def portal_new_project(
    body: NewProjectRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
) -> dict[str, int | str]:
    """Portal: submit a new project request. Persisted for staff to review."""
    req = ProjectRequest(
        customer_id=current.customer_id,
        project_name=body.project_name.strip(),
        project_type=body.project_type.strip(),
        description=body.description.strip() if body.description else None,
        budget=body.budget.strip() if body.budget else None,
        timeline=body.timeline.strip() if body.timeline else None,
        status="pending",
    )
    db.add(req)
    await db.flush()
    await db.refresh(req)
    return {"id": req.id, "message": "Solicitação enviada. Nossa equipe entrará em contato."}


@router.post("/site-briefing", status_code=201)
async def portal_site_briefing(
    body: SiteBriefingRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
) -> dict[str, int | str | None]:
    """Portal: submit site briefing. If customer has a project aguardando_briefing, links it and updates status to briefing_recebido. Creates ProjectRequest + Ticket (if enabled)."""
    company = body.company_name.strip() or "Sem nome"
    description_parts = [
        f"Empresa: {company}",
        body.services and f"Serviços: {body.services}",
        body.city and f"Cidade: {body.city}",
        body.whatsapp and f"WhatsApp: {body.whatsapp}",
        body.domain and f"Domínio: {body.domain}",
        body.logo_url and f"Logo: {body.logo_url}",
        body.colors and f"Cores: {body.colors}",
        body.photos and f"Fotos: {body.photos}",
    ]
    description = "\n".join(p for p in description_parts if p)
    meta: dict[str, Any] = {
        "company_name": company,
        "services": body.services,
        "city": body.city,
        "whatsapp": body.whatsapp,
        "domain": body.domain,
        "logo_url": body.logo_url,
        "colors": body.colors,
        "photos": body.photos,
    }

    # Find a project aguardando_briefing for this customer without a linked briefing yet
    linked_project_id: int | None = None
    r_proj = await db.execute(
        select(Project)
        .where(
            Project.customer_id == current.customer_id,
            Project.status == "aguardando_briefing",
        )
        .where(
            ~Project.id.in_(
                select(ProjectRequest.project_id).where(ProjectRequest.project_id.isnot(None))
            )
        )
        .order_by(Project.id.desc())
        .limit(1)
    )
    project_to_link = r_proj.scalar_one_or_none()
    if project_to_link:
        linked_project_id = project_to_link.id

    req = ProjectRequest(
        customer_id=current.customer_id,
        project_id=linked_project_id,
        project_name=company,
        project_type="site",
        description=description,
        status="pending",
        meta=meta,
    )
    db.add(req)
    await db.flush()
    if linked_project_id is not None:
        project_to_link.status = "briefing_recebido"
        await db.flush()
    await db.refresh(req)

    ticket_id: int | None = None
    tickets_enabled = await get_flag(db, "portal.tickets.enabled")
    if tickets_enabled:
        t = Ticket(
            customer_id=current.customer_id,
            subject=f"Novo site - {company}",
            status="open",
            category="novo_projeto",
            project_id=linked_project_id,
        )
        db.add(t)
        await db.flush()
        ticket_id = t.id
        msg = TicketMessage(
            ticket_id=t.id,
            author_type="customer",
            body=description,
        )
        db.add(msg)
        await db.flush()
    return {
        "id": req.id,
        "project_id": linked_project_id or req.id,
        "ticket_id": ticket_id,
        "message": "Dados do site enviados. Nossa equipe entrará em contato.",
    }


@router.get("/me", response_model=CustomerMeResponse)
async def customer_me(
    current_user: Annotated[CustomerUser, Depends(get_current_customer)],
) -> CustomerMeResponse:
    """Portal: current customer user profile."""
    return CustomerMeResponse(
        id=current_user.id,
        email=current_user.email,
        customer_id=current_user.customer_id,
        email_verified=current_user.email_verified,
    )


@router.patch("/me/password", status_code=200)
async def customer_change_password(
    body: ChangePasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CustomerUser, Depends(get_current_customer)],
) -> dict[str, str]:
    """Portal: change password for current customer (current + new password)."""
    if len(body.new_password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nova senha deve ter no mínimo 6 caracteres",
        )
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Senha atual incorreta",
        )
    current_user.password_hash = hash_password(body.new_password)
    await db.flush()
    return {"message": "Senha alterada com sucesso."}


@router.get("/me/profile", response_model=ProfileRead)
async def get_my_profile(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
) -> ProfileRead:
    """Portal: get current customer profile (name, email, phone, address)."""
    r = await db.execute(
        select(Customer).where(Customer.id == current.customer_id)
    )
    cust = r.scalar_one_or_none()
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found")
    return ProfileRead(
        name=cust.name,
        email=cust.email,
        phone=cust.phone,
        address=cust.address,
    )


@router.patch("/me/profile", response_model=ProfileRead)
async def update_my_profile(
    body: ProfileUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
) -> ProfileRead:
    """Portal: update current customer profile (name, phone, address; email read-only)."""
    r = await db.execute(
        select(Customer).where(Customer.id == current.customer_id)
    )
    cust = r.scalar_one_or_none()
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found")
    if body.name is not None:
        cust.name = body.name.strip()
    if body.phone is not None:
        cust.phone = body.phone.strip() or None
    if body.address is not None:
        cust.address = body.address if body.address else None
    await db.flush()
    await db.refresh(cust)
    return ProfileRead(
        name=cust.name,
        email=cust.email,
        phone=cust.phone,
        address=cust.address,
    )


@router.get("/me/features")
async def customer_me_features(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CustomerUser, Depends(get_current_customer)],
) -> dict[str, bool]:
    """Portal: feature flags for menu visibility (invoices, tickets, projects)."""
    return {
        "invoices": await get_flag(db, "billing.enabled") or await get_flag(db, "portal.invoices.enabled"),
        "tickets": await get_flag(db, "portal.tickets.enabled"),
        "projects": await get_flag(db, "portal.projects.enabled"),
    }


@router.get("/me/project-aguardando-briefing")
async def get_project_aguardando_briefing(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
) -> dict[str, Any] | None:
    """Portal: return one project for current customer with status aguardando_briefing and no linked briefing yet (for site-briefing upload)."""
    r = await db.execute(
        select(Project)
        .where(
            Project.customer_id == current.customer_id,
            Project.status == "aguardando_briefing",
        )
        .where(
            ~Project.id.in_(
                select(ProjectRequest.project_id).where(ProjectRequest.project_id.isnot(None))
            )
        )
        .order_by(Project.id.desc())
        .limit(1)
    )
    p = r.scalar_one_or_none()
    if not p:
        return None
    return {"id": p.id, "name": p.name, "status": p.status}


@router.get("/me/dashboard")
async def customer_dashboard(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
) -> dict[str, Any]:
    """Portal: dashboard for client (plan, site, invoice, pay button, panel login, support, messages)."""
    customer_id = current.customer_id
    plan: dict[str, Any] | None = None
    site: dict[str, Any] | None = None
    invoice: dict[str, Any] | None = None
    can_pay_invoice = False
    panel: dict[str, Any] | None = None

    # All subscriptions for customer; prefer one with Hestia provisioning (hosting) for plan/site/panel
    sub_r = await db.execute(
        select(Subscription, Product, PricePlan)
        .join(Product, Subscription.product_id == Product.id)
        .join(PricePlan, Subscription.price_plan_id == PricePlan.id)
        .where(Subscription.customer_id == customer_id)
        .order_by(Subscription.id.desc())
    )
    sub_rows = sub_r.all()
    # Prefer subscription that has Hestia provisioning (hosting), so "Plano" and "Site" show correctly
    chosen_sub: tuple[Subscription, Product, PricePlan] | None = None
    for sub_row in sub_rows:
        sub, _product, _price_plan = sub_row
        rec_r = await db.execute(
            select(ProvisioningRecord)
            .where(
                ProvisioningRecord.subscription_id == sub.id,
                ProvisioningRecord.provider == "hestia",
            )
            .limit(1)
        )
        has_hestia = rec_r.scalar_one_or_none() is not None
        if has_hestia and sub.status == SubscriptionStatus.ACTIVE.value:
            chosen_sub = sub_row
            break
    if chosen_sub is None and sub_rows:
        # Fallback: use first subscription with hestia (even if inactive) so site/panel appear
        for sub_row in sub_rows:
            sub, _, _ = sub_row
            rec_r = await db.execute(
                select(ProvisioningRecord)
                .where(
                    ProvisioningRecord.subscription_id == sub.id,
                    ProvisioningRecord.provider == "hestia",
                )
                .limit(1)
            )
            if rec_r.scalar_one_or_none() is not None:
                chosen_sub = sub_row
                break
    if chosen_sub is None and sub_rows:
        chosen_sub = sub_rows[0]

    if chosen_sub:
        sub, product, price_plan = chosen_sub
        plan = {
            "status": sub.status,
            "product_name": product.name,
            "price_plan_name": price_plan.name,
            "since": sub.start_date.isoformat() if sub.start_date else None,
            "next_due_date": sub.next_due_date.isoformat() if getattr(sub, "next_due_date", None) else None,
        }
        inv_r = await db.execute(
            select(Invoice)
            .where(Invoice.subscription_id == sub.id)
            .order_by(Invoice.id.desc())
            .limit(1)
        )
        inv = inv_r.scalar_one_or_none()
        if inv:
            invoice = {
                "id": inv.id,
                "status": inv.status,
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
                "total": float(inv.total),
                "currency": inv.currency,
            }
            can_pay_invoice = inv.status == InvoiceStatus.PENDING.value
        rec_r = await db.execute(
            select(ProvisioningRecord)
            .where(
                ProvisioningRecord.subscription_id == sub.id,
                ProvisioningRecord.provider == "hestia",
            )
            .order_by(ProvisioningRecord.id.desc())
            .limit(1)
        )
        rec = rec_r.scalar_one_or_none()
        if rec:
            site = {
                "url": rec.site_url,
                "status": rec.status,
                "domain": rec.domain,
            }
            panel = {
                "login": rec.panel_login or "",
                "panel_url": rec.panel_url,
                "password_hint": "••••••" if rec.panel_password_encrypted else None,
            }

    # If no invoice from subscription, show latest customer invoice (e.g. standalone/service without Hestia)
    if invoice is None:
        inv_standalone_r = await db.execute(
            select(Invoice)
            .where(Invoice.customer_id == customer_id)
            .order_by(Invoice.id.desc())
            .limit(1)
        )
        inv_standalone = inv_standalone_r.scalar_one_or_none()
        if inv_standalone:
            invoice = {
                "id": inv_standalone.id,
                "status": inv_standalone.status,
                "due_date": inv_standalone.due_date.isoformat() if inv_standalone.due_date else None,
                "total": float(inv_standalone.total),
                "currency": inv_standalone.currency,
            }
            can_pay_invoice = inv_standalone.status == InvoiceStatus.PENDING.value

    tickets_r = await db.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.customer_id == customer_id,
            Ticket.status == "open",
        )
    )
    tickets_open_count = tickets_r.scalar() or 0
    unread_r = await db.execute(
        select(func.count()).select_from(Notification).where(
            Notification.customer_user_id == current.id,
            Notification.read_at.is_(None),
        )
    )
    unread_count = unread_r.scalar() or 0

    # Projects for this customer (id, name, status, created_at, has_files)
    projects_r = await db.execute(
        select(Project)
        .where(Project.customer_id == customer_id)
        .order_by(Project.id.desc())
    )
    projects_rows = projects_r.scalars().all()
    projects_aguardando_briefing: list[dict[str, Any]] = []
    projects_summary: list[dict[str, Any]] = []
    for p in projects_rows:
        files_count_r = await db.execute(
            select(func.count()).select_from(ProjectFile).where(ProjectFile.project_id == p.id)
        )
        files_count = files_count_r.scalar() or 0
        item = {
            "id": p.id,
            "name": p.name,
            "status": p.status,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "has_files": files_count > 0,
            "files_count": files_count,
        }
        projects_summary.append(item)
        if p.status == "aguardando_briefing":
            projects_aguardando_briefing.append(item)

    # Dynamic portal: show_briefing only when customer has site product and project awaiting briefing
    products_summary: list[dict[str, Any]] = []
    has_site_delivery_product = False
    subs_products_r = await db.execute(
        select(Product)
        .join(Subscription, Subscription.product_id == Product.id)
        .where(
            Subscription.customer_id == customer_id,
            Subscription.status == SubscriptionStatus.ACTIVE.value,
        )
    )
    for (product,) in subs_products_r.all():
        prov_type = (product.provisioning_type or "").strip() or None
        products_summary.append({
            "product_name": product.name,
            "provisioning_type": prov_type,
        })
        if (prov_type or "").lower() == "site_delivery":
            has_site_delivery_product = True
    show_briefing = has_site_delivery_product and len(projects_aguardando_briefing) > 0
    show_panel = panel is not None
    _has_hestia_hosting = any(
        (p.get("provisioning_type") or "").lower() == "hestia_hosting"
        for p in products_summary
    )
    # Sidebar: "Projetos" só para quem tem produto site; "Dados do site" só para quem assinou o site (não só hospedagem)
    nav_show_projects = has_site_delivery_product
    nav_show_hosting = has_site_delivery_product  # aba "Dados do site" (briefing) só para assinantes de site

    out: dict[str, Any] = {
        "plan": plan,
        "site": site,
        "invoice": invoice,
        "can_pay_invoice": can_pay_invoice,
        "panel": panel,
        "support": {"tickets_open_count": tickets_open_count},
        "messages": {"unread_count": unread_count},
        "projects": projects_summary,
        "projects_aguardando_briefing": projects_aguardando_briefing,
        "show_briefing": show_briefing,
        "show_panel": show_panel,
        "products_summary": products_summary,
        "nav_show_projects": nav_show_projects,
        "nav_show_hosting": nav_show_hosting,
    }
    # Diagnóstico quando plano não aparece: ajuda a ver se o problema é falta de dados no banco
    if plan is None and len(sub_rows) == 0:
        out["_diagnostic"] = {
            "customer_id": customer_id,
            "subscriptions_count": 0,
            "message": "Nenhuma assinatura no banco para este cliente. Rode o seed no mesmo ambiente (API/DB) que o portal usa.",
        }
    elif plan is None and len(sub_rows) > 0:
        out["_diagnostic"] = {
            "customer_id": customer_id,
            "subscriptions_count": len(sub_rows),
            "message": "Cliente tem assinaturas mas nenhuma com Hestia escolhida para exibir plano/site.",
        }
    return out
