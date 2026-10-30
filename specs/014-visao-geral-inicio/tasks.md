---

description: "Tarefas da spec 014: visão geral do Início, totais por mês e produtos enriquecidos"
---

# Tasks: Visão geral do Início, totais por mês e produtos enriquecidos

**Input**: `/specs/014-visao-geral-inicio/` — [spec.md](./spec.md), [plan.md](./plan.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/api.md](./contracts/api.md), [quickstart.md](./quickstart.md)

**Tests**: pedidos (constituição VI) e **escritos primeiro**, vistos falhar (o plano UX v2 manda os testes de unidade das faixas e da média antes do resto). Cada teste leva `@pytest.mark.req("F-xx")`. Identificadores propostos **F-176 a F-182** (faixa reservada à continuação da experiência v2, Funcional §12.4), a confirmar na Q10 de `research.md`; o isolamento usa `PT-AC-9` e `SEC-authz`. Se a Liz preferir F-119 a F-125, trocam-se os números (T002 deixa de ser precisa).

**Comandos**: contentores Docker, como em `quickstart.md` (o `make` não corre nesta máquina). Não há commits nesta lista; o `/simplify` corre antes de cada commit (T033).

**Depende de**: spec 013 (tabela `user_profiles` e `MERCHANT_NAMES`) já presente. As decisões Q1 a Q10 de `research.md` estão adotadas por omissão; se a Liz mudar alguma, ajustar as tarefas indicadas entre parênteses.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: pode correr em paralelo (ficheiros diferentes, sem dependência de tarefa por acabar)
- **[Story]**: US1 gasto, meta e faixa · US2 média e tendência · US3 compras, semanas, mercados e últimas · US4 totais por mês · US5 produtos · US6 isolamento e desempenho

## Phase 1: Setup

- [ ] T001 Confirmar a linha de base sem alterar ficheiros: construir `basketiq-test` e correr a bateria, `ruff`, `mypy`, `uv lock --check` e a rastreabilidade (comandos de `quickstart.md`); registar o resultado antes de qualquer alteração

---

## Phase 2: Foundational (bloqueia as histórias)

- [ ] T002 Em `scripts/traceability.py`, subir `F_MAX` de 175 para 205 e atualizar o comentário (F-176 a F-205 são a faixa reservada à continuação da experiência v2); confirmar que a rastreabilidade continua a falhar com F-206 (Q10)
- [ ] T003 [P] Em `tests/unit/test_app_config.py` (`F-176`): `VALID` e os casos passam a incluir `GOAL_RING_GREEN_UP_TO_PCT` (50), `GOAL_RING_ORANGE_UP_TO_PCT` (75), `AVERAGE_MIN_MONTHS` (2) e `AVERAGE_ON_TARGET_PCT` (5); chave em falta, desconhecida, valor não inteiro (texto, decimal, booleano) e fora dos limites recusados (`1 ≤ verde < laranja ≤ 100`, `AVERAGE_MIN_MONTHS` de 1 a 12, `AVERAGE_ON_TARGET_PCT` de 0 a 50); verde igual ou maior que laranja recusado
- [ ] T004 Em `src/basketiq/app_config.py`, acrescentar os quatro campos (`StrictInt` com `ge`/`le`, `Field(alias=…)`, sem *default*) e o validador `verde < laranja`; em `config/app-config.yaml`, as quatro chaves comentadas (50, 75, 2, 5); em `src/basketiq/api/routers/admin.py`, os quatro campos a `ProductConfigOut` (CFG-02). **Não** acrescentar a `PublicConfigOut`
- [ ] T005 [P] Em `tests/api/test_config.py` (`F-176`): a vista do operador (`GET /v1/admin/config`) mostra as quatro chaves (`test_admin_config_shows_every_key_to_operators_only` compara o dicionário completo); `GET /v1/config` não as mostra; ficheiro sem uma das chaves ou com valor inválido recusa arrancar

**Checkpoint**: configuração nova lida, validada e visível ao operador; T003 e T005 verdes.

---

## Phase 3: User Story 1 — Gasto do mês, meta e faixa do anel (Priority: P1) 🎯 MVP

**Goal**: `GET /v1/analytics/overview?month=` com gasto, meta, `goal_pct` e `band`.

**Independent Test**: testes de unidade das funções puras e testes de API com compras confirmadas.

