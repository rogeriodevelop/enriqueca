import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import billing

client = TestClient(app)


@pytest.fixture()
def token():
    """Conta demo com créditos (equivalente a passar pelo checkout)."""
    billing.add_credits("acct-teste", 50, motivo="teste")
    return "Bearer acct-teste"


def test_health():
    j = client.get("/health").json()
    assert j["ok"] is True and "llm_ativo" in j


def test_auth_exigida():
    assert client.post("/enrich/cep", json={"cep": "70040010"}).status_code == 401
    assert client.post("/enrich/cep", json={"cep": "70040010"},
                       headers={"Authorization": "Bearer inexistente"}).status_code == 401


def test_creditos_insuficientes_402():
    billing.add_credits("acct-pobre", 1, motivo="teste")
    r = client.post("/enrich/cep", json={"cep": "01000-000"},
                    headers={"Authorization": "Bearer acct-pobre"})
    assert r.status_code == 200            # consome o último crédito
    r = client.post("/enrich/cep", json={"cep": "01000-000"},
                    headers={"Authorization": "Bearer acct-pobre"})
    assert r.status_code == 402


def test_enrich_cep_invalido(token):
    r = client.post("/enrich/cep", json={"cep": "abc"}, headers={"Authorization": token})
    assert r.status_code == 200 and r.json()["ok"] is False   # erro de validação é dado, não exceção


def test_enrich_lead_sem_fontes(token):
    # e-mail gratuito (gmail) é fato de DNS → score 40; sem cep/cnpj não há outros fatos
    r = client.post("/enrich/lead", json={"nome": "Teste", "email": "t@gmail.com"},
                    headers={"Authorization": token})
    j = r.json()
    assert j["ficha_ia"]["score"] == 40 and j["cep"] is None and j["cnpj"] is None
    assert any("gratuito" in m.lower() for m in j["ficha_ia"]["motivos_do_score"])
    assert j["ficha_ia"]["backend_ia"] in ("heuristica", "llm")


def test_enrich_lead_totalmente_vazio_pontua_zero(token):
    r = client.post("/enrich/lead", json={"nome": "Teste"},
                    headers={"Authorization": token})
    j = r.json()
    assert j["ficha_ia"]["score"] == 0 and j["cep"] is None and j["cnpj"] is None
    assert j["ficha_ia"]["resumo"] == "Sem dados suficientes para enriquecer."


def test_billing_plans_e_checkout_demo():
    plans = client.get("/billing/plans").json()
    assert {p["plano"] for p in plans} == {"free", "starter", "pro"}
    r = client.post("/billing/checkout", json={"email": "a@b.com", "plan": "starter"})
    j = r.json()
    assert j["creditos"] == 5000 and j["creditos_virgens"] is True and j["conta_id"].startswith("acct-")
    me = client.get("/billing/me", headers={"Authorization": f"Bearer {j['conta_id']}"}).json()
    assert me["creditos"] == 5000


def test_webhook_renova_creditos():
    import json as _json
    billing.add_credits("acct-webhook", 10, motivo="setup")
    ev = {"type": "invoice.paid", "data": {"object": {
        "client_reference_id": "acct-webhook", "metadata": {"plan": "pro"}}}}
    r = client.post("/billing/webhook", content=_json.dumps(ev).encode(),
                    headers={"content-type": "application/json"})
    assert r.status_code == 200 and r.json()["creditos_adicionados"] == 25000
    assert billing.get_account("acct-webhook")["creditos"] == 10 + 25000


def test_webhook_assinatura_invalida_400(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_teste")
    r = client.post("/billing/webhook", content=b'{"type":"x"}',
                    headers={"content-type": "application/json"})
    assert r.status_code == 400
