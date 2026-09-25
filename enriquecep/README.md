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
| `POST /import/csv` | F5 — upload de CSV sujo: mapeamento automático de colunas + normalização + dedup fuzzy (human-in-the-loop: revisar antes de enriquecer) |
| `POST /enrich/batch` | Job assíncrono: N leads → enriquecimento paralelo (semáforo) com dedup opcional |
| `GET /enrich/batch/{job_id}` | Progresso (`done/total`, status) + resultados parciais do job |
| `GET /export/csv?job_id=` | Download do CSV enriquecido (cidade/UF/Razão Social/CNAE/score/tags/abordagem) |
| `GET /health` | healthcheck |

Fluxo completo (validado ponta a ponta com fontes reais):
```bash
curl -s -F file=@leads_sujo.csv localhost:8000/import/csv        # → JSON {"leads":[...]}
curl -X POST localhost:8000/enrich/batch -H 'Content-Type: application/json' \
  -d '{"leads": <leads acima>}'                                   # → {"job_id":"job-0001"}
curl localhost:8000/enrich/batch/job-0001                         # acompanha progresso
curl -OJ "localhost:8000/export/csv?job_id=job-0001"              # planilha pronta p/ Excel BR (;)
```

## Testes
```bash
python -m pytest tests -q              # 20 unitários/E2E offline (fontes mockadas)
RUN_LIVE=1 python -m pytest tests -q   # 22 testes, incl. contratos contra ViaCEP/BrasilAPI reais
```

## Estrutura
- `app/sources.py` — adapters das fontes + cache (trocar por Redis em produção) + normalizadores
- `app/ia.py` — ficha do lead (heurísticas determinísticas com o mesmo schema JSON do LLM; trocar por GPT-4o-mini/Claude Haiku mantendo validação de schema — guardrail anti-alucinação)
- `app/csv_import.py` — importador CSV tolerante (aliases de cabeçalho sujos, `;`/`,`, BOM, normalização de valores)
- `app/dedup.py` — dedup fuzzy: chave forte (CNPJ > e-mail > telefone) + similaridade de nome com union-find (em produção: pg_trgm + desempate por LLM)
- `app/main.py` — FastAPI (endpoints single, lote assíncrono, import/export)

Próximos passos (PLANO.md): Fase 2 — LLM real na ficha/mapeamento, front Next.js (upload → progresso → tabela → export); Fase 3 — billing Stripe + LGPD/DPA.