- [ ] T006 [P] [US1] Escrever `tests/unit/test_overview_rules.py` (novo, `F-176`), **primeiro e a falhar**: `goal_pct` e `ring_band` nos exemplos da spec (152 € → 38 `ok`; 248 € → 62 `attention`; 352 € → 88 `high`) e nas fronteiras sobre uma meta de 40 000: 20 000 → 50 `ok`; 20 160 → 50 `ok`; 20 200 → 51 `attention`; 30 000 → 75 `attention`; 30 160 → 75 `attention`; 30 200 → 76 `high`; gasto 0 → 0 `ok`; gasto ≥ 100 % (40 000 → 100, 52 000 → 130) → `high` sem erro; sem meta → `none`; limites configurados diferentes (por exemplo 40 e 60) mudam a faixa; só aritmética de inteiros (sem `float`)
- [ ] T007 [US1] Criar `src/basketiq/domain/overview.py` com `goal_pct(spent_cents, goal_cents)` (`(spent×100 + goal//2) // goal`) e `ring_band(pct, green, orange)` (`ok` se `pct ≤ green`, `attention` se `pct ≤ orange`, senão `high`); passar T006
- [ ] T008 [P] [US1] Escrever `tests/api/test_overview.py` (novo, `F-176`): meta 40 000 e gasto 15 200/24 800/35 200 → 38/`ok`, 62/`attention`, 88/`high`; sem perfil e com perfil sem meta → `goal_cents`, `goal_pct` nulos, `band = none`, `spent_cents` na mesma; gasto ≥ 100 % sem erro; compras por confirmar e em revisão não entram; compra sem linhas e com `total_cents` entra; compra com `total_cents` nulo usa a soma líquida das linhas; compra às 00:30 de 1 de novembro (Lisboa) conta só em novembro; `month` inválido (`2026-13`, `abc`, `2026-1`) → 422 com `field_errors` em `month`; sem `month` usa o mês corrente de Lisboa; mês futuro → zeros; limites do ficheiro mudados (monkeypatch de `get_app_config`) mudam a faixa; sem consentimento da versão atual → 403 `privacy_consent_required`
- [ ] T009 [US1] Em `src/basketiq/api/schemas.py`, acrescentar `OverviewOut`, `WeekOut`, `MerchantShareOut` e `RecentReceiptOut` conforme `contracts/api.md` (cêntimos e percentagens `int`; `band` e `average_trend` como `Literal`; todos os campos obrigatórios, sem *default*)
- [ ] T010 [US1] Em `src/basketiq/services/analytics.py`, acrescentar `overview(db, user_id, month, tz_name, config)` com o gasto do mês (R1: `total_cents`, ou a soma líquida das linhas), a meta de `user_profiles.monthly_goal_cents`, `goal_pct` e `band`; mês de Lisboa com fronteiras como `_bounds`; os restantes campos ficam com valores neutros até às histórias seguintes (T015, T019)
- [ ] T011 [US1] Em `src/basketiq/api/routers/analytics.py`, acrescentar `GET /analytics/overview` (`ConsentedUser`, `month: str | None` validado com expressão `^\d{4}-(0[1-9]|1[0-2])$` e erro 422 no formato existente, omissão = mês corrente de Lisboa) e a etiqueta de métrica `("overview", "False", "False")`; passar T008

**Checkpoint**: o anel tem os quatro estados, com testes de fronteira verdes.

---

## Phase 4: User Story 2 — Média mensal e tendência (Priority: P1)

**Goal**: `monthly_average_cents`, `recent_vs_average_pct`, `average_trend`, `average_months` e `average_min_months`.

- [ ] T012 [P] [US2] Em `tests/unit/test_overview_rules.py` (`F-177`), **primeiro e a falhar**: média de 300 € e 336 € = 31 800 cêntimos; arredondamento ao cêntimo meio para cima (por exemplo 3 meses de 100, 100 e 101 cêntimos); menos de `AVERAGE_MIN_MONTHS` meses → nula; um mês sem compras entre dois meses com compras não conta como zero; o mês corrente não entra; tendência: 2 meses mais recentes face à média, com sinal (negativo = abaixo), arredondada a inteiro meio para cima; nula com menos de 3 meses; `average_trend` nos limiares (`−5`, `−4`, `0`, `5`, `6` com `on_target = 5`) e nulo quando a tendência é nula; só inteiros
- [ ] T013 [US2] Em `src/basketiq/domain/overview.py`, acrescentar `monthly_average(month_totals, min_months)`, `recent_vs_average(month_totals)` e `average_trend(pct, on_target)` conforme `research.md` R4 a R6; passar T012
- [ ] T014 [P] [US2] Em `tests/api/test_overview.py` (`F-177`): 2 meses completos anteriores de 300 € e 336 € → média 31 800 e tendência nula; 4 meses → média e tendência com sinal; mês corrente com compras não altera a média; meta nula não altera a média e a média não substitui a meta; compras por confirmar não entram; a média não depende do `month` pedido; `average_months` e `average_min_months` corretos
- [ ] T015 [US2] Em `src/basketiq/services/analytics.py`, acrescentar `month_totals(db, user_id, tz_name)` (`GROUP BY date_trunc('month', timezone(tz, purchase_at))`, só `CONFIRMED`, base do gasto de R1) e usá-la em `overview` para os campos da média; passar T014

