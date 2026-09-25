from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    assert client.get("/health").json() == {"ok": True}

def test_enrich_cep_invalido():
    r = client.post("/enrich/cep", json={"cep": "abc"})
    assert r.status_code == 200 and r.json()["ok"] is False

def test_enrich_lead_sem_fontes():
    r = client.post("/enrich/lead", json={"nome": "Teste", "email": "t@gmail.com"})
    j = r.json()
    assert j["ficha_ia"]["score"] == 0 and j["cep"] is None and j["cnpj"] is None
