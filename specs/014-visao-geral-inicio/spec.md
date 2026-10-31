# Feature Specification: Visão geral do Início, totais por mês e produtos enriquecidos

**Feature Branch**: `014-visao-geral-inicio`

**Created**: 2026-10-30

**Status**: Decisões Q1 a Q11 confirmadas pela Liz a 2026-10-31 (Q7 mudou); pronta para implementar

**Input**: Especificação Funcional v1.4 RF-15, RF-09, RF-10, §12.4; `Documentation/Super Mercado_—_Plano_de_Implementação_UX_v2.md` (Fases 3 e 4, lacunas de backend 2 e 3). Frontend: `../basketIQ-frontend/specs/011-inicio`, `012-taloes`.

**Convenção de rastreabilidade**: cada critério cita o RF da Especificação Funcional. Os identificadores F-176 a F-182 (tabela no fim) são os do backend desta spec; o frontend 011 usa F-183 a F-188. O plano manda escrever **primeiro** os testes de unidade das faixas e da média (regra de negócio).

**Fora desta spec (decisão de 2026-10-30):** o endpoint de diferenças de preço entre mercados (`price-gaps`) já não faz parte da Fase 3. Constrói-se depois do passo C, com as regras D11 e D12, na spec 015 (US8).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Gasto do mês, meta e faixa do anel (Priority: P1)

`GET /v1/analytics/overview?month=YYYY-MM` devolve o gasto do mês, a percentagem da meta e a faixa.

**Why this priority**: é o núcleo do Início e a regra de negócio mais visível.

**Independent Test**: testes de unidade da função de faixas e testes de API com compras confirmadas.

**Acceptance Scenarios**:

1. **Given** meta de 400 € e gasto de 152 €, **When** pede o mês, **Then** `goal_pct` = 38 e `band` = `ok`. — RF-15
2. **Given** meta de 400 € e gasto de 248 €, **When** pede o mês, **Then** `goal_pct` = 62 e `band` = `attention`. — RF-15
3. **Given** meta de 400 € e gasto de 352 €, **When** pede o mês, **Then** `goal_pct` = 88 e `band` = `high`. — RF-15
4. **Given** nenhuma meta definida, **When** pede o mês, **Then** `goal_cents` e `goal_pct` são nulos, `band` = `none` e `spent_cents` vem na mesma. — RF-15
5. **Given** gasto igual ou superior a 100 % da meta, **When** pede o mês, **Then** `goal_pct` ≥ 100 e `band` = `high`, sem erro. — RF-15. O «saldo da meta» e a linguagem do ecrã são do cliente (`goal_cents − spent_cents`); a API devolve só números, nunca texto de perda.
6. **Given** `goal_pct` calculado como a percentagem arredondada ao inteiro (meio para cima) com aritmética de inteiros, **When** a faixa é decidida, **Then** calcula-se sobre esse `goal_pct`, o mesmo que o ecrã mostra: verde até 50 % inclusive, laranja acima de 50 % até 75 % inclusive, vermelho acima de 75 %. — RF-15, constituição II
7. **Given** meta de 400 € (40 000 cêntimos) e gasto de 20 000 cêntimos (50 %), **When** pede o mês, **Then** `goal_pct` = 50 e `band` = `ok`. — RF-15 (teste de fronteira)
8. **Given** meta de 40 000 cêntimos e gasto de 20 160 (50,4 %), **When** pede o mês, **Then** `goal_pct` = 50 e `band` = `ok`. — RF-15 (teste de fronteira)
9. **Given** meta de 40 000 cêntimos e gasto de 20 200 (50,5 %), **When** pede o mês, **Then** `goal_pct` = 51 e `band` = `attention`. — RF-15 (teste de fronteira)
10. **Given** meta de 40 000 cêntimos e gasto de 30 000 (75 %), **When** pede o mês, **Then** `goal_pct` = 75 e `band` = `attention`. — RF-15 (teste de fronteira)
11. **Given** meta de 40 000 cêntimos e gasto de 30 160 (75,4 %), **When** pede o mês, **Then** `goal_pct` = 75 e `band` = `attention`. — RF-15 (teste de fronteira)
12. **Given** meta de 40 000 cêntimos e gasto de 30 200 (75,5 %), **When** pede o mês, **Then** `goal_pct` = 76 e `band` = `high`. — RF-15 (teste de fronteira)
13. **Given** limites configurados de forma diferente de 50 e 75 (chaves `GOAL_RING_GREEN_UP_TO_PCT` e `GOAL_RING_ORANGE_UP_TO_PCT`, «até X inclusive»), **When** pede o mês, **Then** a faixa respeita a configuração e não valores fixos no código. — RF-15
14. **Given** compras por confirmar ou em revisão, **When** pede o mês, **Then** não entram no gasto. — RF-15, §12.4
15. **Given** uma compra «só com total», **When** pede o mês, **Then** entra no gasto do mês. — RF-15, §12.0. O gasto de uma compra é o seu total impresso; se faltar, a soma líquida das linhas (Q1).
16. **Given** uma compra às 00:30 de 1 de novembro (hora de Lisboa), **When** pede outubro e novembro, **Then** conta só em novembro. — RF-15, F-18
17. **Given** `month` inválido, **When** pede, **Then** recebe erro de validação; sem `month`, usa o mês corrente de Lisboa. — RF-15

