"""F3 — Enriquecimento por e-mail/domínio (Fase 2 do PLANO.md).

Camada de FATOS (internet): DNS A/MX + scraping leve da homepage para extrair
título/descrição. A classificação de setor/CNAE é feita na camada IA (ia.classify_sector)
com o mesmo guardrail: fatos vêm das fontes, inferência vem da IA.
"""
from __future__ import annotations

import asyncio
import re

import httpx

try:
    import dns.resolver
    _DNS_OK = True
except ImportError:  # dnspython é opcional em ambientes sem rede/DNS
    _DNS_OK = False

FREE_EMAIL_DOMAINS = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "yahoo.com.br",
                      "bol.com.br", "uol.com.br", "ig.com.br", "terra.com.br", "icloud.com",
                      "mail.com", "live.com", "me.com"}

_UA = {"User-Agent": "Mozilla/5.0 (compatible; EnriqueCEP/1.0; +https://enriquecep.app)"}


def extract_domain(email_or_site: str | None) -> str | None:
    """'Joao@Empresa.com.br ' → 'empresa.com.br'; 'https://www.abc.com/x' → 'abc.com'."""
    s = (email_or_site or "").strip().lower()
    if "@" in s:
        s = s.rsplit("@", 1)[1]
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.split("/")[0].split(":")[0].strip(".")
    return s if "." in s and len(s) > 3 else None


def is_free_email(email: str | None) -> bool:
    dom = extract_domain(email or "")
    return dom in FREE_EMAIL_DOMAINS


def _dns_query(domain: str, rtype: str) -> list[str]:
    """Bloqueante — chamar via asyncio.to_thread."""
    if not _DNS_OK:
        return []
    try:
        answers = dns.resolver.resolve(domain, rtype)
        if rtype == "MX":
            return sorted([str(a.exchange).rstrip(".") for a in answers])
        return [str(a) for a in answers]
    except Exception:
        return []


async def check_domain(domain: str) -> dict:
    """Fatos de DNS: registro A, servidores MX (e-mail corporativo real?) e tipo de domínio."""
    a_rec = await asyncio.to_thread(_dns_query, domain, "A")
    mx_rec = await asyncio.to_thread(_dns_query, domain, "MX")
    tld = "." + domain.rsplit(".", 1)[-1] if "." in domain else ""
    tipo = ("corporativo" if tld in (".com.br", ".com", ".br", ".net", ".org", ".app", ".io", ".dev")
            else "outro")
    return {
        "dominio": domain,
        "registrado": bool(a_rec or mx_rec),
        "ips": a_rec[:3],
        "mx": mx_rec[:3],
        "tem_mx": bool(mx_rec),
        "free_domain": domain in FREE_EMAIL_DOMAINS,
        "tipo_dominio": tipo,
    }


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_DESC_RE = re.compile(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']', re.I | re.S)
_OG_RE = re.compile(r'<meta\s+(?:property|name)=["\']og:description["\']\s+content=["\'](.*?)["\']', re.I | re.S)


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:300]


async def fetch_homepage(domain: str) -> dict:
    """Scraping leve: GET https://{domain}/ → título + descrição (para classificação pela IA)."""
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers=_UA) as client:
            r = await client.get(f"https://{domain}/")
        html = r.text[:200_000]
        title = _TITLE_RE.search(html)
        desc = _OG_RE.search(html) or _DESC_RE.search(html)
        return {
            "ok": r.status_code < 400,
            "http_status": r.status_code,
            "titulo": _clean(title.group(1)) if title else "",
            "descricao": _clean(desc.group(1)) if desc else "",
        }
    except Exception as e:
        return {"ok": False, "erro": type(e).__name__}


async def enrich_email(email: str | None = None, site: str | None = None) -> dict:
    """Pipeline F3: extrai domínio do e-mail/site → DNS → homepage."""
    domain = extract_domain(site) or extract_domain(email)
    if not domain:
        return {"ok": False, "erro": f"sem domínio reconhecível (email={email!r}, site={site!r})"}
    dns_info = await check_domain(domain)
    home = await fetch_homepage(domain) if dns_info["registrado"] and not dns_info["free_domain"] \
        else {"ok": False, "motivo": "dominio free ou nao registrado — sem homepage"}
    return {"ok": True, "email": (email or "").lower() or None, **dns_info, "homepage": home}
