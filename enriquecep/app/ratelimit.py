"""Rate limiting (Fase 3): proteção antiabuso por IP e por fonte externa.

slowapi quando disponível; caso contrário, janela deslizante em memória própria
(mesma semântica: 429 Too Many Requests com Retry-After).
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

RATE_ENABLED = os.environ.get("RATE_LIMIT_DISABLED") != "1"

# Nome → (max_req, janela_seg)
_RULES = {
    "ip_default": (60, 60),        # 60 req/min por IP nas rotas /enrich*
    "viacep": (25, 60),            # respeitar limites públicos das fontes
    "brasilapi": (20, 60),
}

_hits: dict[str, deque] = defaultdict(deque)


def _allow(bucket: str, max_req: int, window: int) -> tuple[bool, float]:
    now = time.monotonic()
    q = _hits[bucket]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= max_req:
        return False, window - (now - q[0])
    q.append(now)
    return True, 0.0


def check_ip_rate(request: Request, bucket: str = "ip_default"):
    """Chamar no início de endpoints caros; levanta 429 se estourar."""
    if not RATE_ENABLED:
        return
    ip = request.client.host if request.client else "desconhecido"
    auth = request.headers.get("authorization", "")
    key = f"{bucket}:{auth.split()[-1] if auth else ip}"
    max_req, window = _RULES[bucket]
    ok, retry = _allow(key, max_req, window)
    if not ok:
        raise HTTPException(429, f"limite de {max_req} req/{window}s excedido; aguarde {retry:.0f}s",
                            headers={"Retry-After": str(int(retry) + 1)})


class SourceThrottle:
    """Limita chamadas simultâneas/por minuto a uma fonte externa (fallback do plano)."""

    def __init__(self, source: str):
        self.source = source

    async def acquire(self):
        if not RATE_ENABLED:
            return
        max_req, window = _RULES[self.source]
        ok, retry = _allow(f"fonte:{self.source}", max_req, window)
        if not ok:
            import asyncio
            await asyncio.sleep(min(retry + 0.1, window))  # espera simples em vez de derrubar o job
