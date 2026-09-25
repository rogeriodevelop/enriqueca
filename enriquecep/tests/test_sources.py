import pytest
from app.sources import normalize_cep, normalize_cnpj, fetch_cep, fetch_cnpj

def test_normalize_cep_variants():
    assert normalize_cep("70040-010") == "70040010"
    assert normalize_cep("70.040-010") == "70040010"
    assert normalize_cep("cep 70040010") == "70040010"
    assert normalize_cep("07040010"[1:]) == "07040010"  # 7 dígitos → completa zero
    assert normalize_cep("123") is None
    assert normalize_cep("") is None

def test_normalize_cnpj():
    assert normalize_cnpj("19.131.243/0001-97") == "19131243000197"
    assert normalize_cnpj("123") is None

@pytest.mark.asyncio
async def test_fetch_cep_invalido_sem_rede():
    r = await fetch_cep("abc")
    assert r["ok"] is False

@pytest.mark.asyncio
async def test_fetch_cnpj_invalido_sem_rede():
    r = await fetch_cnpj("abc")
    assert r["ok"] is False
