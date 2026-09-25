"""Camada Fontes: adapters para ViaCEP e BrasilAPI (CNPJ), com cache em memória e normalização."""
from __future__ import annotations

import re
import time
import httpx

# Cache simples em memória (em produção: Redis com TTL 7-30 dias, ver PLANO.md seção 2)
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 7 * 24 * 3600  # 7 dias

def _cache_get(key: str):
    item = _cache.get(key)
    if item and time.time() - item[0] < _CACHE_TTL:
        return item[1]
    return None

def _cache_set(key: str, value: dict):
    _cache[key] = (time.time(), value)

def normalize_cep(raw: str) -> str | None:
    """Aceita '70.000-000', 'cep 70000000', '70000-00' etc. → '70000000' ou None."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 8:
        return digits
    if len(digits) == 7:  # CEP antigo incomum com 7 dígitos → completa com zero à esquerda
        return "0" + digits
    return None

async def fetch_cep(cep_raw: str) -> dict:
    """F1: enriquece por CEP via ViaCEP."""
    cep = normalize_cep(cep_raw)
    if not cep:
        return {"ok": False, "erro": f"CEP inválido: {cep_raw!r}"}
    cached = _cache_get(f"cep:{cep}")
    if cached:
        return {**cached, "ok": True, "cache": True}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"https://viacep.com.br/ws/{cep}/json/")
        data = r.json()
    if data.get("erro"):
        return {"ok": False, "erro": f"CEP {cep} não encontrado"}
    out = {
        "cep": data["cep"],
        "logradouro": data.get("logradouro", ""),
        "complemento": data.get("complemento", ""),
        "bairro": data.get("bairro", ""),
        "cidade": data.get("localidade", ""),
        "uf": data.get("uf", ""),
        "ibge": data.get("ibge", ""),
        "ddd": data.get("ddd", ""),
    }
    _cache_set(f"cep:{cep}", out)
    return {**out, "ok": True, "cache": False}

def normalize_cnpj(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    return digits if len(digits) == 14 else None

async def fetch_cnpj(cnpj_raw: str) -> dict:
    """F2: enriquece por CNPJ via BrasilAPI (dados abertos da Receita Federal)."""
    cnpj = normalize_cnpj(cnpj_raw)
    if not cnpj:
        return {"ok": False, "erro": f"CNPJ inválido: {cnpj_raw!r}"}
    cached = _cache_get(f"cnpj:{cnpj}")
    if cached:
        return {**cached, "ok": True, "cache": True}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}")
        if r.status_code != 200:
            return {"ok": False, "erro": f"CNPJ {cnpj} não encontrado (HTTP {r.status_code})"}
        d = r.json()
    out = {
        "cnpj": d.get("cnpj"),
        "razao_social": d.get("razao_social"),
        "nome_fantasia": d.get("nome_fantasia"),
        "situacao": d.get("descricao_situacao_cadastral"),
        "porte": d.get("porte"),
        "capital_social": d.get("capital_social"),
        "cnae_principal": d.get("cnae_fiscal_descricao"),
        "atividade_secundaria": [x.get("descricao") for x in d.get("atividade_secundaria", []) or []],
        "endereco": ", ".join(filter(None, [
            d.get("logradouro"), d.get("numero"), d.get("complemento"),
            d.get("bairro"), d.get("municipio"), d.get("uf"), d.get("cep"),
        ])),
        "telefone": d.get("ddd_telefone_1"),
        "email": d.get("email"),
        "socios": [{"nome": s.get("nome_socio"), "qualificacao": s.get("qualificacao_socio")}
                   for s in d.get("qsa", []) or []],
        "data_abertura": d.get("data_inicio_atividade"),
    }
    _cache_set(f"cnpj:{cnpj}", out)
    return {**out, "ok": True, "cache": False}
