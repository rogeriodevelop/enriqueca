"""Ponto de entrada ASGI para deploy no Vercel (também serve Render/Railway).

O Vercel não roda `uvicorn` nem servidores longos — ele procura um handler ASGI
exportado como `app` dentro da pasta /api. Este arquivo coloca enriquecep/ no
sys.path e reexporta a aplicação FastAPI.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "enriquecep"))

# Em serverless o disco é efêmero: SQLite em memória por instância
# (sobrescreva com ENRIQUECEP_DB=/tmp/enriquecep.db ou um volume montado).
os.environ.setdefault("ENRIQUECEP_DB", ":memory:")

from app.main import app  # noqa: E402,F401  (o Vercel procura exatamente este nome)
