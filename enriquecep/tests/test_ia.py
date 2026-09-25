from app.ia import build_lead_profile

FAKE_CNPJ = {"ok": True, "razao_social": "ACME LTDA", "nome_fantasia": "Acme Store",
             "situacao": "Ativa", "porte": "EMPRESA DE PEQUENO PORTE", "capital_social": 500000,
             "cnae_principal": "Comércio varejista", "email": "contato@acme.com.br",
             "telefone": "11 99999-0000", "socios": [{"nome": "Fulano", "qualificacao": "Sócio"}],
             "endereco": "Rua X, 10, Centro, São Paulo, SP, 01000-000"}
FAKE_CEP = {"ok": True, "cep": "01000-000", "logradouro": "Rua X", "bairro": "Centro",
            "cidade": "São Paulo", "uf": "SP", "ddd": "11"}

def test_ficha_completa():
    f = build_lead_profile({"email": "a@gmail.com"}, FAKE_CEP, FAKE_CNPJ)
    assert 60 <= f["score"] <= 100
    assert "Acme Store" in f["resumo"]
    assert "email-corporativo" in f["tags"]
    assert f["contato_prioritario"] == "contato@acme.com.br"
    assert isinstance(f["motivos_do_score"], list) and f["motivos_do_score"]

def test_ficha_sem_dados():
    f = build_lead_profile({"email": "a@gmail.com"})
    assert f["score"] == 0
    assert "Sem dados suficientes" in f["resumo"]

def test_ficha_situacao_baixa():
    c = dict(FAKE_CNPJ, situacao="Baixada", email="x@gmail.com")
    f = build_lead_profile({}, None, c)
    assert f["score"] < 60
    assert "validar dados" in f["sugestao_abordagem"].lower() or f["score"] < 60
