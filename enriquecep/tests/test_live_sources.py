"""Testes de contrato contra as fontes reais (internet). Marcados com 'live'."""
import os
import pytest

skip_live = pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="defina RUN_LIVE=1 para testes com internet")

@skip_live
@pytest.mark.asyncio
async def test_viacep_brasilia():
    from app.sources import fetch_cep
    r = await fetch_cep("70040-010")
    assert r["ok"] and r["uf"] == "DF" and r["cidade"] == "Brasília"

@skip_live
@pytest.mark.asyncio
async def test_brasilapi_nubank():
    from app.sources import fetch_cnpj
    r = await fetch_cnpj("19.131.243/0001-97")
    assert r["ok"] is True
    assert r["razao_social"]  # fonte no ar devolve razão social
    assert str(r["situacao"]).upper().startswith("ATIVA")