---

### User Story 2 - Média mensal e tendência (Priority: P1)

**Acceptance Scenarios**:

1. **Given** menos meses com compras confirmadas do que o mínimo configurado (2 por omissão), **When** pede a visão geral, **Then** `monthly_average_cents` e `recent_vs_average_pct` são nulos. — RF-15
2. **Given** 2 meses confirmados de 300 € e 336 €, **When** pede, **Then** a média é 318 € em cêntimos inteiros, arredondada ao cêntimo (meio para cima). — RF-15, constituição II
3. **Given** um mês sem compras entre dois meses com compras, **When** calcula, **Then** o mês vazio não conta como zero. — RF-15 (Q2).
4. **Given** o mês corrente ainda incompleto, **When** calcula a média, **Then** o mês corrente não entra: a média usa só meses completos com compras confirmadas e não depende do mês pedido. — RF-15 (Q2)
5. **Given** média e meses recentes, **When** pede, **Then** `recent_vs_average_pct` compara os 2 meses completos mais recentes com a média, com sinal (por exemplo −7 % = 7 % abaixo), e é nulo com menos de 3 meses com dados (Q3). `average_trend` diz `below`, `on_average` ou `above` com o limiar `AVERAGE_ON_TARGET_PCT` (5 por omissão, Q4). — RF-15
6. **Given** meta nula, **When** pede, **Then** a média calcula-se na mesma e a meta não a substitui nem é substituída por ela. — RF-13, RF-15
7. **Given** compras por confirmar, **When** calcula, **Then** não entram. — RF-15, §12.4

---

### User Story 3 - Compras, semanas, mercados e últimas compras (Priority: P1)

**Acceptance Scenarios**:

1. **Given** 7 compras confirmadas em outubro e 11 em setembro, **When** pede outubro, **Then** `purchases_this_month` = 7 e `purchases_last_month` = 11. — RF-15
2. **Given** um mês, **When** pede, **Then** `weeks[]` traz o gasto por semana, soma igual ao gasto do mês e semanas sem compras a zero. — RF-15. A semana é um bloco de 7 dias desde o dia 1 (1–7, 8–14, 15–21, 22–28, 29–fim); a última pode ter 1 a 3 dias (Q5).
3. **Given** compras em 3 mercados, **When** pede, **Then** `by_merchant[]` vem ordenado por gasto decrescente, com valor e percentagem, e a soma das percentagens é 100. — RF-15. As partilhas somam 100 pelo método do maior resto (Q6).
4. **Given** compras recentes, **When** pede, **Then** `recent_receipts[]` traz as 5 mais recentes do mês, da mais recente, com mercado, data, total e `status`. Incluem também as que requerem revisão (`REVIEW_REQUIRED`, `COMPLETED`) ou estão em processamento (`RECEIVED`, `VALIDATING`, `EXTRACTING`); as de erro ficam de fora. Gasto, média, partilhas, semanas e contagens continuam só com confirmadas. — RF-15 (Q7, decisão da Liz)
5. **Given** uma conta sem nenhuma compra confirmada, **When** pede, **Then** `empty` = true. — RF-15
6. **Given** uma conta com compras mas sem nenhuma no mês pedido, **When** pede, **Then** `empty` = false e o mês vem com zeros. — RF-15. `empty` refere-se à conta inteira (nenhuma compra confirmada em qualquer mês), não ao mês.

---

### User Story 4 - Totais por mês para a lista de Talões (Priority: P2)

*Acrescentada nesta Fase 0: o plano (Fase 4, lacuna 3) pede o endpoint mas não lhe dá nome nem spec.*

**Acceptance Scenarios**:

1. **Given** compras confirmadas em outubro e setembro, **When** pede os totais, **Then** `GET /v1/analytics/months` devolve, por mês, o número de confirmadas e o total em cêntimos (por exemplo outubro: 7 e 24 800). — §8 (Q8)
2. **Given** meses sem compras confirmadas, **When** pede, **Then** os meses não aparecem. — §8
3. **Given** compras por confirmar, **When** pede, **Then** não entram nos totais nem na contagem de confirmadas. — §8, §12.4
4. **Given** a definição de mês, **When** compara com a da visão geral, **Then** é a mesma (hora de Lisboa). — F-18

---

### User Story 5 - Produtos com preço médio e contagens (Priority: P2)

