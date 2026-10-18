# Feature Specification: Análises de compras

**Feature Branch**: `005-analises`

**Created**: 2026-10-15

**Status**: Implemented (MVP)

**Input**: Especificação Funcional RF-09…RF-11, §4.3; Especificação Técnica §6 (`/analytics/summary`),
§9; Plano de Testes F-18…F-21, §7, §12.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Dashboard do período (Priority: P1)

O utilizador escolhe mês, trimestre, ano ou intervalo e vê gasto total, compras, unidades, preço
médio, variação face ao período anterior, evolução temporal, distribuição por supermercado e
produtos mais comprados por quantidade e valor. Só compras confirmadas contam.

**Why this priority**: é o valor entregue ao utilizador.

**Independent Test**: histórico sintético de cinco notas confirmadas em `tests/api/test_analytics.py`.

**Acceptance Scenarios**:

1. **Given** compras em vários meses, **When** filtra setembro, **Then** só compras de setembro
   contam, usando a hora de Lisboa (30/09 23:30 é setembro; 01/10 00:15 é outubro). — F-18, PT-AC-4 ·
   `tests/api/test_analytics.py::test_month_filter_and_lisbon_timezone`
2. **Given** o período, **When** pede o resumo, **Then** cartões, variação face ao período anterior
   equivalente, série diária, distribuição por supermercado e rankings são coerentes; um ano usa
   séries mensais. — RF-09 · `tests/api/test_analytics.py::test_cards_variation_series_and_rankings`,
   `tests/api/test_analytics.py::test_year_view_uses_monthly_buckets`
3. **Given** filtro de supermercado, **When** é aplicado, **Then** altera todos os cartões, séries e
   rankings. — F-19, PT-AC-5 · `tests/api/test_analytics.py::test_merchant_filter_changes_every_card_and_chart`
4. **Given** um período sem compras ou com um só ponto, **When** pede o resumo, **Then** a resposta
   indica `empty`/`insufficient_data` em vez de um gráfico enganador. — F-21 ·
   `tests/api/test_analytics.py::test_empty_period_and_insufficient_data`
5. **Given** um intervalo invertido ou superior a 3 anos, **When** pede o resumo, **Then** recebe 422.
   — RF-11 · `tests/api/test_analytics.py::test_invalid_ranges`

---

### User Story 2 - Produto e histórico de preços (Priority: P1)

**Acceptance Scenarios**:

1. **Given** um produto comprado em vários supermercados, **When** abre o detalhe, **Then** vê
   quantidade total, gasto, preço médio ponderado com a base (un), mínimo, máximo, histórico por data
   e comparação entre supermercados. — F-20, RF-10 ·
   `tests/api/test_analytics.py::test_product_history_weighted_average_and_merchant_comparison`
2. **Given** um produto vendido ao peso, **When** abre o detalhe, **Then** os preços são por kg.
   — RF-10 · `tests/api/test_analytics.py::test_weighed_product_uses_price_per_kg`
3. **Given** grafias diferentes do mesmo produto, **When** é normalizado, **Then** agrupa no mesmo
   produto. — F-20 · `tests/unit/test_privacy_logging.py::test_normalization_merges_spelling_variants`

### Edge Cases

- Produto com unidades mistas → a base mais frequente é usada e `mixed_units=true`.
- Pesquisa com caracteres especiais/wildcards → tratada como texto (ver 006, SEC-injection).
- A apresentação do estado vazio e da alternativa textual aos gráficos é verificada no Frontend.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `GET /v1/analytics/summary` MUST aceitar `from`, `to`, `merchant`, `product_id`.
- **FR-002**: Gasto MUST ser Σ(total da linha − desconto) de compras `CONFIRMED`.
- **FR-003**: Variação MUST comparar com o período imediatamente anterior de igual duração.
- **FR-004**: Preço médio de um produto MUST ser ponderado pela quantidade e declarar a base (un, kg, l).
  O resumo não calcula preço médio global: misturar produtos de preços distintos não tem significado.
- **FR-005**: Os agregados são consultas SQL sobre compras confirmadas (idempotentes por construção),
  sem materializações — desvio consciente justificado pelas medições abaixo.
- **FR-006**: `GET /v1/analytics/consumption` MUST aceitar `from`, `to`, `merchant`, `limit` e devolver, por
  produto identificado, compras (notas), unidades, unidades/mês no período, intervalo médio entre dias de
  compra (nulo com uma só compra) e primeira/última compra, ordenado por nº de compras.

### Key Entities

- Resumo (cartões, série, distribuição, rankings) e análise de produto (histórico, comparação).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: dashboard p95 < 2 s com 5 000 linhas (medido: ≈ 47 ms ano, 28 ms mês).
- **SC-002**: dashboard com 50 000 linhas p95 ≈ 0,3 s (medido com `scripts/seed_perf.py --bench`).
- **SC-003**: eliminar ou confirmar uma compra reflete-se no pedido seguinte (sem cache).

## Assumptions

- Fuso horário fixo Europe/Lisbon (supermercados portugueses).
- Categorias de produto estão fora do MVP.
