"""Public products API: list site plans for the landing page and catalog (monthly + one-time)."""
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.modules.billing.models import PricePlan, Product

router = APIRouter(prefix="/products", tags=["public-products"])

# Product names used for "venda de sites" (must match seed_products_site_venda.py)
SITE_PRODUCT_NAMES = ("Site Essencial", "Site Completo")

# delivery_hours and features per product name (not in DB)
SITE_PLAN_META: dict[str, dict[str, Any]] = {
    "Site Essencial": {
        "delivery_hours": 48,
        "features": [
            "Site institucional",
            "Até 5 páginas",
            "Design profissional",
            "Responsivo (celular e computador)",
            "Integração WhatsApp",
            "SEO básico",
            "Hospedagem incluída",
            "SSL",
            "Manutenção e suporte",
        ],
    },
    "Site Completo": {
        "delivery_hours": 72,
        "features": [
            "Tudo do plano essencial",
            "Blog integrado",
            "Sistema de agendamento",
            "Painel administrativo",
            "SEO avançado",
            "Integrações",
            "Estrutura para crescimento",
        ],
    },
}


class PricePlanOut(BaseModel):
    id: int
    name: str
    amount: float
    interval: str
    currency: str


class ProductSiteOut(BaseModel):
    id: int
    name: str
    description: str | None
    price_plan: PricePlanOut
    delivery_hours: int
    features: list[str]


class ProductCatalogOut(BaseModel):
    """Product with all its price plans (monthly and/or one-time) for portal/site to show options."""

    id: int
    name: str
    description: str | None
    plans: list[PricePlanOut]


@router.get("/catalog", response_model=list[ProductCatalogOut])
async def list_products_catalog(
    db: Annotated[AsyncSession, Depends(get_db)],
    interval: Annotated[
        Literal["all", "month", "one_time"],
        Query(description="Filter by plan interval: all, month (mensal), one_time (pagamento único)"),
    ] = "all",
) -> list[ProductCatalogOut]:
    """Return active products with their price plans. Use interval=one_time to list only pagamento único options."""
    r = await db.execute(
        select(Product)
        .where(Product.org_id == "innexar", Product.is_active.is_(True))
        .options(selectinload(Product.price_plans))
        .order_by(Product.id)
    )
    products = list(r.scalars().unique().all())
    result: list[ProductCatalogOut] = []
    for p in products:
        plans = [
            PricePlanOut(
                id=pp.id,
                name=pp.name,
                amount=float(pp.amount),
                interval=pp.interval,
                currency=pp.currency or "BRL",
            )
            for pp in p.price_plans
            if interval == "all" or (interval == "month" and pp.interval == "month") or (interval == "one_time" and pp.interval == "one_time")
        ]
        if not plans:
            continue
        result.append(
            ProductCatalogOut(
                id=p.id,
                name=p.name,
                description=p.description,
                plans=plans,
            )
        )
    return result


@router.get("/sites", response_model=list[ProductSiteOut])
async def list_products_sites(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ProductSiteOut]:
    """Return site products (Essencial R$ 197, Completo R$ 297) for the landing. No auth required."""
    r = await db.execute(
        select(Product, PricePlan)
        .join(PricePlan, PricePlan.product_id == Product.id)
        .where(
            Product.org_id == "innexar",
            Product.is_active.is_(True),
            Product.name.in_(SITE_PRODUCT_NAMES),
            PricePlan.interval == "month",
        )
        .order_by(Product.id)
    )
    rows = r.all()
    result: list[ProductSiteOut] = []
    for product, price_plan in rows:
        meta = SITE_PLAN_META.get(product.name, {})
        result.append(
            ProductSiteOut(
                id=product.id,
                name=product.name,
                description=product.description,
                price_plan=PricePlanOut(
                    id=price_plan.id,
                    name=price_plan.name,
                    amount=float(price_plan.amount),
                    interval=price_plan.interval,
                    currency=price_plan.currency or "BRL",
                ),
                delivery_hours=meta.get("delivery_hours", 48),
                features=meta.get("features", []),
            )
        )
    return result
