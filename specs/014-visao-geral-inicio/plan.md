# Implementation Plan: Visão geral do Início, totais por mês e produtos enriquecidos

**Branch**: `014-visao-geral-inicio` (sem ramo git próprio) | **Date**: 2026-10-31 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/014-visao-geral-inicio/spec.md`

**Decisões:** as marcas `[NEEDS CLARIFICATION]` da spec foram resolvidas em `research.md` (Q1 a Q11) e confirmadas pela Liz a 2026-10-31; a Q7 mudou (últimas compras também em revisão e em processamento, com `status`). A `spec.md` já as incorpora.

## Summary

Três acrescentos de leitura à API de análises, sem tabela nova nem migração:

1. **Visão geral do mês** (`GET /v1/analytics/overview?month=YYYY-MM`): gasto, meta e faixa do anel, média mensal e tendência, compras do mês e do anterior, gasto por semana, por mercado e últimas compras (estas também com as que requerem revisão ou estão em processamento). A regra de negócio (percentagem da meta, faixa, média, tendência) vive em **funções puras** testadas primeiro; a consulta lê as compras confirmadas do mês numa só *query* e agrega em Python.
2. **Totais por mês** (`GET /v1/analytics/months`): por mês, número de compras confirmadas e total em cêntimos. Serve a lista de Talões e a média da visão geral (mesma função).
3. **Produtos enriquecidos** (`GET /v1/products`): cada item ganha preço médio ponderado com a sua base, número de compras e de mercados, com o mesmo cálculo do detalhe do produto (função partilhada, números iguais).

Limites do anel e mínimo de meses da média passam a ser quatro chaves obrigatórias de `config/app-config.yaml` (sem *default* no código, como a spec 008).

Contrato primeiro: depois de implementar regenera-se `openapi.json` e o frontend 011 corre `pnpm gen:api`.

## Technical Context

**Language/Version**: Python 3.12 (`>=3.12,<3.13`)

**Primary Dependencies**: FastAPI, SQLAlchemy 2, Pydantic 2 (nada novo)

**Storage**: PostgreSQL, só leitura; **sem migração** (o índice `ix_receipts_user_purchase` serve a consulta do mês). A meta vem de `user_profiles.monthly_goal_cents` (spec 013)

**Testing**: pytest (`@pytest.mark.req("F-xx")`), fixtures `client`, `login`, `db` de `tests/conftest.py`; testes de unidade das funções puras escritos primeiro (pedido do plano UX v2)

**Target Platform**: contentores Linux; nesta máquina o `make` não corre: Docker Desktop (comandos em `quickstart.md`)

**Project Type**: web-service (API + worker; o frontend é outro repositório)

**Performance Goals**: p95 de `overview` < 2 s com 5 000 linhas (constituição); medir com `make bench` (equivalente em contentor) com 5 000 e 50 000 linhas

**Constraints**: dinheiro em cêntimos inteiros e percentagens com aritmética de inteiros, meio para cima (II); só compras `CONFIRMED` (§12.4); mês em hora de Lisboa (F-18); sem valores de compras nem do perfil em logs ou etiquetas de métricas (IV); chaves de configuração obrigatórias e sem *default* no código

**Scale/Scope**: 3 endpoints (2 novos, 1 alargado), 4 chaves de configuração, 0 tabelas, ~7 identificadores de teste

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.* Constituição do backend v1.0.0.

| Princípio | Avaliação |
|---|---|
| I. Processamento local | N/A (leitura da base; nada sai para serviços externos) |
| II. Dinheiro exato | Passa: cêntimos inteiros; `goal_pct`, partilhas e tendência com aritmética de inteiros e arredondamento explícito (meio para cima); preço médio ponderado por quantidade com a base declarada (`price_basis`), calculado com `Decimal` |
| III. Nunca inventar valores | Passa: sem meta → `goal_*` nulos e `band = none`; abaixo do mínimo de meses → média e tendência nulas; produto sem peso/volume → sem preço por kg/l (§12.4); mês vazio não conta como zero na média |
| IV. Privacidade por omissão | Passa: todas as consultas por `user_id` (outro utilizador nunca vê, PT-AC-9, SEC-authz); `ConsentedUser` em todas as rotas; logs e métricas sem valores (teste SEC-logs) |
| V. Mobile-first e acessível | N/A (API); o texto («Tranquilo», saldo) é do frontend; a API devolve números e faixa, nunca linguagem de perda |
| VI. Rastreabilidade | Passa (Q10 confirmada): ids F-176 a F-182 da faixa reservada à continuação da experiência v2, porque F-119 a F-125 não chegam; exige subir `F_MAX` em `scripts/traceability.py` (tarefa) |
| VII. Simplicidade | Passa: funções puras pequenas, uma consulta por conceito, sem tabela nem cache; a tendência «na média» é o único campo acrescentado à spec (Q4) |

Resultado: **sem violações**. Risco aceite: Q1 (o gasto do Início pode diferir em cêntimos do Resumo até à Fase 4).

Reavaliado após o desenho: sem alterações.

## Project Structure

### Documentation (this feature)

```text
specs/014-visao-geral-inicio/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── api.md
└── tasks.md
```

### Source Code (repository root)

```text
config/app-config.yaml                 # + 4 chaves (comentadas)
src/basketiq/
├── app_config.py                      # + goal_ring_green_up_to_pct, goal_ring_orange_up_to_pct, average_min_months, average_on_target_pct (+ validação green < orange)
├── domain/overview.py                 # NOVO: funções puras (goal_pct, band, partilhas, média, tendência, semanas)
├── services/analytics.py              # + overview, month_totals; search_products alargado; cálculo de preço médio partilhado com product_analytics
├── api/
│   ├── schemas.py                     # + OverviewOut e itens, MonthTotalOut; ProductSearchItem alargado
│   └── routers/
│       ├── analytics.py               # + GET /analytics/overview, GET /analytics/months
│       ├── config.py                  # (sem alteração: as chaves novas não são públicas)
│       └── admin.py                   # ProductConfigOut + 4 campos (CFG-02)
scripts/traceability.py                # F_MAX 175 → 205 (Q10)
tests/
├── unit/test_overview_rules.py        # NOVO: faixas, fronteiras, média, tendência, partilhas, semanas
├── unit/test_app_config.py            # + chaves novas
├── api/test_overview.py               # NOVO
├── api/test_analytics.py              # + produtos enriquecidos (compatibilidade F-20)
└── api/test_config.py                 # + vista do operador
README.md                              # + linhas na tabela de chaves de configuração e dos endpoints
```

**Structure Decision**: a regra de negócio fica em `domain/overview.py` (sem acesso a dados, como `domain/money.py`), para os testes de unidade da spec; a consulta e a montagem ficam em `services/analytics.py`, onde o plano UX v2 as manda pôr.

## Complexity Tracking

Sem violações a justificar.
