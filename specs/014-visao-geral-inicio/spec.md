# Feature Specification: Visão geral do Início, totais por mês e produtos enriquecidos

**Feature Branch**: `014-visao-geral-inicio`

**Created**: 2026-10-30

**Status**: Draft (Fase 0 do plano UX v2: só histórias e critérios de aceitação; sem requisitos técnicos, plano nem tarefas)

**Input**: Especificação Funcional v1.4 RF-15, RF-09, RF-10, §12.4; `Documentation/Super Mercado_—_Plano_de_Implementação_UX_v2.md` (Fases 3 e 4, lacunas de backend 2 e 3). Frontend: `../basketIQ-frontend/specs/011-inicio`, `012-taloes`.

**Convenção de rastreabilidade**: cada critério cita o RF da Especificação Funcional. O identificador F-xx do Plano de Testes atribui-se na Fase 8, na faixa da experiência v2 (§12.4 do Funcional). Teste de cada critério: por criar. O plano manda escrever **primeiro** os testes de unidade das faixas e da média (regra de negócio).

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
5. **Given** gasto igual ou superior a 100 % da meta, **When** pede o mês, **Then** `goal_pct` ≥ 100 e `band` = `high`, sem erro. — RF-15 — [NEEDS CLARIFICATION: o texto «saldo da meta» com saldo negativo; sem linguagem de perda]
6. **Given** `goal_pct` calculado como a percentagem arredondada ao inteiro (meio para cima) com aritmética de inteiros, **When** a faixa é decidida, **Then** calcula-se sobre esse `goal_pct`, o mesmo que o ecrã mostra: verde até 50 % inclusive, laranja acima de 50 % até 75 % inclusive, vermelho acima de 75 %. — RF-15, constituição II
7. **Given** meta de 400 € (40 000 cêntimos) e gasto de 20 000 cêntimos (50 %), **When** pede o mês, **Then** `goal_pct` = 50 e `band` = `ok`. — RF-15 (teste de fronteira)
8. **Given** meta de 40 000 cêntimos e gasto de 20 160 (50,4 %), **When** pede o mês, **Then** `goal_pct` = 50 e `band` = `ok`. — RF-15 (teste de fronteira)
9. **Given** meta de 40 000 cêntimos e gasto de 20 200 (50,5 %), **When** pede o mês, **Then** `goal_pct` = 51 e `band` = `attention`. — RF-15 (teste de fronteira)
10. **Given** meta de 40 000 cêntimos e gasto de 30 000 (75 %), **When** pede o mês, **Then** `goal_pct` = 75 e `band` = `attention`. — RF-15 (teste de fronteira)
11. **Given** meta de 40 000 cêntimos e gasto de 30 160 (75,4 %), **When** pede o mês, **Then** `goal_pct` = 75 e `band` = `attention`. — RF-15 (teste de fronteira)
12. **Given** meta de 40 000 cêntimos e gasto de 30 200 (75,5 %), **When** pede o mês, **Then** `goal_pct` = 76 e `band` = `high`. — RF-15 (teste de fronteira)
13. **Given** limites configurados de forma diferente de 50 e 75 (chaves `GOAL_RING_GREEN_UP_TO_PCT` e `GOAL_RING_ORANGE_UP_TO_PCT`, «até X inclusive»), **When** pede o mês, **Then** a faixa respeita a configuração e não valores fixos no código. — RF-15
14. **Given** compras por confirmar ou em revisão, **When** pede o mês, **Then** não entram no gasto. — RF-15, §12.4
15. **Given** uma compra «só com total», **When** pede o mês, **Then** entra no gasto do mês. — RF-15, §12.0 — [NEEDS CLARIFICATION: confirmar que entra no gasto (só fica fora dos preços por produto)]
16. **Given** uma compra às 00:30 de 1 de novembro (hora de Lisboa), **When** pede outubro e novembro, **Then** conta só em novembro. — RF-15, F-18
17. **Given** `month` inválido, **When** pede, **Then** recebe erro de validação; sem `month`, usa o mês corrente de Lisboa. — RF-15

---

### User Story 2 - Média mensal e tendência (Priority: P1)

