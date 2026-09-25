"""Camada IA (F4): ficha do lead. Em produção, chama LLM (GPT-4o-mini/Claude Haiku).
Aqui: heurísticas determinísticas com MESMO schema JSON de saída, para o pipeline funcionar
sem API key e servir de baseline/guardrail contra alucinação (PLANO.md seção 4)."""
from __future__ import annotations

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

def build_lead_profile(record: dict, cep_data: dict | None = None, cnpj_data: dict | None = None) -> dict:
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
    score = int(sum(scores) / len(scores)) if scores else 0
    tags = []
    if cnpj_data and cnpj_data.get("ok"):
        if cnpj_data.get("porte") == "MICRO EMPRESA": tags.append("micro")
        if cnpj_data.get("capital_social", 0) and cnpj_data["capital_social"] >= 1_000_000: tags.append("alta-renda-fiscal")
        if not _is_free_email(contato): tags.append("email-corporativo")
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
    }
