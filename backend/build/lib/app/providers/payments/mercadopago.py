"""Mercado Pago: Checkout Pro (one-time) and Assinaturas (subscription plans).
- Checkout Pro: one payment link per invoice (create_payment_link).
- Assinaturas: create_subscription_plan returns init_point; customer subscribes on MP, we sync via webhook.
"""
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.debug_log import debug_log
from app.providers.payments.base import PaymentLinkResult, WebhookResult

logger = logging.getLogger(__name__)
MP_API_BASE = "https://api.mercadopago.com"


@dataclass
class SubscriptionPlanResult:
    plan_id: str
    init_point: str


def _get_access_token() -> str | None:
    raw = os.environ.get("MP_ACCESS_TOKEN") or os.environ.get("MERCADOPAGO_ACCESS_TOKEN")
    return raw.strip() if raw else None


class MercadoPagoProvider:
    """Mercado Pago Checkout Pro: create preference (payment link) and handle IPN webhook."""

    def __init__(self, access_token: str | None = None) -> None:
        raw = access_token or _get_access_token()
        self._access_token = raw.strip() if raw else None

    def create_payment_link(
        self,
        invoice_id: int,
        amount: float,
        currency: str,
        success_url: str,
        cancel_url: str,
        customer_email: str | None = None,
        customer_name: str | None = None,
        customer_phone: str | None = None,
        description: str | None = None,
    ) -> PaymentLinkResult:
        # #region agent log
        debug_log(
            "mercadopago.create_payment_link",
            "MP token check",
            {"token_set": bool(self._access_token), "invoice_id": invoice_id},
            "C",
        )
        # #endregion
        if not self._access_token:
            raise ValueError("Mercado Pago not configured; set MP_ACCESS_TOKEN or use IntegrationConfig")
        currency_id = (currency or "BRL").upper()[:3]
        # Checkout Pro redirect: layout/alignment are controlled by Mercado Pago; we only send preference data.
        payload = {
            "items": [
                {
                    "id": str(invoice_id),
                    "title": description or f"Pagamento #{invoice_id}",
                    "quantity": 1,
                    "currency_id": currency_id,
                    "unit_price": round(amount, 2),
                }
            ],
            "back_urls": {
                "success": success_url,
                "failure": cancel_url,
                "pending": success_url,
            },
            "auto_return": "approved",
            "external_reference": str(invoice_id),
            "statement_descriptor": "INNEXAR",
        }
        if customer_email or customer_name or customer_phone:
            payer: dict[str, Any] = {}
            if customer_email:
                payer["email"] = customer_email
            if customer_name:
                parts = (customer_name or "").strip().split(None, 1)
                payer["name"] = parts[0] or ""
                if len(parts) > 1:
                    payer["surname"] = parts[1]
            if customer_phone:
                digits = "".join(c for c in (customer_phone or "") if c.isdigit())
                if len(digits) >= 10:
                    payer["phone"] = {
                        "area_code": digits[:2],
                        "number": digits[2:],
                    }
            if payer:
                payload["payer"] = payer
        # notification_url must be set in dashboard or here; caller can pass via env
        notification_url = os.environ.get("MP_NOTIFICATION_URL") or os.environ.get("MERCADOPAGO_NOTIFICATION_URL")
        if notification_url:
            payload["notification_url"] = notification_url

        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{MP_API_BASE}/checkout/preferences",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code != 201:
            # #region agent log
            debug_log(
                "mercadopago.create_payment_link",
                "MP API non-201",
                {"status_code": resp.status_code, "text_preview": (resp.text or "")[:150]},
                "D",
            )
            # #endregion
            raise ValueError(f"Mercado Pago preference failed: {resp.status_code} {resp.text}")
        data = resp.json()
        # Com token TEST- preferir sandbox_init_point se a API devolver; não trocar host (sandbox.mercadopago.com.br leva a 404/Argentina)
        is_test = (self._access_token or "").strip().upper().startswith("TEST-")
        init_point = (
            (data.get("sandbox_init_point") or data.get("init_point"))
            if is_test
            else (data.get("init_point") or data.get("sandbox_init_point"))
        ) or ""
        pref_id = data.get("id") or ""
        logger.info(
            "MP Checkout Pro preference: is_test=%s sandbox_init_point=%s payment_url=%s",
            is_test,
            bool(data.get("sandbox_init_point")),
            (init_point[:80] + "...") if len(init_point) > 80 else init_point,
        )
        if is_test:
            logger.warning(
                "MP em modo TESTE: o comprador deve usar CONTA DE TESTE do MP (Painel > Usuários de teste). Conta real gera 'Uma das partes é de teste'.",
            )
        return PaymentLinkResult(payment_url=init_point, external_id=str(pref_id) if pref_id else None)

    def create_subscription_plan(
        self,
        reason: str,
        amount: float,
        currency: str,
        back_url: str,
        frequency: int = 1,
        frequency_type: str = "months",
        billing_day: int | None = None,
    ) -> SubscriptionPlanResult:
        """Create a preapproval_plan for Assinaturas. Returns plan_id and init_point (redirect URL)."""
        if not self._access_token:
            raise ValueError("Mercado Pago not configured; set MP_ACCESS_TOKEN or use IntegrationConfig")
        tok = (self._access_token or "").strip()
        prefix = (tok[:16] + "..." if len(tok) > 16 else tok[:16]) if tok else "vazio"
        logger.info("MP Assinaturas preapproval_plan: token prefixo=%s len=%s", prefix, len(tok))
        if tok.upper().startswith("TEST-"):
            logger.warning(
                "Assinaturas: credenciais de teste (TEST-) podem retornar 401; use APP_USR-... de produção.",
            )
        currency_id = (currency or "BRL").upper()[:3]
        auto_recurring: dict[str, Any] = {
            "frequency": frequency,
            "frequency_type": frequency_type,
            "transaction_amount": round(amount, 2),
            "currency_id": currency_id,
        }
        if billing_day is not None:
            auto_recurring["billing_day"] = billing_day
            auto_recurring["billing_day_proportional"] = False
        payload = {
            "reason": reason[:255],
            "auto_recurring": auto_recurring,
            "back_url": back_url,
        }
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{MP_API_BASE}/preapproval_plan",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code not in (200, 201):
            err_detail = resp.text
            if resp.status_code == 401:
                # Diagnóstico: o mesmo token funciona no Checkout Pro?
                try:
                    with httpx.Client(timeout=8.0) as client:
                        probe = client.post(
                            f"{MP_API_BASE}/checkout/preferences",
                            json={
                                "items": [{"title": "probe", "quantity": 1, "currency_id": "BRL", "unit_price": 0.01}],
                                "back_urls": {"success": "https://x.com", "failure": "https://x.com"},
                            },
                            headers={
                                "Authorization": f"Bearer {self._access_token}",
                                "Content-Type": "application/json",
                            },
                        )
                    if probe.status_code == 201:
                        err_detail += (
                            " DIAGNÓSTICO: Este token é válido para Checkout Pro mas NÃO tem permissão para Assinaturas. "
                            "Na app do MP (Suas integrações > sua app): em 'Qual produto você está integrando?' marque ASSINATURAS e clique Salvar. "
                            "Depois em Credenciais de produção > Renovar e use o novo token."
                        )
                    else:
                        err_detail += " Token rejeitado também no Checkout Pro; confira se o Access Token está correto e não expirado."
                except Exception as e:
                    logger.warning("Probe Checkout Pro falhou: %s", e)
                    err_detail += (
                        " Confira: produto Assinaturas ativado na app; token APP_USR- de produção; Renovar credenciais após ativar Assinaturas."
                    )
            raise ValueError(f"Mercado Pago preapproval_plan failed: {resp.status_code} {err_detail}")
        data = resp.json()
        plan_id = data.get("id") or ""
        # API de Assinaturas (preapproval_plan) só devolve init_point; não existe sandbox_init_point como no Checkout Pro.
        # Com token TEST- o plano fica em modo teste no MP; a mesma URL (www.mercadopago.com.br) é usada e cobrança é de teste.
        is_test = (self._access_token or "").strip().upper().startswith("TEST-")
        sandbox_url = data.get("sandbox_init_point") or ""
        init_point = (
            (sandbox_url or data.get("init_point"))
            if is_test
            else (data.get("init_point") or sandbox_url)
        ) or ""
        logger.info(
            "MP Assinaturas preapproval_plan: is_test=%s response_has_sandbox_init_point=%s init_point=%s",
            is_test,
            bool(sandbox_url),
            init_point[:80] + "..." if len(init_point) > 80 else init_point,
        )
        if is_test:
            logger.warning(
                "MP em modo TESTE: o comprador deve usar uma CONTA DE TESTE do Mercado Pago (criar em Painel MP > Sua integração > Usuários de teste). "
                "Pagando com conta real aparece: 'Uma das partes é de teste'.",
            )
        if not plan_id or not init_point:
            raise ValueError("Mercado Pago preapproval_plan missing id or init_point")
        return SubscriptionPlanResult(plan_id=str(plan_id), init_point=init_point)

    def get_preapproval(self, preapproval_id: str) -> dict[str, Any] | None:
        """GET /preapproval/{id} to get subscription details (payer, status, preapproval_plan_id)."""
        if not self._access_token:
            return None
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{MP_API_BASE}/preapproval/{preapproval_id}",
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        if resp.status_code != 200:
            return None
        return resp.json()

    def handle_webhook(self, body: bytes, headers: dict[str, str]) -> WebhookResult:
        """Parse IPN: topic=payment (Checkout Pro) or subscription_preapproval (Assinaturas)."""
        if not self._access_token:
            return WebhookResult(processed=False, message="Mercado Pago not configured")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return WebhookResult(processed=False, message="Invalid JSON")
        topic = data.get("type") or data.get("topic")
        if topic == "subscription_preapproval":
            preapproval_id = None
            if "data" in data and "id" in data["data"]:
                preapproval_id = str(data["data"]["id"])
            if not preapproval_id:
                return WebhookResult(processed=False, message="Missing preapproval id in webhook")
            # Notificação de teste do painel MP (id 123456): responder 200 sem chamar a API
            if preapproval_id == "123456":
                return WebhookResult(processed=True, message="123456")
            preapp = self.get_preapproval(preapproval_id)
            if not preapp:
                return WebhookResult(processed=False, message="Failed to get preapproval")
            plan_id = (preapp.get("preapproval_plan_id") or preapp.get("preapproval_plan", {}).get("id") or "")
            if not plan_id:
                return WebhookResult(processed=True, message=preapproval_id)
            return WebhookResult(
                processed=True,
                message=preapproval_id,
                mp_preapproval_id=preapproval_id,
                mp_plan_id=str(plan_id),
            )
        # Cobrança recorrente aprovada (assinatura): aceitar 200; marcar fatura paga seria por external_reference se MP enviar
        if topic == "subscription_authorized_payment":
            return WebhookResult(processed=True, message=f"ignored:{topic}")
        if topic != "payment":
            return WebhookResult(processed=True, message=f"ignored:{topic}")
        payment_id = None
        if "data" in data and "id" in data["data"]:
            payment_id = str(data["data"]["id"])
        if not payment_id:
            return WebhookResult(processed=False, message="Missing payment id in webhook")
        # Notificação de teste do painel MP (id 123456): responder 200 sem chamar a API
        if payment_id == "123456":
            return WebhookResult(processed=True, message="123456")
        with httpx.Client(timeout=10.0) as client:
            pay_resp = client.get(
                f"{MP_API_BASE}/v1/payments/{payment_id}",
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        if pay_resp.status_code != 200:
            return WebhookResult(processed=False, message=f"Failed to get payment: {pay_resp.status_code}")
        pay_data = pay_resp.json()
        status = (pay_data.get("status") or "").lower()
        if status != "approved":
            logger.info(
                "MP webhook payment not approved: payment_id=%s status=%s (só marcamos fatura paga quando status=approved)",
                payment_id,
                status,
            )
            return WebhookResult(processed=True, message=payment_id)
        ext_ref = pay_data.get("external_reference") or ""
        try:
            invoice_id = int(ext_ref)
        except (ValueError, TypeError):
            return WebhookResult(processed=False, message="Invalid external_reference")
        return WebhookResult(processed=True, invoice_id=invoice_id, message=payment_id)

    # ── Bricks: Customer & Card management ────────────────────────────

    def create_or_get_customer(self, email: str, name: str | None = None) -> dict[str, Any]:
        """Find or create an MP customer by e-mail. Returns the customer dict with 'id'."""
        if not self._access_token:
            raise ValueError("Mercado Pago not configured")
        # Search first
        with httpx.Client(timeout=10.0) as client:
            search = client.get(
                f"{MP_API_BASE}/v1/customers/search",
                params={"email": email},
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        if search.status_code == 200:
            results = search.json().get("results") or []
            if results:
                return results[0]
        # Create
        payload: dict[str, Any] = {"email": email}
        if name:
            parts = name.strip().split(" ", 1)
            payload["first_name"] = parts[0]
            if len(parts) > 1:
                payload["last_name"] = parts[1]
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{MP_API_BASE}/v1/customers",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code not in (200, 201):
            raise ValueError(f"MP create customer failed: {resp.status_code} {resp.text}")
        return resp.json()

    def save_card(self, customer_id: str, card_token: str) -> dict[str, Any]:
        """Save a tokenized card to an MP customer. Returns the card dict with 'id'."""
        if not self._access_token:
            raise ValueError("Mercado Pago not configured")
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{MP_API_BASE}/v1/customers/{customer_id}/cards",
                json={"token": card_token},
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code not in (200, 201):
            logger.warning("MP save_card failed: %s %s", resp.status_code, resp.text)
            # Non-critical: card saving may fail for some card types; payment still works
            return {}
        return resp.json()

    def create_payment(
        self,
        *,
        token: str | None = None,
        amount: float,
        installments: int,
        payment_method_id: str,
        issuer_id: str | None = None,
        payer_email: str,
        description: str | None = None,
        external_reference: str | None = None,
        notification_url: str | None = None,
    ) -> dict[str, Any]:
        """Process a payment using Bricks (card token or pix/ticket). Returns the payment dict."""
        if not self._access_token:
            raise ValueError("Mercado Pago not configured")
        payload: dict[str, Any] = {
            "transaction_amount": round(amount, 2),
            "installments": installments,
            "payment_method_id": payment_method_id,
            "payer": {"email": payer_email},
            "statement_descriptor": "INNEXAR",
        }
        if token:
            payload["token"] = token
        if issuer_id:
            payload["issuer_id"] = issuer_id
        if description:
            payload["description"] = description
        if external_reference:
            payload["external_reference"] = external_reference
        n_url = notification_url or os.environ.get("MP_NOTIFICATION_URL") or os.environ.get("MERCADOPAGO_NOTIFICATION_URL")
        if n_url:
            payload["notification_url"] = n_url
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{MP_API_BASE}/v1/payments",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                    "X-Idempotency-Key": f"payment-{external_reference or (token[:32] if token else 'nokey')}",
                },
            )
        if resp.status_code == 401:
            raise ValueError(
                "Token do Mercado Pago inválido ou não configurado. "
                "Use o Access Token (credencial secreta) do painel do MP em MP_ACCESS_TOKEN, não a Chave Pública."
            )
        if resp.status_code not in (200, 201):
            raise ValueError(f"MP payment failed: {resp.status_code} {resp.text}")
        return resp.json()

    def charge_saved_card(
        self,
        *,
        customer_id: str,
        card_id: str,
        amount: float,
        description: str | None = None,
        external_reference: str | None = None,
    ) -> dict[str, Any]:
        """Charge a saved card (recurring). Uses customer_id + card.first_six/last_four + card_id via /v1/payments."""
        if not self._access_token:
            raise ValueError("Mercado Pago not configured")
        # Get card details to find payment_method_id
        with httpx.Client(timeout=10.0) as client:
            card_resp = client.get(
                f"{MP_API_BASE}/v1/customers/{customer_id}/cards/{card_id}",
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        if card_resp.status_code != 200:
            raise ValueError(f"MP get card failed: {card_resp.status_code} {card_resp.text}")
        card_data = card_resp.json()
        payment_method_id = card_data.get("payment_method", {}).get("id") or card_data.get("payment_method_id") or ""
        issuer_id = str(card_data.get("issuer", {}).get("id") or "")
        payload: dict[str, Any] = {
            "transaction_amount": round(amount, 2),
            "token": card_data.get("id", card_id),
            "installments": 1,
            "payment_method_id": payment_method_id,
            "payer": {
                "type": "customer",
                "id": customer_id,
            },
            "statement_descriptor": "INNEXAR",
        }
        if issuer_id:
            payload["issuer_id"] = issuer_id
        if description:
            payload["description"] = description
        if external_reference:
            payload["external_reference"] = external_reference
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{MP_API_BASE}/v1/payments",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                    "X-Idempotency-Key": f"recurring-{external_reference or card_id}",
                },
            )
        if resp.status_code not in (200, 201):
            raise ValueError(f"MP recurring payment failed: {resp.status_code} {resp.text}")
        return resp.json()

