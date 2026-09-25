"""Camada IA (F4 + F3-classificação): ficha do lead, score, tags e classificação de setor.

Dois backends, mesmo contrato JSON (guardrail anti-alucinação do PLANO.md seção 4):
  - LLM real (OpenAI GPT-4o-mini) quando OPENAI_API_KEY está definida → `llm_available()`;
  - Heurísticas determinísticas como fallback/baseline offline.
Campos FACTUAIS nunca vêm do modelo: sempre das fontes (ViaCEP/BrasilAPI/DNS).
"""
from __future__ import annotations

import json
import os
import re

FREE_EMAIL_DOMAINS = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "yahoo.com.br",
                      "bol.com.br", "uol.com.br", "ig.com.br", "terra.com.br", "icloud.com", "mail.com"}

def _score_cnpj(d: dict) -> tuple[int, list[str]]:
    score, motivos = 40, ["Base: registro PJ encontrado na Receita Federal"]
    if str(d.get("situacao", "")).upper().startswith("ATIVA"):
        score += 20; motivos.append("Situação cadastral ATIVA (+20)")
    elif d.get("situacao"):
        score -= 25; motivos.append(f"Situação cadastral '{d['situacao']}' (-25)")
    if d.get("email") and not _is_free_email(d.get("email", "")):
        score += 10; motivos.append("E-mail corporativo cadastrado na RFB (+10)")
    if d.get("telefone"):
        score += 5; motivos.append("Telefone disponível (+5)")
    if d.get("capital_social") and (d.get("capital_social") or 0) >= 300000:
        score += 10; motivos.append("Capital social ≥ R$300k (+10)")
    if d.get("porte") in ("DEMAIS", "EMPRESA DE PEQUENO PORTE"):
        score += 5; motivos.append(f"Porte {d['porte']} (+5)")
    if d.get("socios"):
        score += 5; motivos.append("QSA (sócios) preenchido (+5)")
    return max(0, min(100, score)), motivos

def _score_cep(d: dict) -> tuple[int, list[str]]:
    score, motivos = 50, ["Endereço validado nos Correios (ViaCEP)"]
    for f in ("logradouro", "bairro", "cidade", "uf"):
        if d.get(f):
            score += 10; motivos.append(f"{f} presente (+10)")
    return max(0, min(100, score)), motivos

def _is_free_email(email: str) -> bool:
    m = re.search(r"@([\w.-]+)", email or "")
    return bool(m) and m.group(1).lower() in FREE_EMAIL_DOMAINS

def _resumo_empresa(d: dict) -> str:
    nome = d.get("nome_fantasia") or d.get("razao_social") or "Empresa"
    cnae = d.get("cnae_principal") or "atividade não informada"
    cidade = (d.get("endereco") or "").split(",")
    local = cidade[-2].strip() + "/" + cidade[-1].strip() if len(cidade) >= 2 else ""
    situ = d.get("situacao", "")
    porte = d.get("porte", "")
    return (f"{nome} é uma empresa ({porte}) de {local}, cuja atividade principal é: {cnae}. "
            f"Situação atual na Receita: {situ}.")

