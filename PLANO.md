# 🎯 Plano: "EnriqueCEP" — Enriquecimento Automático de Cadastros com IA + Internet

> **Produto escolhido:** um web app (que também funciona como API) que recebe dados "crus" de pessoas e empresas
> (nome, CEP, CNPJ, e-mail, telefone) e devolve registros **enriquecidos**: endereço completo, bairro, cidade,
> UF, razão social, CNAE, situação cadastral, porte, domínio/e-mail corporativo, cargo provável, score de qualidade etc.
>
> **Por que essa é a melhor opção?**
> 1. **IA + Internet são o núcleo do produto**, não enfeite: consultas a APIs públicas na internet + LLM para
>    deduplicação, normalização, classificação e preenchimento de campos inferidos.
> 2. **Mercado comprovado**: Clearbit, ZoomInfo, Lusha e Neuron cobram caro por isso no exterior; no Brasil há espaço
>    enorme com fontes públicas gratuitas (Receita Federal, Correios, IBGE).
> 3. **Modelo de receita claro**: freemium (consultas grátis/dia) → planos por volume → API para times de vendas/marketing.
> 4. **Escopo controlável**: dá para lançar um MVP funcional em ~6 semanas, e evoluir para um "game" de gamificação
>    de CRM depois (ver Fases).
>
> **Público-alvo inicial:** times de SDR/vendas, agências de marketing, marketplaces e anyone com bases de leads sujas.

---

## 1. Visão do Produto

**Promessa em uma frase:** *"Cole sua lista de leads bagunçada e receba-a pronta para usar — endereços corrigidos,
CNPJs completos e fichas de empresa geradas por IA."*

### Funcionalidades principais (MVP)
| # | Funcionalidade | Fonte na internet | Papel da IA |
|---|----------------|-------------------|-------------|
| F1 | Enriquecer por **CEP** (rua, bairro, cidade, UF, lat/long) | API ViaCEP / BrasilAPI (grátis) | Normalizar CEPs mal formatados ("70000-00", "cep 70.000") |
| F2 | Enriquecer por **CNPJ** (razão social, nome fantasia, CNAE, sócios, situação, porte, capital) | BrasilAPI / ReceitaWS (dados abertos RFB) | Resumir a empresa em texto (site, atuação, risco) |
| F3 | Enriquecer por **e-mail/domínio** (empresa provável, rede social, tipo de domínio) | DNS + DuckDuckGo/Homepage + scraping leve | Extrair descrição do site, classificar setor com CNAE |
| F4 | **Ficha IA do lead** (score de qualidade, sugestão de abordagem, tags) | resultado de F1–F3 | LLM gera resumo + score 0–100 + e-mail de prospecção sugerido |
| F5 | Upload/exportação **CSV/XLSX** e **API REST** | — | Dedup de linhas, correção de fuso de colunas ("qualquer coluna vira campo certo") |

### O que fica para V2+
- Enriquecimento por telefone (portabilidade/operadora), busca de LinkedIn público, matching com dados IBGE
  (renda média do bairro), extensão de navegador Chrome, webhooks para CRM (HubSpot/Pipedrive).

---

## 2. Arquitetura Técnica

```
[Usuário] ──► Front Next.js (upload CSV, tabela editável, dashboard)
                 │
                 ▼
        API FastAPI (Python 3.12)
                 │  ├─ Fila de jobs assíncronos (Celery/Redis ou arq)
                 │  ├─ Camada "Fontes" (adapters): ViaCEP, BrasilAPI, ReceitaWS, DNS/scraping
                 │  │     └─ cache Redis (TTL 7–30 dias) + rate limiting por fonte
                 │  ├─ Camada IA: LLM (GPT-4o-mini / Claude Haiku via gateway)
                 │  │     └─ prompts: normalização, dedup fuzzy, classificação CNAE, resumo de empresa
                 │  └─ Postgres (registros enriquecidos, usuários, créditos de uso)
```

**Decisões-chave**
- **Adapters plugáveis de fonte**: cada API da internet é um módulo isolado → trocar/adicionar fonte sem tocar no core.
- **Cache agressivo**: mesmo CEP/CNPJ nunca é consultado duas vezes dentro do TTL → custo baixo e resposta rápida.
- **IA onde ela paga o jantar**: LLM só para (a) entrada ambígua, (b) geração de texto/score. Campos determinísticos
  (CEP→rua) vêm direto da API, sem LLM (mais barato e confiável).
- **Fallback em cascata**: ViaCEP falhou → BrasilAPI → base local de CEP dos Correios (dump público).