*Acrescentada nesta Fase 0: o plano (Fase 4) manda enriquecer `GET /v1/products` sem indicar spec de backend.*

**Acceptance Scenarios**:

1. **Given** um produto com compras, **When** pesquisa, **Then** cada item traz o preço médio ponderado com a sua base, o número de compras e o número de mercados. — RF-10, constituição II
2. **Given** um produto sem unidade conhecida, **When** pesquisa, **Then** o preço por kg ou l fica vazio. — §12.4
3. **Given** a pesquisa existente, **When** o contrato é alargado, **Then** os campos atuais e a pesquisa mantêm-se compatíveis. — F-20

---

### User Story 6 - Isolamento e desempenho (Priority: P1)

**Acceptance Scenarios**:

1. **Given** o utilizador B, **When** pede qualquer destes endpoints, **Then** só vê os seus dados. — PT-AC-9, SEC-authz
2. **Given** um pedido sem sessão, **When** chama, **Then** recebe 401. — SEC-authz
3. **Given** 5 000 linhas, **When** mede a visão geral, **Then** p95 < 2 s. — §10, constituição (metas)
4. **Given** 50 000 linhas, **When** mede (Fase 8), **Then** o resultado fica registado em `make bench`. — plano Fase 8
5. **Given** erros e logs, **When** são produzidos, **Then** não contêm valores de compras nem dados de perfil. — SEC-logs

### Edge Cases

- Utilizador sem perfil (meta nula) obtém sempre resposta válida.
- Utilizador eliminado a meio de um pedido: sem erro interno.
- `month` no futuro devolve zeros, não erro.

## Requirements *(mandatory)*

- **Contrato:** `contracts/api.md`. Valores em cêntimos inteiros e percentagens inteiras (meio para cima); só dados do próprio utilizador consentido; mês em hora de Lisboa.
- **Gasto:** só compras `CONFIRMED`; por compra, `total_cents` ou, se nulo, a soma líquida das linhas (Q1). O Resumo continua a somar linhas; o alinhamento faz-se na Fase 4.
- **Meta:** a atual do perfil, também nos meses passados (Q11).
- **Configuração:** `GOAL_RING_GREEN_UP_TO_PCT` (50), `GOAL_RING_ORANGE_UP_TO_PCT` (75), `AVERAGE_MIN_MONTHS` (2) e `AVERAGE_ON_TARGET_PCT` (5), obrigatórias, sem *default* no código, visíveis ao operador e não públicas.
- **Produtos:** `GET /v1/products` ganha `avg_price_cents`, `price_basis`, `purchases` e `merchants`, com o mesmo cálculo do detalhe (Q9).

## Success Criteria *(mandatory)*

- Os 4 estados do anel (`ok`, `attention`, `high`, `none`) e os estados vazios têm testes, incluindo as fronteiras 50/50,4/50,5/75/75,4/75,5 %.
- p95 de `overview` < 2 s com 5 000 linhas; o resultado com 50 000 fica registado em `research.md` (R13).
- Nenhum valor de compra nem da meta em logs, erros ou etiquetas de métricas.

## Identificadores de teste

| Id | Critérios | Teste |
|---|---|---|
| F-176 | Configuração nova; US1.1–1.17 (regras de faixa e `overview` do gasto) | `tests/unit/test_app_config.py`, `tests/unit/test_overview_rules.py`, `tests/api/test_config.py`, `tests/api/test_overview.py` |
| F-177 | US2.1–2.7 (média, tendência, «na média») | `tests/unit/test_overview_rules.py`, `tests/api/test_overview.py` |
| F-178 | US3.1–3.6 (contagens, semanas, mercados, últimas compras, `empty`) | idem |
| F-179 | US4.1–4.4 (totais por mês) | `tests/api/test_overview.py` |
| F-180 | US5.1–5.3 (produtos enriquecidos) | `tests/api/test_analytics.py` |
| F-181 | US6.1, 6.2, 6.5 (isolamento, 401, logs) | `tests/api/test_overview.py` |
| F-182 | US6.3, 6.4 e casos limite (desempenho, utilizador apagado) | `tests/api/test_overview.py`, `scripts/seed_perf.py` |

## Decisões confirmadas (Liz, 2026-10-31)

Q1 (total da compra; o alinhamento do Resumo faz-se na Fase 4), Q2 a Q6, Q8 a Q11 como em `research.md`. **Q7 mudou:** as últimas compras incluem as que requerem revisão ou estão em processamento, com `status`; o resto continua só com confirmadas. A RF-15 do Funcional foi emendada (emenda 1.4 em curso). Ids: backend F-176 a F-182; frontend 011 F-183 a F-188.

## Assumptions

- Depende da spec 013 (meta no perfil) e do `analytics.py` existente (reaproveitar).
- O passo C usa os números 010 e seguintes; esta spec não depende dele.
