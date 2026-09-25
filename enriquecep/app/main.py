"""EnriqueCEP — API de enriquecimento de cadastros com fontes da internet + camada IA."""
import asyncio
import time
import uuid

from fastapi import FastAPI, UploadFile, File, Header, HTTPException, Request
from pydantic import BaseModel

from .sources import fetch_cep, fetch_cnpj
from .ia import build_lead_profile, build_lead_profile_llm, llm_available
from .csv_import import read_leads_csv
from .dedup import dedupe
from .email_intel import enrich_email
from .billing import (Plan, credits_for_plan, charge_credits, add_credits, get_account,
                      checkout_url, PLANS, WebhookError, verify_stripe_signature)
from . import storage
from .ratelimit import check_ip_rate

storage.init_db()  # cria tabelas accounts/credit_ledger/jobs/job_results (Fase 3)

from fastapi.staticfiles import StaticFiles
from pathlib import Path as _P
_WEB = _P(__file__).resolve().parent.parent / "web"

app = FastAPI(title="EnriqueCEP API", version="0.2.0",
              description="F1 CEP · F2 CNPJ · F3 E-mail/DNS · F4 Ficha IA · F5 CSV/API "
                          "· Billing Stripe (PLANO.md Fases 1–3)")

# ----------------------------- Contas / créditos (Fase 3) -------------------

