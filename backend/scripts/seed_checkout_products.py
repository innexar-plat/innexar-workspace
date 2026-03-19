"""
Seed: Criar/atualizar produtos e planos de checkout para o novo-site.

Produtos criados:
  - Site Essencial           → R$ 299/mês  (provisioning: sem_hestia, atualza plano se existir)
  - Site Profissional        → R$ 499/mês  (novo)
  - Site Máquina de Vendas   → R$ 799/mês  (novo)
  - Gestão de Ads            → R$ 399/mês  (novo, sem provisioning)
  - Gestão de Ads Premium    → R$ 699/mês  (novo, sem provisioning)

Uso (a partir do diretório backend/):
    cd innexar-workspace/backend
    python -m scripts.seed_checkout_products

Ou:
    python scripts/seed_checkout_products.py
"""
import asyncio
import json
import os
import sys

_backend_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
_build_lib = os.path.join(_backend_dir, "build", "lib")
if os.path.isdir(_build_lib):
    sys.path.insert(0, _build_lib)
else:
    sys.path.insert(0, _backend_dir)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from app.core.config import settings  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# Definição dos produtos e planos de checkout
# ──────────────────────────────────────────────────────────────────────────────
CHECKOUT_PRODUCTS = [
    {
        "name": "Site Essencial",
        "description": (
            "Site profissional com até 5 páginas, design responsivo, "
            "integração WhatsApp, SEO básico e hospedagem incluída."
        ),
        "provisioning_type": None,   # Não provisionado via Hestia automaticamente no checkout
        "hestia_package": None,
        "plans": [
            {"name": "Mensal", "interval": "month", "amount": 299.00, "currency": "BRL"},
        ],
    },
    {
        "name": "Site Profissional",
        "description": (
            "Site completo com blog, captura de leads, painel administrativo, "
            "SEO avançado e relatórios de desempenho."
        ),
        "provisioning_type": None,
        "hestia_package": None,
        "plans": [
            {"name": "Mensal", "interval": "month", "amount": 499.00, "currency": "BRL"},
        ],
    },
    {
        "name": "Site Máquina de Vendas",
        "description": (
            "Site enterprise com CRM integrado, automações de marketing, "
            "chatbot, estrutura para múltiplas páginas e suporte dedicado."
        ),
        "provisioning_type": None,
        "hestia_package": None,
        "plans": [
            {"name": "Mensal", "interval": "month", "amount": 799.00, "currency": "BRL"},
        ],
    },
    {
        "name": "Gestão de Ads",
        "description": (
            "Gerenciamento profissional de campanhas Google Ads e Meta Ads "
            "com relatórios mensais e otimização contínua."
        ),
        "provisioning_type": None,
        "hestia_package": None,
        "plans": [
            {"name": "Mensal", "interval": "month", "amount": 399.00, "currency": "BRL"},
        ],
    },
    {
        "name": "Gestão de Ads Premium",
        "description": (
            "Gestão completa de marketing digital: Google Ads, Meta Ads, "
            "TikTok Ads, criação de criativos e análise detalhada de ROI."
        ),
        "provisioning_type": None,
        "hestia_package": None,
        "plans": [
            {"name": "Mensal", "interval": "month", "amount": 699.00, "currency": "BRL"},
        ],
    },
]


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    result_map: dict[str, dict] = {}  # name → { product_id, plans: [{plan_id, name, amount}] }

    async with engine.begin() as conn:
        for product in CHECKOUT_PRODUCTS:
            pname = product["name"]

            # ── Buscar produto existente ──────────────────────────────────
            r = await conn.execute(
                text(
                    "SELECT id FROM billing_products "
                    "WHERE name = :name AND org_id = 'innexar' LIMIT 1"
                ),
                {"name": pname},
            )
            row = r.fetchone()

            if row:
                product_id: int = row[0]
                print(f"  ✓ Produto já existe: '{pname}' (id={product_id})")
            else:
                # ── Criar produto ─────────────────────────────────────────
                await conn.execute(
                    text(
                        "INSERT INTO billing_products "
                        "(org_id, name, description, is_active, "
                        "provisioning_type, hestia_package, created_at, updated_at) "
                        "VALUES ('innexar', :name, :desc, true, :ptype, :hpkg, NOW(), NOW())"
                    ),
                    {
                        "name": pname,
                        "desc": product["description"],
                        "ptype": product["provisioning_type"],
                        "hpkg": product["hestia_package"],
                    },
                )
                r2 = await conn.execute(
                    text(
                        "SELECT id FROM billing_products "
                        "WHERE name = :name AND org_id = 'innexar' LIMIT 1"
                    ),
                    {"name": pname},
                )
                product_id = r2.fetchone()[0]
                print(f"  + Produto criado:     '{pname}' (id={product_id})")

            # ── Criar planos (somente se não existirem) ───────────────────
            plan_results = []
            for plan in product["plans"]:
                rp = await conn.execute(
                    text(
                        "SELECT id FROM billing_price_plans "
                        "WHERE product_id = :pid AND interval = :interval AND name = :name LIMIT 1"
                    ),
                    {"pid": product_id, "interval": plan["interval"], "name": plan["name"]},
                )
                plan_row = rp.fetchone()
                if plan_row:
                    plan_id: int = plan_row[0]
                    print(f"    ✓ Plano já existe: '{plan['name']}' R$ {plan['amount']:.2f}/{plan['interval']} (id={plan_id})")
                else:
                    await conn.execute(
                        text(
                            "INSERT INTO billing_price_plans "
                            "(product_id, name, interval, amount, currency, created_at) "
                            "VALUES (:pid, :name, :interval, :amount, :currency, NOW())"
                        ),
                        {
                            "pid": product_id,
                            "name": plan["name"],
                            "interval": plan["interval"],
                            "amount": plan["amount"],
                            "currency": plan.get("currency", "BRL"),
                        },
                    )
                    rp2 = await conn.execute(
                        text(
                            "SELECT id FROM billing_price_plans "
                            "WHERE product_id = :pid AND interval = :interval AND name = :name LIMIT 1"
                        ),
                        {"pid": product_id, "interval": plan["interval"], "name": plan["name"]},
                    )
                    plan_id = rp2.fetchone()[0]
                    print(f"    + Plano criado:    '{plan['name']}' R$ {plan['amount']:.2f}/{plan['interval']} (id={plan_id})")

                plan_results.append(
                    {
                        "plan_id": plan_id,
                        "name": plan["name"],
                        "interval": plan["interval"],
                        "amount": plan["amount"],
                    }
                )

            result_map[pname] = {"product_id": product_id, "plans": plan_results}

    # ── Print mapeamento para uso no novo-site ──────────────────────────────
    print("\n" + "=" * 70)
    print("MAPEAMENTO PARA VARIÁVEIS DE AMBIENTE (.env.local no novo-site)")
    print("=" * 70)
    print("Cole no arquivo innexar-workspace/backend/.env e novo-site/.env.local:\n")

    slug_map = {
        "Site Essencial": "SITE_STARTER",
        "Site Profissional": "SITE_PRO",
        "Site Máquina de Vendas": "SITE_ENTERPRISE",
        "Gestão de Ads": "ADS_MANAGER",
        "Gestão de Ads Premium": "ADS_PREMIUM",
    }

    for name, data in result_map.items():
        slug = slug_map.get(name, name.upper().replace(" ", "_"))
        plan = data["plans"][0]
        print(f"PLAN_{slug}_PRODUCT_ID={data['product_id']}")
        print(f"PLAN_{slug}_PRICE_PLAN_ID={plan['plan_id']}")
        print()

    print("=" * 70)
    print("\nJSON completo (para configuração manual):")
    print(json.dumps(result_map, indent=2, ensure_ascii=False))
    print("\n✅ Seed concluído!")


if __name__ == "__main__":
    asyncio.run(main())
