# EnriqueCEP — MVP (Fase 1 do PLANO.md)

API de enriquecimento de cadastros usando **fontes da internet** (ViaCEP, BrasilAPI/Receita)
+ **camada IA** (ficha do lead com score 0–100, resumo, tags e sugestão de abordagem).

## Rodar
```bash
pip install -r requirements.txt
./run.sh                # sobe em http://localhost:8000 (docs interativas em /docs)
```

## Endpoints
| Rota | Descrição |
|---|---|
| `POST /enrich/cep` | F1 — normaliza CEP ("70.040-010", "cep 70040010") e devolve endereço completo |
| `POST /enrich/cnpj` | F2 — razão social, fantasia, CNAE, situação, porte, capital, sócios (QSA), contato |
| `POST /enrich/lead` | F4+F5 — aceita linha crua `{nome,email,cep,cnpj}` → dados enriquecidos + ficha IA |
| `GET /health` | healthcheck |

Exemplo:
```bash
curl -X POST localhost:8000/enrich/lead -H 'Content-Type: application/json' \
  -d '{"cep":"70.040-010","cnpj":"19.131.243/0001-97","email":"fulano@gmail.com"}'
```

## Testes
```bash
python -m pytest tests -q              # unitários (offline; fontes mockadas por entrada inválida)
RUN_LIVE=1 python -m pytest tests -q   # + testes de contrato contra ViaCEP/BrasilAPI reais
```

## Estrutura
- `app/sources.py` — adapters das fontes + cache (trocar por Redis em produção) + normalizadores
- `app/ia.py` — ficha do lead (heurísticas determinísticas com o mesmo schema JSON do LLM; trocar por GPT-4o-mini/Claude Haiku mantendo validação de schema — guardrail anti-alucinação)
- `app/main.py` — FastAPI

Próximos passos: Fase 1 restantes (importador CSV tolerante) → Fase 2 (LLM real + dedup + front Next.js). Ver `/workspace/PLANO.md`.