### Stack sugerida
| Camada | Escolha | Motivo |
|---|---|---|
| Frontend | Next.js + Tailwind + TanStack Table | tabela grande com edição/exportação |
| Backend | FastAPI + Pydantic | validação de schema, async, docs automáticas |
| Fila | arq ou Celery + Redis | uploads de 10k linhas processam em background |
| Banco | Postgres (+ pg_trgm p/ fuzzy match) | relacional + busca aproximada p/ dedup |
| IA | OpenAI GPT-4o-mini (com saída JSON estruturada) | custo/baixo latency; trocar por modelo aberto se necessário |
| Infra | Railway/Fly.io ou VPS + Docker Compose | deploy simples, escala horizontal na fila |

---

## 3. Roadmap por Fases (6 semanas até MVP pago)

### Fase 0 — Validação (Semana 1)
- [ ] Landing page com waitlist + demo interativa (colar 5 CNPJs → ver resultado).
- [ ] 15 entrevistas com SDRs/gestores de growth: "o que te faz descartar um lead?" 
- [ ] Definir preço inicial: Free 100 consultas/mês · Starter R$97/mês (5k) · Pro R$297/mês (25k + API).
- **Gate de saída:** ≥30% dos entrevistados dizem que pagariam; ≥50 cadastros na waitlist.

### Fase 1 — Núcleo de enriquecimento (Semanas 2–3)
- [ ] Adapters ViaCEP + BrasilAPI/CNPJ com cache Redis e fallback.
- [ ] API REST `/enrich` (POST linha → objeto enriquecido) + testes (respx/pact mockando fontes).
- [ ] Importador CSV "tolerante": mapeamento automático de colunas com LLM (few-shot com cabeçalhos reais sujos).
- **Entregável:** demo interna subindo planilha de 1.000 leads em <5 min.

### Fase 2 — Camada IA + UI (Semana 4)
- [ ] Prompt de **ficha do lead**: resumo da empresa, setor CNAE, score 0–100, motivo do score (JSON estruturado).
- [ ] Dedup fuzzy (nome+domínio+telefone) com similaridade pg_trgm + desempate por LLM.
- [ ] Front: upload → progresso do job → tabela filtrável → export CSV/XLSX.
- **Gate:** amostra de 200 leads com ≥90% de campos críticos corretos vs. revisão humana.

### Fase 3 — Lançamento pago (Semanas 5–6)
- [ ] Billing Stripe (checkout + medição de créditos por consulta; guardrail de custo de LLM por conta).
- [ ] Onboarding por e-mail, termos de uso/LGPD (dados públicos de PJ; base legal legítimo interesse; DPA).
- [ ] Lançamento: Product Hunt BR, comunidades de vendas/growth, 3 posts "antes/depois" de planilhas reais.
- **Meta de lançamento:** 50 contas ativas, 10 pagantes, NPS > 40.

### Fase 4 — Crescimento (Mês 3+)
- [ ] Extensão Chrome (enriquece página de lead ao navegar).
- [ ] Integrações HubSpot/Pipedrive/Zapier (canal de distribuição).
- [ ] **Modo "Game"** (ver seção 5): gamificação de limpeza de CRM.

---

## 4. Riscos e Mitigações

| Risco | Impacto | Mitigação |
|---|---|---|
| Fonte pública fora do ar/muda schema | Alto | Cascata de fallbacks + testes de contrato semanais + dump local de CEP |
| Custo de LLM escalar com volume | Médio | Cache de prompts por hash de entrada; LLM só em casos ambíguos; modelo mini |
| Alucinação da IA em score/resumo | Médio | Saída JSON com schema validado; campos factuais nunca vêm do LLM |
| LGPD (dados pessoais) | Médio | MVP focado em dados PJ/públicos; anonimizar logs; DPIA antes de dados PF |
| Concorrente grande copia | Baixo/Médio | Velocidade + nicho (CRM brasileiros, Pix/WhatsApp nativos) |

---

## 5. Bônus — Evolução "Game": *DataQuest CRM*
Depois do MVP, adicionar camada lúdica para retenção de equipes:
- Times ganham XP ao limpar/enriquecer registros; ranking semanal por empresa.
- "Chefes de fase" = bases corrompidas para sanar contra o relógio usando as ferramentas de IA.
- Badges por cobertura (% de leads enriquecidos) — sharing natural gera aquisição viral B2B.

---

## 6. Métricas Norte
- **North Star:** nº de registros enriquecidos/semana.
- Ativação: % de contas que sobem 1ª planilha em <24h (meta >60%).
- Qualidade: taxa de correção manual pós-enriquecimento (meta <5%).
- Unit economics: custo/fonte+LLM por registro ≤ R$0,002; margem bruta >80%.

---

## 7. Primeiro Sprint (lista de tarefas imediata)
1. Criar repositório `enriquecep` (FastAPI + Next.js + docker-compose com Postgres/Redis).
2. Implementar adapter ViaCEP + endpoint `/enrich/cep` com cache e 10 testes.
3. Implementar adapter BrasilAPI CNPJ + endpoint `/enrich/cnpj`.
4. Prototipar prompt de ficha IA com 20 CNPJs reais e validar formato JSON.
5. Landing page com demo embed + formulário de waitlist.
