"""Billing (Fase 3 do PLANO.md): planos, créditos por consulta e integração Stripe.

- Sem STRIPE_SECRET_KEY → modo demo: checkout devolve URL mock e conta nasce com créditos
  (permite testar todo o fluxo sem cartão/chave).
- Com STRIPE_SECRET_KEY → cria Session real via API do Stripe.
- Webhook: verifica assinatura Stripe-Signature (HMAC-SHA256, t/v1) quando
  STRIPE_WEBHOOK_SECRET estiver configurado; sem secret = modo dev (documentado).
- Guardrail de custo de LLM por conta (PLANO.md seção 4): LLMAssistant diária por plano.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from enum import Enum

import httpx


class Plan(str, Enum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"

    @classmethod
    def from_str(cls, s: str | None) -> "Plan":
        try:
            return cls((s or "free").lower())
        except ValueError:
            return cls.FREE


# Preço em BRL e créditos mensais — tabela definida no PLANO.md (Fase 0)
PLANS: dict[Plan, dict] = {
    Plan.FREE:   {"preco_brl": 0,   "creditos_mes": 100,    "llm_dia": 50},
    Plan.STARTER: {"preco_brl": 97,  "creditos_mes": 5_000,  "llm_dia": 1_000},
    Plan.PRO:    {"preco_brl": 297,  "creditos_mes": 25_000, "llm_dia": 5_000},
}


def credits_for_plan(p: Plan) -> int:
    return PLANS[p]["creditos_mes"]


# Conta: SQLite via storage (durável); cache em memória para leitura rápida.
from . import storage

_accounts: dict[str, dict] = {}


def _persist(acct: dict):
    storage.upsert_account(acct)


def get_account(conta_id: str) -> dict | None:
    if conta_id in _accounts:
        return _accounts[conta_id]
    acct = storage.load_account(conta_id)
    if acct:
        acct["plano"] = Plan.from_str(acct.get("plano"))
        _accounts[conta_id] = acct
    return acct


def ensure_account(conta_id: str, email: str = "", plan: Plan = Plan.FREE) -> dict:
    """Garante que a conta existe (créditos iniciais = 0; o plano é concedido via checkout/webhook).

    Bug corrigado: antes a conta nascia com os créditos do plano E o checkout somava de novo
    (double-credit: free virava 200 em vez de 100 — e test_creditos_insuficientes_402 falhava).
    """
    acct = get_account(conta_id)
    if not acct:
        acct = {"id": conta_id, "email": email, "plano": plan,
                "creditos": 0, "historico": [],
                "llm_hoje": 0, "llm_dia": "", "criado_em": time.time()}
        _accounts[conta_id] = acct
        _persist(acct)
    return acct


def add_credits(conta_id: str, n: int, motivo: str = "") -> dict:
    acct = ensure_account(conta_id)
    acct["creditos"] += n
    acct["historico"].append({"ts": time.time(), "delta": n, "motivo": motivo})
    _persist(acct)
    storage.ledger_add(conta_id, n, motivo)
    return acct


def charge_credits(conta_id: str, n: int = 1) -> tuple[bool, str]:
    """Debita créditos; False + mensagem se insuficientes (HTTP 402 na API)."""
    acct = get_account(conta_id)
    if not acct:
        return False, "conta não encontrada"
    if acct["creditos"] < n:
        return False, f"créditos insuficientes: precisa {n}, tem {acct['creditos']} (faça upgrade em /billing/checkout)"
    acct["creditos"] -= n
    acct["historico"].append({"ts": time.time(), "delta": -n, "motivo": "consulta"})
    _persist(acct)
    storage.ledger_add(conta_id, -n, "consulta")
    return True, "ok"


def llm_guardrail_ok(conta_id: str) -> bool:
    """Guardrail de custo de LLM: limita chamadas/dia por plano (renova a cada dia UTC)."""
    acct = get_account(conta_id)
    if not acct:
        return False
    hoje = time.strftime("%Y-%m-%d", time.gmtime())
    if acct.get("llm_dia") != hoje:
        acct["llm_dia"], acct["llm_hoje"] = hoje, 0
    limite = PLANS[Plan(acct["plano"])]["llm_dia"]
    if acct["llm_hoje"] >= limite:
        return False
    acct["llm_hoje"] += 1
    _persist(acct)
    return True


def stripe_secret() -> str | None:
    return os.environ.get("STRIPE_SECRET_KEY")


async def checkout_url(plan: Plan, email: str, conta_id: str) -> tuple[str, bool]:
    """Retorna (url, demo_mode). Em produção cria uma Checkout Session real."""
    key = stripe_secret()
    if not key:
        return f"https://checkout.stripe.mock/c/{conta_id}?plan={plan.value}", True
    price_id = os.environ.get(f"STRIPE_PRICE_{plan.value.upper()}", "")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://api.stripe.com/v1/checkout/sessions",
            auth=(key, ""),
            data={
                "mode": "subscription",
                "customer_email": email,
                "client_reference_id": conta_id,
                "metadata[conta_id]": conta_id,
                "metadata[plan]": plan.value,
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": "1",
                "success_url": os.environ.get("APP_URL", "http://localhost:3000") + "/sucesso",
                "cancel_url": os.environ.get("APP_URL", "http://localhost:3000") + "/planos",
            })
        r.raise_for_status()
        return r.json()["url"], False


class WebhookError(Exception):
    pass


def verify_stripe_signature(payload: bytes, sig_header: str | None) -> None:
    """Verifica Stripe-Signature (t=...,v1=...) com STRIPE_WEBHOOK_SECRET.

    Sem secret configurado = modo dev: aceita payload (para testes locais/stripe listen).
    Com secret: assinatura inválida ou ausente → WebhookError (nunca processar evento não autenticado).
    """
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        return
    if not sig_header:
        raise WebhookError("ausente Stripe-Signature header")
    parts = dict(kv.split("=", 1) for kv in sig_header.split(",") if "=" in kv)
    ts, sigs = parts.get("t", ""), parts.get("v1", "")
    if not ts or not sigs:
        raise WebhookError("Stripe-Signature malformada")
    if abs(time.time() - int(ts)) > 600:  # tolerância de 10 min contra replay
        raise WebhookError("timestamp expirado")
    expected = hmac.new(secret.encode(), f"{ts}.{payload.decode('utf-8', 'replace')}".encode(),
                        hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s.strip()) for s in sigs.split(";")):
        raise WebhookError("assinatura inválida")
