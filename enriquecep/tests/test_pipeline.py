"""Testes E2E do pipeline CSV → dedup → batch (mockando fontes, sem rede)."""
import io
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import app.sources as sources
from app.main import app, _jobs

client = TestClient(app)


async def fake_client_get(self, url, **kw):
    if url.startswith("https://viacep.com.br/ws/"):
        return httpx.Response(200, json={
            "cep": "70040010", "logradouro": "SBN Quadra 2", "complemento": "",
            "bairro": "Asa Sul", "localidade": "Brasília", "uf": "DF",
            "ibge": "5300108", "ddd": "61", "erro": False})
    return httpx.Response(404, json={"detail": "não mockado"})


@pytest.fixture(autouse=True)
def mock_sources(monkeypatch):
    monkeypatch.setattr(sources, "_cache", {})  # cache limpo por teste
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_client_get)


def test_e2e_csv_import_batch_export():
    csv_bytes = (
        "Nome;Email;CEP\r\n"
        "Ana;ana@gmail.com;70.040-010\r\n"
        "Bruno;;70040010\r\n"
        "Ana;ana@gmail.com;70.040-010\r\n"   # duplicata exata de Ana
    ).encode("utf-8")

    # 1) upload CSV tolerante → mapeia colunas + dedup
    r = client.post("/import/csv", files={"file": ("leads.csv", io.BytesIO(csv_bytes), "text/csv")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["total_recebido"] == 3
    assert j["total_pos_dedup"] == 2
    assert j["duplicatas_removidas"] == [2]
    assert j["mapeamento"]["Nome"] == "nome" and j["mapeamento"]["Email"] == "email"
    leads = j["leads"]

    # 2) dispara o lote com os leads canônicos
    r = client.post("/enrich/batch", json={"leads": leads})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert r.json()["total"] == 2

    # 3) aguarda o job concluir (fontes mockadas → rápido)
    for _ in range(50):
        st = client.get(f"/enrich/batch/{job_id}").json()
        if st["status"] != "rodando":
            break
        time.sleep(0.05)
    assert st["status"] == "concluido", st
    assert st["done"] == 2
    res = st["resultados"]
    assert res[0]["ficha_ia"]["score"] > 0 and "Brasília" in res[0]["ficha_ia"]["resumo"]
    assert res[0]["cep"]["uf"] == "DF" and res[0]["cep"]["ddd"] == "61"

    # 4) exporta CSV enriquecido
    r = client.get(f"/export/csv?job_id={job_id}")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    linhas = r.text.strip().splitlines()
    assert linhas[0].startswith("nome;email;telefone;cep;cnpj;cidade;uf")
    assert len(linhas) == 3  # header + 2 leads
    assert "Brasília" in r.text and "DF" in r.text


def test_batch_sem_dedupe_opcional():
    leads = [{"nome": "X", "cep": "70040010"}, {"nome": "X", "cep": "70040010"}]
    r = client.post("/enrich/batch", json={"leads": leads, "dedupe": False})
    assert r.json()["total"] == 2
    r = client.post("/enrich/batch", json={"leads": leads, "dedupe": True})
    assert r.json()["total"] == 1  # nomes idênticos → dedup agrupa