**Checkpoint**: média, tendência e «na média» com regras testadas isoladamente e pela API.

---

## Phase 5: User Story 3 — Compras, semanas, mercados e últimas compras (Priority: P1)

- [ ] T016 [P] [US3] Em `tests/unit/test_overview_rules.py` (`F-178`), **primeiro e a falhar**: `week_blocks` dá 4 blocos em fevereiro de 2027 (28 dias), 5 em outubro de 2026 (31 dias; o último de 29 a 31) e 5 em fevereiro de 2028 (29 dias; o último de 1 dia); `shares` soma sempre 100 (maior resto; por exemplo 1/3, 1/3, 1/3 → 34, 33, 33 por ordem de gasto e nome), uma só parte → 100, lista vazia → lista vazia
- [ ] T017 [US3] Em `src/basketiq/domain/overview.py`, acrescentar `week_blocks(year, month)` e `shares(values)` (maior resto; desempate pela ordem recebida); passar T016
- [ ] T018 [P] [US3] Em `tests/api/test_overview.py` (`F-178`): 7 compras em outubro e 11 em setembro → `purchases_this_month = 7`, `purchases_last_month = 11`; `weeks[]` com a soma igual ao gasto do mês e semanas sem compras a 0; compra a 7/8 e a 28/29 do mês caem na semana certa; `by_merchant[]` em 3 mercados por gasto decrescente, com `share_pct` a somar 100 e nomes de `MERCHANT_NAMES`; loja sem leitor próprio com `merchant_name` aparece com `merchant = null` e esse nome (sem nome: «Outros»); `recent_receipts[]` com as 5 mais recentes confirmadas do mês, da mais recente, com data de Lisboa e total; conta sem nenhuma compra confirmada → `empty = true`; conta com compras só noutro mês → `empty = false` e mês a zeros; compra de balança sem linhas entra em tudo
- [ ] T019 [US3] Em `src/basketiq/services/analytics.py`, completar `overview` numa só leitura das compras confirmadas do mês (semanas, mercados, últimas 5 e contagem), a contagem do mês anterior e a existência de qualquer compra confirmada (`empty`); passar T018

**Checkpoint**: `overview` completo; contrato do Início fechado.

---

## Phase 6: User Story 4 — Totais por mês para os Talões (Priority: P2)

- [ ] T020 [P] [US4] Em `tests/api/test_overview.py` (`F-179`): `GET /v1/analytics/months` com compras em outubro (7 confirmadas, 24 800) e setembro → um item por mês, do mais recente, com `confirmed_count` e `total_cents`; meses sem confirmadas não aparecem; compras por confirmar não contam; o mês de uma compra às 00:30 de 1 de novembro é novembro e coincide com o de `overview`; sem sessão 401; sem consentimento da versão atual 403
- [ ] T021 [US4] Em `src/basketiq/api/schemas.py`, `MonthTotalOut`; em `src/basketiq/api/routers/analytics.py`, `GET /analytics/months` (`ConsentedUser`) sobre `month_totals` de T015, com a etiqueta de métrica `("months", "False", "False")`; passar T020

---

## Phase 7: User Story 5 — Produtos com preço médio e contagens (Priority: P2)

- [ ] T022 [P] [US5] Em `tests/api/test_analytics.py` (`F-180`): cada item de `GET /v1/products` traz `avg_price_cents`, `price_basis`, `purchases` e `merchants`; o preço médio é ponderado pela quantidade (por exemplo 2 kg a 4,00 € e 1 kg a 5,50 € → 4 500 cêntimos/kg) e **igual** ao `avg_price_cents` de `GET /v1/products/{id}/analytics`; produto sem peso/volume impresso fica com base `un` e sem preço por kg/l; produto sem quantidade para ponderar → `avg_price_cents` nulo; `purchases` conta compras distintas e `merchants` mercados distintos sem o desconhecido; os campos e a pesquisa atuais mantêm-se (F-20: testes existentes sem alterações)
- [ ] T023 [US5] Em `src/basketiq/services/analytics.py`, extrair de `product_analytics` a função que calcula base, quantidade na base e preço médio ponderado a partir das linhas, usá-la nos dois sítios, e alargar `search_products` (uma consulta das linhas dos produtos já escolhidos; sem consulta por produto); em `src/basketiq/api/schemas.py`, os quatro campos de `ProductSearchItem`; passar T022 e os testes existentes de produtos sem os alterar

---

## Phase 8: User Story 6 — Isolamento, privacidade e desempenho (Priority: P1)

