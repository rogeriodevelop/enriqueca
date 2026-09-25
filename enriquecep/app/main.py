"""EnriqueCEP — API de enriquecimento de cadastros com fontes da internet + camada IA."""
from fastapi import FastAPI
from pydantic import BaseModel
from .sources import fetch_cep, fetch_cnpj
from .ia import build_lead_profile

app = FastAPI(title="EnriqueCEP API", version="0.1.0",
              description="F1 CEP · F2 CNPJ · F4 Ficha IA — MVP da Fase 1 (ver PLANO.md)")

class CepIn(BaseModel):
    cep: str

class CnpjIn(BaseModel):
    cnpj: str

class LeadIn(BaseModel):
    nome: str | None = None
    email: str | None = None
    cep: str | None = None
    cnpj: str | None = None

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/enrich/cep")
async def enrich_cep(body: CepIn):
    return await fetch_cep(body.cep)

@app.post("/enrich/cnpj")
async def enrich_cnpj(body: CnpjIn):
    return await fetch_cnpj(body.cnpj)

@app.post("/enrich/lead")
async def enrich_lead(body: LeadIn):
    """F5 combinada: enriquece uma linha da base (CEP e/ou CNPJ) e devolve a ficha IA (F4)."""
    cep_data = await fetch_cep(body.cep) if body.cep else None
    cnpj_data = await fetch_cnpj(body.cnpj) if body.cnpj else None
    ficha = build_lead_profile(body.model_dump(), cep_data, cnpj_data)
    return {"entrada": body.model_dump(), "cep": cep_data, "cnpj": cnpj_data, "ficha_ia": ficha}
