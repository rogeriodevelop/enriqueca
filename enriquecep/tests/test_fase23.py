"""Testes da Fase 2/3: e-mail+DNS (F3), classificação IA, créditos, persistência, XLSX."""
import io
import json

import pytest
from fastapi.testclient import TestClient

import app.email_intel as email_intel
from app.main import app
from app import billing, storage
from app.ia import classify_sector, build_lead_profile

client = TestClient(app)


@pytest.fixture()
def token():
    billing.add_credits("acct-f23", 100, motivo="teste")
    return "Bearer acct-f23"


# ---------- extração de domínio / free domains ----------

def test_extract_domain():
    assert email_intel.extract_domain("Joao@Empresa.com.br ") == "empresa.com.br"
    assert email_intel.extract_domain("https://www.LOJA.com.br/x?a=1") == "loja.com.br"
    assert email_intel.extract_domain("sem-arroba") is None
    assert email_intel.is_free_email("a@gmail.com") and not email_intel.is_free_email("a@loja.com.br")


# ---------- enrich_email com DNS/homepage mockados (offline determinístico) ----------

@pytest.fixture()
def mock_net(monkeypatch):
    async def fake_check(domain):
        return {"dominio": domain, "registrado": True, "ips": ["93.184.216.34"],
                "mx": ["mx.emailhosting.net"] if domain != "gmail.com" else [],
                "tem_mx": domain != "gmail.com", "free_domain": domain == "gmail.com",
                "tipo_dominio": "corporativo"}
    async def fake_home(domain):
        return {"ok": True, "http_status": 200,
                "titulo": "Agência Digital Criativa — Marketing e Publicidade",
                "descricao": "Somos uma agência de marketing digital com mídia e branding."}
    monkeypatch.setattr(email_intel, "check_domain", fake_check)
    monkeypatch.setattr(email_intel, "fetch_homepage", fake_home)


def test_enrich_email_endpoint(token, mock_net):
    r = client.post("/enrich/email", json={"email": "contato@agencia.com.br"},
                    headers={"Authorization": token})
    j = r.json()
    assert j["ok"] and j["dominio"] == "agencia.com.br" and j["tem_mx"] is True
    assert billing.get_account("acct-f23")["creditos"] == 99  # cobrou 1 crédito


def test_classificacao_setor_pela_homepage(mock_net):
    import asyncio
    data = asyncio.run(email_intel.enrich_email("x@agencia.com.br"))
    cls = classify_sector(data)
    assert cls["setor"] == "Marketing/Agência"
    assert cls["cnae_provavel"] == "73.11-4-00"
    assert cls["confianca"] > 0.5


def test_ficha_ia_usa_fatos_do_dominio(mock_net):
    import asyncio
    data = asyncio.run(email_intel.enrich_email("contato@agencia.com.br"))
    f = build_lead_profile({"email": "contato@agencia.com.br"}, None, None, data)
    assert "email-corporativo" in f["tags"] or any("corporativo" in m for m in f["motivos_do_score"])
    assert any("setor:" in t for t in f["tags"])
    assert "Agência Digital" in f["resumo"]


# ---------- billing unitário ----------

def test_charge_and_refill():
    billing.add_credits("acct-unit", 3, motivo="t")
    ok, _ = billing.charge_credits("acct-unit", 2); assert ok
    ok, msg = billing.charge_credits("acct-unit", 5); assert not ok and "insuficientes" in msg
    billing.add_credits("acct-unit", 100, motivo="recarga"); assert billing.charge_credits("acct-unit")[0]


def test_llm_guardrail_por_plano():
    billing.ensure_account("acct-gr", plan=billing.Plan.FREE)
    limite = billing.PLANS[billing.Plan.FREE]["llm_dia"]
    for _ in range(limite):
        assert billing.llm_guardrail_ok("acct-gr")
    assert not billing.llm_guardrail_ok("acct-gr")  # estourou o teto diário


# ---------- persistência SQLite (sobrevive a "restart") ----------

def test_job_persistido_e_recuperavel(token, monkeypatch):
    """Grava resultado direto no storage e recupera via API como se o processo tivesse reiniciado."""
    job_id = "job-persist"
    job = {"id": job_id, "status": "concluido", "total": 1, "done": 1, "creditos": 1,
           "conta": "acct-f23", "criado_em": 1.0, "removidos_dedup": []}
    storage.save_job(job)
    storage.save_job_result(job_id, 0, {"entrada": {"nome": "Persistida"}, "cep": None,
                                        "cnpj": None, "email": None,
                                        "ficha_ia": {"score": 42, "tags": [], "resumo": "x",
                                                     "backend_ia": "heuristica"}})
    from app import main as m
    m._jobs.pop(job_id, None)                      # simula restart (cache vazio)
    r = client.get(f"/enrich/batch/{job_id}")
    assert r.status_code == 200 and r.json()["resultados"][0]["ficha_ia"]["score"] == 42
    csv = client.get(f"/export/csv?job_id={job_id}")
    assert "Persistida" in csv.text and "42" in csv.text


def test_conta_sobrevive_a_restart_da_cache():
    billing.add_credits("acct-durable", 7, motivo="t")
    billing._accounts.pop("acct-durable", None)     # derruba cache de memória
    acct = billing.get_account("acct-durable")      # recarrega do SQLite
    assert acct and acct["creditos"] >= 7


# ---------- exportação XLSX ----------

def test_export_xlsx(token):
    resultados = [{"entrada": {"nome": "Acme", "email": "a@acme.com", "telefone": "11 99999-0000",
                               "cep": "01000-000", "cnpj": None},
                   "cep": {"cidade": "São Paulo", "uf": "SP"}, "cnpj": {}, "email": {"dominio": "acme.com", "tem_mx": True},
                   "ficha_ia": {"score": 77, "tags": ["email-corporativo"], "backend_ia": "heuristica",
                                "sugestao_abordagem": "ligar", "classificacao_ia": {"setor": "Varejo"}}}]
    r = client.post("/export/xlsx", json={"resultados": resultados})
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb.active
    rows = list(ws.values)
    assert rows[0][0] == "nome" and rows[1][0] == "Acme" and rows[1][13] == 77


def test_export_csv_fmt_json(token):
    job_id = "job-json"
    storage.save_job({"id": job_id, "status": "concluido", "total": 1, "done": 1, "creditos": 1,
                      "conta": "acct-f23", "criado_em": 1.0, "removidos_dedup": []})
    storage.save_job_result(job_id, 0, {"entrada": {"nome": "N"}, "cep": None, "cnpj": None,
                                        "email": None, "ficha_ia": {"score": 1, "tags": []}})
    from app import main as m
    m._jobs.pop(job_id, None)
    r = client.get(f"/export/csv?job_id={job_id}&fmt=json")
    assert r.status_code == 200 and r.json()["resultados"][0]["entrada"]["nome"] == "N"


# ---------- rate limit ----------

def test_rate_limit_429():
    from app import ratelimit
    ratelimit._hits.clear()
    monkey_saved = dict(ratelimit._RULES)
    ratelimit._RULES["ip_default"] = (3, 60)
    try:
        codes = [client.post("/enrich/lead-demo", json={"cep": "abc"}).status_code for _ in range(5)]
        assert codes[:3] == [200, 200, 200] and set(codes[3:]) == {429}
    finally:
        ratelimit._RULES.update(monkey_saved)
        ratelimit._hits.clear()


# ---------- landing page ----------

def test_landing_page_servida():
    r = client.get("/")
    assert r.status_code == 200 and "EnriqueCEP" in r.text and "Planos" in r.text