def _acct(authorization: str | None) -> dict:
    """Auth simples por header `Authorization: Bearer <conta_id>` (placeholder de JWT/OIDC)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "envie Authorization: Bearer <conta_id> (crie em POST /billing/checkout)")
    conta_id = authorization.split(None, 1)[1].strip()
    acct = get_account(conta_id)
    if not acct:
        raise HTTPException(401, f"conta {conta_id!r} desconhecida")
    return acct


def _consume(acct: dict, n: int = 1):
    ok, msg = charge_credits(acct["id"], n)
    if not ok:
        raise HTTPException(402, msg)


class CepIn(BaseModel):
    cep: str

class CnpjIn(BaseModel):
    cnpj: str

class EmailIn(BaseModel):
    email: str | None = None
    site: str | None = None

class LeadIn(BaseModel):
    nome: str | None = None
    email: str | None = None
    telefone: str | None = None
    cep: str | None = None
    cnpj: str | None = None
    site: str | None = None

@app.get("/health")
async def health():
    return {"ok": True, "llm_ativo": llm_available()}

@app.post("/enrich/cep")
async def enrich_cep(body: CepIn, request: Request, authorization: str | None = Header(None)):
    check_ip_rate(request)
    acct = _acct(authorization); _consume(acct)
    return await fetch_cep(body.cep)

@app.post("/enrich/cnpj")
async def enrich_cnpj(body: CnpjIn, request: Request, authorization: str | None = Header(None)):
    check_ip_rate(request)
    acct = _acct(authorization); _consume(acct)
    return await fetch_cnpj(body.cnpj)

@app.post("/enrich/email")
async def enrich_email_ep(body: EmailIn, request: Request, authorization: str | None = Header(None)):
    """F3: domínio do e-mail/site → DNS (A/MX) + homepage → classificação de setor pela IA."""
    check_ip_rate(request)
    acct = _acct(authorization); _consume(acct)
    data = await enrich_email(body.email, body.site)
    return data

@app.post("/enrich/lead")
async def enrich_lead(body: LeadIn, request: Request, authorization: str | None = Header(None)):
    """F5 combinada: enriquece uma linha (CEP/CNPJ/e-mail) e devolve a ficha IA (F4)."""
    check_ip_rate(request)
    acct = _acct(authorization)
    n_consultas = sum(1 for x in (body.cep, body.cnpj, body.email or body.site) if x)
    _consume(acct, max(1, n_consultas))
    cep_data = await fetch_cep(body.cep) if body.cep else None
    cnpj_data = await fetch_cnpj(body.cnpj) if body.cnpj else None
    email_data = await enrich_email(body.email, body.site) if (body.email or body.site) else None
    ficha = await build_lead_profile_llm(body.model_dump(), cep_data, cnpj_data, email_data)
    return {"entrada": body.model_dump(), "cep": cep_data, "cnpj": cnpj_data,
            "email": email_data, "ficha_ia": ficha}


# ---------- Lote / CSV (Fase 1→2: importador tolerante + dedup + job assíncrono) ----------

class BatchIn(BaseModel):
    leads: list[LeadIn]
    dedupe: bool = True
    max_concurrency: int = 8

class JobOut(BaseModel):
    job_id: str
    status: str
    total: int

_jobs: dict[str, dict] = {}  # cache de jobs em execução; persistência durável via storage (SQLite)


def _job_get(job_id: str) -> dict | None:
    """Job do cache (em execução) ou do banco (após restart)."""
    if job_id in _jobs:
        return _jobs[job_id]
    job = storage.get_job(job_id)
    if job:
        _jobs[job_id] = job
    return job


async def _run_batch(job_id: str, leads: list[dict], sem: asyncio.Semaphore):
    async def one(idx: int, lead: dict):
        async with sem:
            cep_data = await fetch_cep(lead["cep"]) if lead.get("cep") else None
            cnpj_data = await fetch_cnpj(lead["cnpj"]) if lead.get("cnpj") else None
            email_data = (await enrich_email(lead.get("email"), lead.get("site"))
                          if (lead.get("email") or lead.get("site")) else None)
            ficha = await build_lead_profile_llm(lead, cep_data, cnpj_data, email_data)
            result = {"entrada": lead, "cep": cep_data, "cnpj": cnpj_data,
                      "email": email_data, "ficha_ia": ficha}
            _jobs[job_id]["results"][idx] = result
            _jobs[job_id]["done"] += 1
            storage.save_job_result(job_id, idx, result)          # durável por linha
            storage.update_job_progress(job_id, _jobs[job_id]["done"])

    results = await asyncio.gather(*(one(i, l) for i, l in enumerate(leads)),
                                   return_exceptions=True)
    erros = [r for r in results if isinstance(r, Exception)]
    status = "concluido_com_erros" if erros else "concluido"
    _jobs[job_id]["status"] = status
    _jobs[job_id]["erros"] = len(erros)
    storage.update_job_progress(job_id, _jobs[job_id]["done"], status)


class DemoIn(BaseModel):
    cep: str | None = None
    cnpj: str | None = None
    email: str | None = None


@app.post("/enrich/lead-demo")
async def enrich_lead_demo(body: DemoIn, request: Request):
    """Demo pública da landing page: mesma ficha IA, sem créditos, rate-limited por IP."""
    check_ip_rate(request)
    cep_data = await fetch_cep(body.cep) if body.cep else None
    cnpj_data = await fetch_cnpj(body.cnpj) if body.cnpj else None
    email_data = await enrich_email(body.email) if body.email else None
    record = {"nome": None, "email": body.email, "cep": body.cep, "cnpj": body.cnpj}
    ficha = build_lead_profile(record, cep_data, cnpj_data, email_data)  # heurística: zero custo
    return {"entrada": record, "cep": cep_data, "cnpj": cnpj_data, "email": email_data, "ficha_ia": ficha}


@app.post("/enrich/batch", response_model=JobOut)
async def enrich_batch(body: BatchIn, request: Request, authorization: str | None = Header(None)):
    check_ip_rate(request, "ip_default")
    """Recebe N leads estruturados (opcionalmente com dedup fuzzy), processa em paralelo.

    Cobra 1 crédito por linha efetivamente processada (após dedup)."""
    rows = [l.model_dump() for l in body.leads]
    removed = []
    if body.dedupe and len(rows) > 1:
        rows, removed_groups = dedupe(rows)
        removed = [g for group in removed_groups for g in group]
    n_cred = len(rows)
    acct = _acct(authorization)
    _consume(acct, n_cred)
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    _jobs[job_id] = {"id": job_id, "status": "rodando", "total": len(rows), "done": 0,
                     "results": [None] * len(rows), "removidos_dedup": removed,
                     "conta": acct["id"], "creditos": n_cred, "criado_em": time.time()}
    storage.save_job(_jobs[job_id])  # durável desde a criação (Fase 3)
    sem = asyncio.Semaphore(max(1, min(body.max_concurrency, 16)))
    asyncio.create_task(_run_batch(job_id, rows, sem))
    return {"job_id": job_id, "status": "rodando", "total": len(rows)}


@app.get("/enrich/batch/{job_id}")
async def batch_status(job_id: str):
    job = _job_get(job_id)
    if not job:
        raise HTTPException(404, f"job {job_id} não encontrado")
    return {k: v for k, v in job.items() if k != "results"} | {"resultados": job["results"]}


@app.get("/jobs")
async def jobs_list(authorization: str | None = Header(None)):
    """Histórico de jobs da conta (persistido — sobrevive a restart do servidor)."""
    acct = _acct(authorization)
    return {"jobs": storage.list_jobs(acct["id"])}


@app.post("/import/csv")
async def import_csv(file: UploadFile = File(...), authorization: str | None = Header(None)):
    """F5: upload de CSV sujo → mapeamento automático de colunas + normalização + dedup.

    Retorna as linhas canônicas prontas para POST /enrich/batch (sem chamar as fontes ainda,
    para o usuário revisar o mapeamento antes — princípio 'human-in-the-loop' do plano).
    Não consome créditos."""
    _acct(authorization)
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
async def export_csv(job_id: str, fmt: str = "csv"):
    """Exporta o resultado de um job concluído como CSV enriquecido (download)."""
    from fastapi.responses import PlainTextResponse
    import csv as _csv
    import io

    job = _job_get(job_id)
    if not job:
        raise HTTPException(404, "job inexistente")
    if job["status"] == "rodando":
        raise HTTPException(409, "job ainda rodando")
    if fmt == "json":
        from fastapi.responses import JSONResponse
        return JSONResponse({"job_id": job_id, "status": job["status"], "resultados": job["results"]})
    buf = io.StringIO()
    cols = ["nome", "email", "telefone", "cep", "cnpj", "cidade", "uf", "razao_social",
            "situacao", "cnae_principal", "dominio", "mx_valido", "setor_ia",
            "score", "tags", "backend_ia", "sugestao_abordagem"]
    w = _csv.writer(buf, delimiter=";")
    w.writerow(cols)
    for r in job["results"]:
        if not r:
            continue
        e, cep, cnpj, em, f = r["entrada"], r["cep"] or {}, r["cnpj"] or {}, r.get("email") or {}, r["ficha_ia"]
        cls = f.get("classificacao_ia") or {}
        w.writerow([e.get("nome"), e.get("email"), e.get("telefone"), e.get("cep"), e.get("cnpj"),
                    cep.get("cidade"), cep.get("uf"), cnpj.get("razao_social"), cnpj.get("situacao"),
                    cnpj.get("cnae_principal"), em.get("dominio"), em.get("tem_mx"),
                    cls.get("setor"), f.get("score"), "|".join(f.get("tags", [])),
                    f.get("backend_ia"), f.get("sugestao_abordagem")])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{job_id}.csv"'})


# ----------------------------- Billing (Fase 3) -----------------------------

class CheckoutIn(BaseModel):
    email: str
    plan: Plan


@app.get("/billing/plans")
async def plans_ep():
    return [{"plano": p.value, "preco_brl": PLANS[p]["preco_brl"],
             "creditos_mes": credits_for_plan(p)} for p in PLANS]


@app.post("/billing/checkout")
async def checkout(body: CheckoutIn):
    """Cria a conta no plano escolhido e devolve a URL do Checkout Stripe.

    Sem STRIPE_SECRET_KEY (dev/demo): creditos_virgens=true e a conta já nasce ativa
    com os créditos do plano — comportamento esperado fora de produção."""
    conta_id = f"acct-{uuid.uuid4().hex[:10]}"
    url, demo = await checkout_url(body.plan, body.email, conta_id)
    add_credits(conta_id, credits_for_plan(body.plan), motivo="checkout_inicial")
    return {"conta_id": conta_id, "token_api": conta_id, "plano": body.plan.value,
            "creditos": credits_for_plan(body.plan), "checkout_url": url,
            "creditos_virgens": demo,
            "aviso": "GUARDE este token — ele é a credencial da sua API."}


@app.get("/billing/me")
async def me(authorization: str | None = Header(None)):
    acct = _acct(authorization)
    return get_account(acct["id"])


@app.post("/billing/webhook")
async def stripe_webhook(request: Request):
    """Webhook Stripe: renova créditos em checkout.session.completed / invoice.paid.

    Assinatura verificada com STRIPE_WEBHOOK_SECRET quando configurado; em dev (sem secret)
    aceita payload simulado para testes de integração."""
    raw = await request.body()
    try:
        verify_stripe_signature(raw, request.headers.get("stripe-signature"))
    except WebhookError as e:
        raise HTTPException(400, str(e))
    import json as _json
    ev = _json.loads(raw)
    t = ev.get("type", "")
    if t in ("checkout.session.completed", "invoice.paid"):
        obj = ev.get("data", {}).get("object", {})
        meta = (obj.get("client_reference_id") or obj.get("metadata", {}).get("conta_id")
                or obj.get("customer_details", {}).get("email"))
        plan = Plan.from_str(obj.get("metadata", {}).get("plan", "starter"))
        if meta:
            add_credits(meta, credits_for_plan(plan), motivo=t)
            return {"ok": True, "conta": meta, "creditos_adicionados": credits_for_plan(plan)}
    return {"ok": True, "ignorado": t}


@app.post("/export/xlsx")
async def export_xlsx(body: dict):
    """Converte o resultado de um job (ou payload direto) em planilha XLSX para download.

    Aceita {"job_id": "..."} — lê o job persistido — ou {"resultados": [...]} cru."""
    from fastapi.responses import Response
    import io as _io
    from openpyxl import Workbook

    results = body.get("resultados")
    if not results and body.get("job_id"):
        job = _job_get(body["job_id"])
        if not job:
            raise HTTPException(404, "job inexistente")
        results = job["results"]
    if not results:
        raise HTTPException(400, "envie job_id ou resultados")

    wb = Workbook()
    ws = wb.active
    ws.title = "Leads Enriquecidos"
    cols = ["nome", "email", "telefone", "cep", "cnpj", "cidade", "uf", "razao_social",
            "situacao", "cnae_principal", "dominio", "mx_valido", "setor_ia",
            "score", "tags", "backend_ia", "sugestao_abordagem"]
    ws.append(cols)
    for r in results:
        if not r:
            continue
        e, cep, cnpj, em, f = r["entrada"], r.get("cep") or {}, r.get("cnpj") or {}, r.get("email") or {}, r["ficha_ia"]
        cls = f.get("classificacao_ia") or {}
        ws.append([e.get("nome"), e.get("email"), e.get("telefone"), e.get("cep"), e.get("cnpj"),
                   cep.get("cidade"), cep.get("uf"), cnpj.get("razao_social"), cnpj.get("situacao"),
                   cnpj.get("cnae_principal"), em.get("dominio"), em.get("tem_mx"),
                   cls.get("setor"), f.get("score"), "|".join(f.get("tags", [])),
                   f.get("backend_ia"), f.get("sugestao_abordagem")])
    buf = _io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="leads_enriquecidos.xlsx"'})


@app.get("/")
async def home():
    from fastapi.responses import FileResponse
    return FileResponse(_WEB / "index.html")

app.mount("/static", StaticFiles(directory=str(_WEB)), name="static")
