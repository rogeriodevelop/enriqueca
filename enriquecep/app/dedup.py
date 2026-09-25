"""Dedup fuzzy (Fase 2 do PLANO.md): agrupa linhas duplicadas da mesma base.

Estratégia em duas etapas, espelhando o plano (pg_trgm + desempate por LLM):
1. Chave exata normalizada (cnpj > e-mail corporativo > telefone) → duplicata certa;
2. Similaridade de nomes (difflib ≥ limiar) entre registros sem chave em comum
   → candidato a duplicata (em produção: pg_trgm; desempate final pode ir ao LLM).
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

DEFAULT_THRESHOLD = 0.85


def _norm_str(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


COMPANY_SUFFIXES = {"ltda", "ltda", "me", "pp", "epp", "brasil", "do brasil", "eireli"}

def _core_name(s: str | None) -> str:
    """Remove sufixos societários comuns para comparação ('Tech Solutions Ltda' ≈ 'Tech Solution LTDA')."""
    words = [w for w in _norm_str(s).split() if w not in COMPANY_SUFFIXES]
    return " ".join(words)

def name_similarity(a: str | None, b: str | None) -> float:
    """Similaridade 0..1 entre dois nomes (após normalização de acentos/caixa/sufixos)."""
    na, nb = _core_name(a), _core_name(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _digits(s: str | None) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[-10:] if len(d) >= 10 else ""  # compara os últimos 10 dígitos (DDD+numero)


def match_key(rec: dict) -> str | None:
    """Chave forte de identidade: CNPJ > e-mail > telefone. None se não houver."""
    cnpj = re.sub(r"\D", "", rec.get("cnpj") or "")
    if len(cnpj) == 14:
        return f"cnpj:{cnpj}"
    email = _norm_str(rec.get("email"))
    if "@" in email:
        return f"email:{email}"
    tel = _digits(rec.get("telefone") or rec.get("phone") or rec.get("celular"))
    if tel:
        return f"tel:{tel}"
    return None


def find_duplicates(records: list[dict], threshold: float = DEFAULT_THRESHOLD) -> list[list[int]]:
    """Retorna grupos de índices [i, j, ...] considerados duplicados (união de chave forte + nome similar).

    Cada grupo tem tamanho ≥ 2; registros únicos não entram em nenhum grupo.
    Usa union-find para agrupar transitivamente (A=B e B=C ⇒ {A,B,C}).
    """
    keys = [match_key(r) for r in records]
    names = [r.get("nome") or r.get("razao_social") or r.get("name") for r in records]

    parent = list(range(len(records)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # 1) duplicatas por chave forte
    by_key: dict[str, int] = {}
    for i, k in enumerate(keys):
        if not k:
            continue
        if k in by_key:
            union(by_key[k], i)
        else:
            by_key[k] = i

    # 2) duplicatas por nome similar — candidata (mesmo entre registros com chaves fortes diferentes;
    #    em produção, pares ambíguos vão para desempate por LLM — PLANO.md Fase 2)
    for a in range(len(records)):
        for b in range(a + 1, len(records)):
            if keys[a] and keys[b] and keys[a] == keys[b]:
                continue  # já unidos pelo passo 1
            if name_similarity(names[a], names[b]) >= threshold:
                union(a, b)

    groups: dict[int, list[int]] = {}
    for i in range(len(records)):
        groups.setdefault(find(i), []).append(i)
    return sorted([g for g in groups.values() if len(g) > 1])


def dedupe(records: list[dict], threshold: float = DEFAULT_THRESHOLD) -> tuple[list[dict], list[list[int]]]:
    """Remove duplicatas mantendo o registro mais completo de cada grupo (mais campos preenchidos).

    Retorna (registros únicos, grupos removidos) — os grupos indicam quais índices
    foram descartados em favor do representante, para auditoria/exportação "antes/depois".
    """
    dup_groups = find_duplicates(records, threshold)
    drop: set[int] = set()
    keep_of_group: dict[int, int] = {}
    for g in dup_groups:
        best = max(g, key=lambda i: sum(1 for v in records[i].values() if v not in (None, "", [])))
        keep_of_group[g[0]] = best
        drop.update(i for i in g if i != best)
    uniques = [r for i, r in enumerate(records) if i not in drop]
    removed_groups = [[i for i in g if i != keep_of_group[g[0]]] for g in dup_groups]
    return uniques, removed_groups
