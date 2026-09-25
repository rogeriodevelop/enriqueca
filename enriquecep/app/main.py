"""EnriqueCEP — API de enriquecimento de cadastros com fontes da internet + camada IA."""
import asyncio

from fastapi import FastAPI, UploadFile, File
from pydantic import BaseModel

from .sources import fetch_cep, fetch_cnpj
from .ia import build_lead_profile
from .csv_import import read_leads_csv
from .dedup import dedupe

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


# ---------- Lote / CSV (Fase 1→2: importador tolerante + dedup + job assíncrono) ----------

class BatchIn(BaseModel):
    leads: list[LeadIn]
    dedupe: bool = True
    max_concurrency: int = 8

class JobOut(BaseModel):
    job_id: str
    status: str
    total: int

_jobs: dict[str, dict] = {}  # em produção: Postgres/Redis (PLANO.md seção 2)


async def _run_batch(job_id: str, leads: list[dict], sem: asyncio.Semaphore):
    async def one(idx: int, lead: dict):
        async with sem:
            cep_data = await fetch_cep(lead["cep"]) if lead.get("cep") else None
            cnpj_data = await fetch_cnpj(lead["cnpj"]) if lead.get("cnpj") else None
            ficha = build_lead_profile(lead, cep_data, cnpj_data)
            _jobs[job_id]["results"][idx] = {
                "entrada": lead, "cep": cep_data, "cnpj": cnpj_data, "ficha_ia": ficha,
            }
            _jobs[job_id]["done"] += 1

    results = await asyncio.gather(*(one(i, l) for i, l in enumerate(leads)),
                                   return_exceptions=True)
    erros = [r for r in results if isinstance(r, Exception)]
    _jobs[job_id]["status"] = "concluido_com_erros" if erros else "concluido"
    _jobs[job_id]["erros"] = len(erros)


@app.post("/enrich/batch", response_model=JobOut)
async def enrich_batch(body: BatchIn):
    """Recebe N leads já estruturados (opcionalmente com dedup fuzzy), processa em paralelo."""
    rows = [l.model_dump() for l in body.leads]
    removed = []
    if body.dedupe and len(rows) > 1:
        rows, removed_groups = dedupe(rows)
        removed = [g for group in removed_groups for g in group]
    job_id = f"job-{len(_jobs)+1:04d}"
    _jobs[job_id] = {"status": "rodando", "total": len(rows), "done": 0,
                     "results": [None] * len(rows), "removidos_dedup": removed}
    sem = asyncio.Semaphore(max(1, min(body.max_concurrency, 16)))
    asyncio.create_task(_run_batch(job_id, rows, sem))
    return {"job_id": job_id, "status": "rodando", "total": len(rows)}


@app.get("/enrich/batch/{job_id}")
async def batch_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return {"erro": f"job {job_id} não encontrado"}
    return {k: v for k, v in job.items() if k != "results"} | {"resultados": job["results"]}


@app.post("/import/csv")
async def import_csv(file: UploadFile = File(...)):
    """F5: upload de CSV sujo → mapeamento automático de colunas + normalização + dedup.

    Retorna as linhas canônicas prontas para POST /enrich/batch (sem chamar as fontes ainda,
    para o usuário revisar o mapeamento antes — princípio 'human-in-the-loop' do plano).
    """
    content = await file.read()
    parsed = read_leads_csv(content)
    rows, removed_groups = dedupe(parsed["linhas"]) if len(parsed["linhas"]) > 1 else (parsed["linhas"], [])
    return {
        "mapeamento": parsed["mapeamento"],
        "colunas_ignoradas": parsed["colunas_ignoradas"],
        "total_recebido": len(parsed["linhas"]),
        "total_pos_dedup": len(rows),
        "duplicatas_removidas": [i for g in removed_groups for i in g],
        "leads": rows,
    }


@app.get("/export/csv")
async def export_csv(job_id: str):
    """Exporta o resultado de um job concluído como CSV enriquecido (download)."""
    from fastapi.responses import PlainTextResponse
    import csv as _csv
    import io

    job = _jobs.get(job_id)
    if not job or job["status"] == "rodando":
        return {"erro": "job inexistente ou ainda rodando"}
    buf = io.StringIO()
    cols = ["nome", "email", "telefone", "cep", "cnpj", "cidade", "uf", "razao_social",
            "situacao", "cnae_principal", "score", "tags", "sugestao_abordagem"]
    w = _csv.writer(buf, delimiter=";")
    w.writerow(cols)
    for r in job["results"]:
        if not r:
            continue
        e, cep, cnpj, f = r["entrada"], r["cep"] or {}, r["cnpj"] or {}, r["ficha_ia"]
        w.writerow([e.get("nome"), e.get("email"), e.get("telefone"), e.get("cep"), e.get("cnpj"),
                    cep.get("cidade"), cep.get("uf"), cnpj.get("razao_social"), cnpj.get("situacao"),
                    cnpj.get("cnae_principal"), f.get("score"), "|".join(f.get("tags", [])),
                    f.get("sugestao_abordagem")])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{job_id}.csv"'})