def build_lead_profile(record: dict, cep_data: dict | None = None, cnpj_data: dict | None = None,
                       email_data: dict | None = None) -> dict:
    """Gera a ficha IA do lead: resumo, score 0-100, motivos e sugestão de abordagem (JSON validável)."""
    partes_score, resumos = [], []
    scores = []
    contato = ""
    if cnpj_data and cnpj_data.get("ok"):
        s, m = _score_cnpj(cnpj_data); scores.append(s); partes_score += m
        resumos.append(_resumo_empresa(cnpj_data))
        nome = cnpj_data.get("nome_fantasia") or cnpj_data.get("razao_social")
        contato = cnpj_data.get("email") or ""
    if cep_data and cep_data.get("ok"):
        s, m = _score_cep(cep_data); scores.append(s); partes_score += m
        resumos.append(f"Localização confirmada: {cep_data['logradouro']}, {cep_data['bairro']}, "
                       f"{cep_data['cidade']}/{cep_data['uf']} (DDD {cep_data.get('ddd','?')}).")
        contato = contato if (cnpj_data and cnpj_data.get("ok") and contato) else record.get("email", "")
    # F3: bônus/penalidade por qualidade do domínio de e-mail (fatos DNS, não inferência)
    dominio_info = None
    if email_data and email_data.get("ok"):
        dominio_info = classify_sector(email_data)
        if email_data.get("tem_mx") and not email_data.get("free_domain"):
            scores.append(75); partes_score.append("Domínio de e-mail corporativo válido com MX (+) ")
        elif email_data.get("free_domain"):
            scores.append(40); partes_score.append("E-mail em domínio gratuito (-)")
        else:
            scores.append(25); partes_score.append("Domínio sem MX — risco de e-mail inválido (-)")
        home = email_data.get("homepage") or {}
        if home.get("titulo"):
            resumos.append(f"Site: {home['titulo']}" + (f" — {home['descricao'][:120]}" if home.get("descricao") else ""))
    score = int(sum(scores) / len(scores)) if scores else 0
    tags = []
    if cnpj_data and cnpj_data.get("ok"):
        if cnpj_data.get("porte") == "MICRO EMPRESA": tags.append("micro")
        if cnpj_data.get("capital_social", 0) and cnpj_data["capital_social"] >= 1_000_000: tags.append("alta-renda-fiscal")
        if not _is_free_email(contato): tags.append("email-corporativo")
    if dominio_info and dominio_info.get("setor"):
        tags.append(f"setor:{dominio_info['setor']}")
    abordagem = (f"Contatar via {'e-mail corporativo' if 'email-corporativo' in tags else 'telefone/WhatsApp'} "
                 f"sobre {cnpj_data.get('cnae_principal', 'oferta padrão') if cnpj_data and cnpj_data.get('ok') else 'oferta padrão'}."
                 if score >= 60 else "Baixo score: validar dados antes de investir tempo de SDR.")
    return {
        "score": score,
        "motivos_do_score": partes_score,
        "resumo": " ".join(resumos) or "Sem dados suficientes para enriquecer.",
        "tags": tags,
        "sugestao_abordagem": abordagem,
        "contato_prioritario": contato or None,
        "classificacao_ia": dominio_info,
        "backend_ia": "heuristica",
    }


# ---------------------------------------------------------------------------
# Classificação de setor (F3, papel da IA no plano): homepage/DNS → setor + CNAE provável
# ---------------------------------------------------------------------------

_SETOR_KEYWORDS: list[tuple[str, str, list[str]]] = [  # (setor, cnae_provavel, palavras-chave)
    ("Tecnologia/SaaS", "62.01-5-01", ["software", "saas", "plataforma", "cloud", "api", "sistema", "tech", "digital", "app"]),
    ("Varejo/E-commerce", "47.89-0-99", ["loja", "shop", "compre", "e-commerce", "ecommerce", "produtos", "moda", "entrega"]),
    ("Alimentação/Restaurantes", "56.11-2-01", ["restaurante", "food", "cardápio", "delivery", "lanchonete", "pizzaria", "café", "cafe"]),
    ("Saúde", "86.30-5-01", ["clínica", "clinica", "saúde", "saude", "medic", "dent", "hospital", "fisioter", "psicolog"]),
    ("Educação", "85.99-6-04", ["curso", "escola", "educa", "faculdade", "treinamento", "aprendiz", "aluno"]),
    ("Serviços financeiros", "64.99-9-99", ["crédito", "financeir", "segur", "investiment", "pagament", "fintech", "conta"]),
    ("Indústria", "25.99-3-99", ["indústria", "industria", "fabrica", "manufa", "metalúrgica", "montadora", "produção"]),
    ("Construção/Imobiliária", "41.20-4-00", ["construt", "imobili", "incorporad", "obra", "reforma", "lancament"]),
    ("Marketing/Agência", "73.11-4-00", ["agência", "agencia", "marketing", "publicidade", "mídia", "midia", "social media", "branding"]),
    ("Transporte/Logística", "49.30-2-01", ["transport", "logíst", "logist", "frete", "entregas", "carg", "frot"]),
    ("Terceiro setor/ONG", "94.30-8-00", ["ong", "instituto", "associa", "fundação", "fundacao", "civil", "sociedade"]),
    ("Agronegócio", "01.11-3-01", ["agro", "fazenda", "rural", "grãos", "graos", "pecuár", "pecuar"]),
]


