"""F5 — Importador CSV "tolerante" (Fase 1 do PLANO.md).

Pipeline: lê CSV com cabeçalhos sujos → mapeia colunas automaticamente para o
schema canônico (heurística determinística; em produção, LLM few-shot desempata)
→ normaliza valores (CEP/CNPJ/telefone/e-mail) → devolve linhas prontas para /enrich.
"""
from __future__ import annotations

import csv
import io
import re

# Campos canônicos do schema de lead do EnriqueCEP
CANONICAL_FIELDS = ("nome", "razao_social", "email", "telefone", "cep", "cnpj", "empresa", "site")

# Heurística de mapeamento: alias (normalizado) → campo canônico.
# Em produção isto é o fallback do prompt LLM few-shot (PLANO.md F5).
ALIASES: dict[str, str] = {
    # nome de pessoa / contato
    "nome": "nome", "name": "nome", "contato": "nome", "pessoa": "nome",
    "nome completo": "nome", "nome do contato": "nome", "lead": "nome", "cliente": "nome", "solicitante": "nome",
    # razão social / empresa
    "razao social": "razao_social", "razão social": "razao_social", "empresa": "empresa",
    "company": "empresa", "organizacao": "empresa", "organização": "empresa", "fantasia": "empresa",
    "nome da empresa": "empresa", "razao": "razao_social",
    # e-mail
    "email": "email", "e-mail": "email", "mail": "email", "email corporativo": "email",
    "correo": "email",
    # telefone
    "telefone": "telefone", "tel": "telefone", "celular": "telefone", "whatsapp": "telefone",
    "fone": "telefone", "phone": "telefone", "ddd": "telefone", "cel": "telefone",
    "numero": "telefone", "número": "telefone", "contato telefonico": "telefone",
    # CEP
    "cep": "cep", "codigo postal": "cep", "zipcode": "cep", "zip": "cep",
    # CNPJ
    "cnpj": "cnpj", "cpnj": "cnpj", "doc": "cnpj", "documento": "cnpj",
    "cnpj/mf": "cnpj", "inscricao municipal": None,  # NÃO mapear p/ cnpj (IE ≠ CNPJ)
    # site
    "site": "site", "website": "site", "url": "site", "homepage": "site", "dominio": "site",
}


def _norm_header(h: str) -> str:
    h = (h or "").strip().lower().replace("_", " ").replace("-", " ")
    h = re.sub(r"\s+", " ", h)
    return h


EMAIL_ACCENTS = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüç", "aaaaaeeeeiiiiooooouuuuc")


def guess_mapping(headers: list[str]) -> dict[str, str | None]:
    """header original → campo canônico (ou None se irreconhecível)."""
    mapping: dict[str, str | None] = {}
    used: set[str] = set()
    for h in headers:
        key = _norm_header(h)
        target = ALIASES.get(key)
        if target is None:
            # fallback p/ e-mail com acentos ("E-Mail" → "e mail")
            k2 = key.translate(EMAIL_ACCENTS)
            if k2 in ("e mail", "email"):
                target = "email"
        if target and target not in used:
            mapping[h] = target
            used.add(target)
        else:
            mapping[h] = None
    return mapping


def normalize_value(field: str, raw: str) -> str:
    """Limpa o valor conforme o campo canônico (mesma normalização dos adapters)."""
    v = (raw or "").strip()
    if not v:
        return ""
    if field == "cep":
        digits = re.sub(r"\D", "", v)
        if len(digits) == 7:
            digits = "0" + digits
        return digits if len(digits) == 8 else v
    if field == "cnpj":
        digits = re.sub(r"\D", "", v)
        return digits if len(digits) == 14 else v
    if field == "email":
        return v.lower()
    if field == "telefone":
        digits = re.sub(r"\D", "", v)
        return digits[-11:] if len(digits) >= 10 else v
    return v


def read_leads_csv(content: str | bytes, threshold_note: bool = False) -> dict:
    """Parse tolerante: aceita ; ou , como separador, BOM, cabeçalhos sujos.

    Retorna {"linhas": [dict canônico], "mapeamento": {...}, "colunasignoradas": [...]}
    Cada linha é um dicionário com os campos canônicos preenchidos (vazio quando ausente).
    """
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")
    sample = content[:4096]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    headers = reader.fieldnames or []
    mapping = guess_mapping(list(headers))
    ignored = [h for h, t in mapping.items() if t is None]

    rows: list[dict] = []
    for raw_row in reader:
        row = {f: "" for f in CANONICAL_FIELDS}
        for h, target in mapping.items():
            if not target:
                continue
            value = normalize_value(target, raw_row.get(h) or "")
            if value and not row[target]:
                row[target] = value
        if any(row.values()):  # ignora linhas totalmente vazias
            rows.append(row)
    return {"linhas": rows, "mapeamento": mapping, "colunas_ignoradas": ignored}