- [ ] T024 [P] [US6] Em `tests/api/test_overview.py` (`F-181`, `PT-AC-9`, `SEC-authz`): o utilizador B só vê os seus dados em `overview`, `months` e `products`; sem sessão 401 nas três; utilizador sem perfil obtém sempre resposta válida
- [ ] T025 [P] [US6] Em `tests/api/test_overview.py` (`F-181`, `SEC-logs`): com um mês de compras sintéticas, nenhum valor de compra nem da meta aparece nos logs nem em respostas de erro (reaproveitar o método do teste `SEC-logs` existente); as etiquetas de métricas só levam booleanos
- [ ] T026 [P] [US6] Em `tests/api/test_overview.py` (`F-182`): um utilizador apagado a meio de um pedido (compras e perfil já removidos) obtém resposta válida, sem erro interno
- [ ] T027 [US6] Desempenho (`F-182`): medir `overview` com 5 000 linhas (`scripts/seed_perf.py --lines 5000 --bench`, em contentor e numa base descartável), p95 < 2 s; repetir com 50 000 e registar o resultado em `research.md` (R13); se faltar um modo de medir `overview` no `seed_perf.py`, acrescentá-lo (uma chamada ao serviço, sem lógica nova)

---

## Phase 9: Polish & cross-cutting

- [ ] T028 Regenerar `openapi.json` (`scripts/export_openapi.py`, em contentor) e confirmar que o diff só traz os endpoints e campos desta spec
- [ ] T029 Correr a rastreabilidade com as specs montadas: F-176 a F-182 com teste e sem ids citados sem teste (a tabela da `spec.md` tem de os listar, ver T034)
- [ ] T030 Correr `ruff format --check`, `ruff check`, `mypy`, `bandit`, `pip-audit`, `uv lock --check` e a bateria completa; registar o resultado
- [ ] T031 Confirmar que o golden sintético (`--baseline tests/golden/baseline.json`) não mudou (esta spec não toca no *parser*)
- [ ] T032 Atualizar o `README.md` do backend: as quatro chaves de configuração na tabela de chaves e os endpoints `overview`, `months` e o alargamento de `products`
- [ ] T033 Correr `/simplify` e repetir T030; mostrar o `git diff --stat`
- [ ] T034 Acrescentar à `spec.md` a tabela «Identificadores de teste» (F-176 a F-182 com os critérios de cada história, como na spec 013) e as decisões Q1 a Q10 confirmadas pela Liz; resolver as marcas `[NEEDS CLARIFICATION]` com a resposta final
- [ ] T035 (manual, Liz) Avisar o frontend: depois de T028, a spec 011 corre `corepack pnpm@9 gen:api` e a spec 012 passa a usar `GET /v1/analytics/months` e os campos novos de `products`; antes de reconstruir a imagem da API, atualizar o `config/app-config.yaml` de qualquer ambiente com cópia própria (sem as quatro chaves nem a API nem o *worker* arrancam)

---

## Dependencies & Execution Order

- Phase 1 → Phase 2 → histórias → Polish. T035 é manual e fica fora do «pronto».
- **Ordem das histórias:** US1 → US2 → US3 (partilham `services/analytics.py`, `domain/overview.py`, `tests/api/test_overview.py`: **não** correr em paralelo as tarefas que editam o mesmo ficheiro). US4 depende de T015. US5 é independente das outras e pode ser feita em paralelo (outros ficheiros de teste e `search_products`, mas o mesmo `services/analytics.py`: coordenar). US6 fecha no fim.
- Dentro de cada história, escrever o teste (T006, T008, T012, T014, T016, T018, T020, T022, T024 a T026) antes da implementação e vê-lo falhar.
- T009 antes de T010; T010 antes de T011; T015 antes de T019 e de T021.
- T028 depende de todos os endpoints (T011, T021, T023); T029 depende de T034.

## Parallel opportunities

- Fase 2: T003 ‖ T005 (testes de ficheiros diferentes), depois T004.
- Testes de unidade em `tests/unit/test_overview_rules.py` (T006, T012, T016) são do mesmo ficheiro: sequenciais.
- US5 (T022, T023) em paralelo com US1 a US3 por outra pessoa, se for o caso.
- T024 ‖ T025 ‖ T026 (mesmo ficheiro de teste: acrescentos independentes, juntar no fim).

## Implementation strategy

- **MVP**: Phases 1 a 3 (US1) dão o anel com os quatro estados e os testes de fronteira. Para o Início completo do frontend 011 é preciso US1 a US3; a US4 serve a lista de Talões (012) e a US5 os Produtos (012).
- Entrega incremental: validar cada checkpoint com a bateria antes de seguir. Contrato primeiro: só depois de T028 o frontend corre `pnpm gen:api`.
- Nada de migrações: nenhuma aplicação à base de desenvolvimento nesta spec.
- Fora desta lista: o `price-gaps` e a lista inteligente (spec 015); linguagem do ecrã (frontend 011).