def classify_sector(email_data: dict | None) -> dict | None:
    """Backend heurístico: título+descrição da homepage → setor/CNAE provável.

    Em produção com OPENAI_API_KEY, `classify_sector_llm` faz o mesmo com few-shot LLM
    (PLANO.md: 'extrair descrição do site, classificar setor com CNAE').
    """
    if not email_data or not email_data.get("ok"):
        return None
    home = email_data.get("homepage") or {}
    texto = ((home.get("titulo") or "") + " " + (home.get("descricao") or "")).lower()
    if not texto.strip():
        return {"setor": None, "cnae_provavel": None, "confianca": 0.0,
                "baseado_em": "sem conteúdo de homepage"}
    best, hits = None, 0
    for setor, cnae, kws in _SETOR_KEYWORDS:
        n = sum(1 for kw in kws if kw in texto)
        if n > hits:
            best, hits = (setor, cnae), n
    if not best:
        return {"setor": "Não classificado", "cnae_provavel": None, "confianca": 0.2,
                "baseado_em": texto[:80]}
    return {"setor": best[0], "cnae_provavel": best[1],
            "confianca": min(0.95, 0.4 + 0.15 * hits), "baseado_em": texto[:80]}


# ---------------------------------------------------------------------------
# Backend LLM real (OpenAI-compatível) — Fase 2/3 do PLANO.md
# ---------------------------------------------------------------------------

_FICHA_PROMPT = """Você é o analista de leads do EnriqueCEP. Receba fatos VERIFICADOS (nunca invente dados fora deles)
e produza EXATAMENTE este JSON:
{"score": 0-100, "motivos_do_score": [...], "resumo": "...", "tags": [...], "sugestao_abordagem": "..."}
Critérios: situação cadastral ATIVA pesa +; e-mail corporativo com MX válido pesa +; domínio free ou sem MX pesa -;
endereço confirmado pesa +; capital social alto e QSA completo pesam +. Responda só o JSON."""

_llm_cache: dict[str, dict] = {}  # mitigação de custo do PLANO.md: cache por hash da entrada


def llm_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


async def build_lead_profile_llm(record: dict, cep_data=None, cnpj_data=None, email_data=None) -> dict:
    """Versão LLM (GPT-4o-mini) da ficha; valida saída pelo MESMO schema; fallback p/ heurística."""
    base = build_lead_profile(record, cep_data, cnpj_data, email_data)
    if not llm_available():
        return base
    import httpx
    facts = json.dumps({"entrada": record, "cep": cep_data, "cnpj": cnpj_data,
                        "email_dns": {k: v for k, v in (email_data or {}).items() if k != "homepage"},
                        "homepage": (email_data or {}).get("homepage")},
                       ensure_ascii=False, default=str)
    key = str(hash(facts))
    if key in _llm_cache:
        return {**_llm_cache[key], "backend_ia": "llm", "cache": True}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1") + "/chat/completions",
                headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                json={"model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                      "temperature": 0.2, "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": _FICHA_PROMPT},
                                   {"role": "user", "content": facts}]})
            out = json.loads(r.json()["choices"][0]["message"]["content"])
        # Guardrail: aceita apenas campos do schema; score sempre 0-100 int
        ficha = {**base,
                 "score": max(0, min(100, int(out.get("score", base["score"])))),
                 "resumo": str(out.get("resumo", base["resumo"]))[:600],
                 "tags": [str(t) for t in out.get("tags", base["tags"])][:10],
                 "sugestao_abordagem": str(out.get("sugestao_abordagem", base["sugestao_abordagem"]))[:400],
                 "motivos_do_score": [str(m) for m in out.get("motivos_do_score", base["motivos_do_score"])][:12],
                 "backend_ia": "llm"}
        _llm_cache[key] = ficha
        return ficha
    except Exception:
        return base  # falha de LLM nunca derruba o pipeline (fallback determinístico)