**Acceptance Scenarios**:

1. **Given** menos meses com compras confirmadas do que o mínimo configurado (2 por omissão), **When** pede a visão geral, **Then** `monthly_average_cents` e `recent_vs_average_pct` são nulos. — RF-15
2. **Given** 2 meses confirmados de 300 € e 336 €, **When** pede, **Then** a média é 318 € em cêntimos inteiros, arredondada ao cêntimo (meio para cima). — RF-15, constituição II
3. **Given** um mês sem compras entre dois meses com compras, **When** calcula, **Then** o mês vazio não conta como zero. — RF-15 — [NEEDS CLARIFICATION: confirmar]
4. **Given** o mês corrente ainda incompleto, **When** calcula a média, **Then** a regra decidida é aplicada e testada. — RF-15 — [NEEDS CLARIFICATION: o mês corrente entra na média? o plano manda testar «meses incompletos»]
5. **Given** média e meses recentes, **When** pede, **Then** `recent_vs_average_pct` compara os últimos 2 meses com a média (por exemplo 7 % abaixo). — RF-15 — [NEEDS CLARIFICATION: definição exata (os 2 meses incluem o corrente?) e o limiar de «na média», que o protótipo usa sem o definir]
6. **Given** meta nula, **When** pede, **Then** a média calcula-se na mesma e a meta não a substitui nem é substituída por ela. — RF-13, RF-15
7. **Given** compras por confirmar, **When** calcula, **Then** não entram. — RF-15, §12.4

---

### User Story 3 - Compras, semanas, mercados e últimas compras (Priority: P1)

**Acceptance Scenarios**:

1. **Given** 7 compras confirmadas em outubro e 11 em setembro, **When** pede outubro, **Then** `purchases_this_month` = 7 e `purchases_last_month` = 11. — RF-15
2. **Given** um mês, **When** pede, **Then** `weeks[]` traz o gasto por semana, soma igual ao gasto do mês e semanas sem compras a zero. — RF-15 — [NEEDS CLARIFICATION: definição de semana (blocos de 7 dias desde o dia 1 ou semana ISO) e o que fazer com a quinta semana parcial]
3. **Given** compras em 3 mercados, **When** pede, **Then** `by_merchant[]` vem ordenado por gasto decrescente, com valor e percentagem, e a soma das percentagens é 100. — RF-15 — [NEEDS CLARIFICATION: regra de arredondamento para a soma ser 100]
4. **Given** compras recentes, **When** pede, **Then** `recent_receipts[]` traz as mais recentes com mercado, data, total e estado. — RF-15 — [NEEDS CLARIFICATION: quantas, e se incluem as que requerem ação ou estão em processamento]
5. **Given** uma conta sem nenhuma compra confirmada, **When** pede, **Then** `empty` = true. — RF-15
6. **Given** uma conta com compras mas sem nenhuma no mês pedido, **When** pede, **Then** `empty` = false e o mês vem com zeros. — RF-15 — [NEEDS CLARIFICATION: a distinção «sem dados» e «mês sem compras»]

---

### User Story 4 - Totais por mês para a lista de Talões (Priority: P2)

*Acrescentada nesta Fase 0: o plano (Fase 4, lacuna 3) pede o endpoint mas não lhe dá nome nem spec.*

**Acceptance Scenarios**:

1. **Given** compras confirmadas em outubro e setembro, **When** pede os totais, **Then** vem, por mês, o número de confirmadas e o total em cêntimos (por exemplo outubro: 7 e 24 800). — §8 — [NEEDS CLARIFICATION: nome e forma do endpoint]
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

*Por escrever em `/speckit-clarify` e `/speckit-plan`; a Fase 0 cobre só histórias e critérios.*

## Success Criteria *(mandatory)*

*Por escrever. O plano pede: os 4 estados do anel e os estados vazios com testes; p95 de `overview` < 2 s com 5 000 linhas.*

## Assumptions

- Depende da spec 013 (meta no perfil) e do `analytics.py` existente (reaproveitar).
- O passo C usa os números 010 e seguintes; esta spec não depende dele.
