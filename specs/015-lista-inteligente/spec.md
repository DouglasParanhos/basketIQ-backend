# Feature Specification: Lista inteligente e comparação entre mercados

**Feature Branch**: `015-lista-inteligente`

**Created**: 2026-10-30

**Status**: Draft (Fase 0 do plano UX v2: só histórias e critérios de aceitação; sem requisitos técnicos, plano nem tarefas). **Só se constrói depois do passo C.**

**Input**: Especificação Funcional v1.4 RF-16, RF-15, §12.1, §12.3, §12.4; `Documentation/Super Mercado_—_Plano_de_Implementação_UX_v2.md` (Fase 7 e secção 6.7); `Documentation/RASCUNHO_decisoes_spec010_2026-10-30.md` (D1 a D12). Frontend: `../basketIQ-frontend/specs/015-lista-inteligente`.

**Convenção de rastreabilidade**: cada critério cita o RF da Especificação Funcional. O identificador F-xx do Plano de Testes atribui-se na Fase 8, na faixa do passo D (§12.4 do Funcional). Teste de cada critério: por criar. **Todos os testes usam dados sintéticos.**

**Âmbito**: a lista fica atrás de uma opção de configuração desligada por omissão. Preços de terceiros e o plano Pro ficam fora até haver estudo legal (§12.3): não há valor `public` nem texto Pro.

**Pré-condições**: passo C concluído (catálogo de grupos de produto, tamanho da embalagem, média D11 e comparação D12) e spike de dados feito (US0). A lista e a comparação trabalham sobre **grupos de produto**, não sobre produtos soltos.

**Regra de comparação (D11 e D12, prevalece sobre qualquer outro texto):** janela de 90 dias, só compras confirmadas com tamanho conhecido. Só se diz que um mercado fica «abaixo» ou «acima» do que o utilizador costuma pagar se houver **2 mercados com pelo menos 3 compras cada**, a diferença for pelo menos **15 % da média do grupo do utilizador** e a diferença × a quantidade média por compra for pelo menos **0,20 €**. Caso contrário, «sem diferença clara». Com menos de 3 compras, «faltam X compras para comparar». **Não há ranking global de cadeias.** Os valores 90 dias, 3, 15 % e 0,20 € são as constantes de configuração do passo C, não novas.

## User Scenarios & Testing *(mandatory)*

### User Story 0 - Spike de dados antes de construir (Priority: P1, porta de entrada)

Antes de qualquer interface, corre-se o algoritmo sobre os talões reais de `../Receipts` (só leitura) e confirma-se que o agrupamento e as unidades dão resultados com sentido. Este spike sobe para antes de qualquer fase que dependa do agrupamento de produtos.

**Acceptance Scenarios**:

1. **Given** os talões reais, **When** o spike corre, **Then** produz um relatório **agregado** que diz onde o agrupamento falha (quantos produtos casam entre mercados, quantos ficam por agrupar e porquê). — RF-16
2. **Given** o relatório, **When** é guardado, **Then** não contém NIF, números de cartão, nomes de pessoas, texto de talões, preços nem datas, e nenhum dado real entra no repositório. — F-49, constituição IV
3. **Given** os testes do algoritmo, **When** são escritos, **Then** usam só dados sintéticos construídos para o teste; nenhum talão real, nem anonimizado, entra no repositório. — constituição IV, F-49
4. **Given** o resultado do spike, **When** a correspondência entre mercados é fraca, **Then** a decisão de avançar, limitar ou adiar fica registada antes de implementar. — RF-16

---

### User Story 1 - Gerar a lista por modo (Priority: P1)

`POST /v1/lists` com `mode` (`today`, `second_purchase`, `missing`).

**Acceptance Scenarios**:

1. **Given** histórico suficiente, **When** pede o modo `today`, **Then** os itens e quantidades sugeridas vêm do ritmo de compra do utilizador (frequência e unidades por mês). — RF-16
2. **Given** o modo `second_purchase`, **When** pede, **Then** só vêm os itens que faltam na segunda quinzena do mês. — RF-16 — [NEEDS CLARIFICATION: definição de «falta» e de «segunda quinzena»]
3. **Given** o modo `missing`, **When** pede, **Then** só vêm os itens que ainda não entraram no mês corrente. — RF-16
4. **Given** um item comprado por peso, **When** a quantidade é sugerida, **Then** vem na unidade do grupo (kg, l ou unidades) com decimais exatos. — RF-16, constituição II
5. **Given** talões por confirmar, **When** gera, **Then** não entram. — RF-16, §12.4
6. **Given** um modo inválido, **When** pede, **Then** recebe erro de validação. — RF-16
7. **Given** o mesmo pedido repetido no mesmo dia, **When** gera, **Then** o resultado é o definido. — RF-16 — [NEEDS CLARIFICATION: cria outra lista ou devolve a aberta]
8. **Given** produtos «por agrupar» ou sem tamanho, **When** gera, **Then** ficam de fora e a resposta diz quantos. — §12.4, constituição III

---

### User Story 2 - Um só mercado (Priority: P1)

**Acceptance Scenarios**:

1. **Given** `strategy` = `single` e um mercado escolhido, **When** gera, **Then** a resposta traz o **total esperado** da lista nesse mercado e o esperado por item. — RF-16
2. **Given** itens em que a regra de comparação se cumpre, **When** gera, **Then** cada um traz a comparação com o outro mercado (preço médio que o utilizador pagou em cada um, janela e número de compras de cada um, e a frase «a diferença é de cerca de 0,50 € numa compra típica»). — RF-16
3. **Given** itens em que a regra não se cumpre, **When** gera, **Then** trazem «sem diferença clara» ou «faltam X compras para comparar», e nunca uma conclusão. — RF-16, constituição III
4. **Given** a resposta, **When** é lida, **Then** não traz mercados ordenados por total nem um «mais barato». — RF-16, D12
5. **Given** um item sem compras nesse mercado, **When** calcula o esperado, **Then** usa a média geral do grupo e marca-o com `~`. — RF-16 — [NEEDS CLARIFICATION: `~` mostra uma estimativa; o rascunho do passo C proíbe números estimados no relatório]
6. **Given** um mercado sem nenhuma compra do utilizador, **When** o pede, **Then** a resposta diz que não há histórico nesse mercado em vez de estimar um total. — RF-16, constituição III
7. **Given** o mercado escolhido, **When** não está na lista suportada, **Then** é recusado. — RF-16, RF-13

---

### User Story 3 - Múltiplos mercados (Priority: P1)

**Acceptance Scenarios**:

1. **Given** `strategy` = `multi` e `merchants[]` com pelo menos 2 mercados, **When** gera, **Then** um item só é atribuído a um mercado se a regra de comparação se cumprir para esse item; a atribuição é «pelo que costuma pagar». — RF-16
2. **Given** um item sem diferença clara entre mercados, **When** gera, **Then** vai para o grupo «Em qualquer mercado». — RF-16
3. **Given** um item com menos de 3 compras num dos mercados, **When** gera, **Then** vai para «Em qualquer mercado» com «faltam X compras para comparar». — RF-16
4. **Given** a lista distribuída, **When** devolve, **Then** traz o total esperado por mercado, o do grupo «Em qualquer mercado» e o total da lista, **sem** comparação com o «melhor mercado único» nem ordenação de mercados. — RF-16, D12
5. **Given** `merchants[]` com menos de 2 mercados, **When** pede `multi`, **Then** é recusado. — RF-16
6. **Given** mercados fora da lista suportada, **When** pede, **Then** é recusado. — RF-16, RF-13
7. **Given** as diferenças, **When** são calculadas, **Then** usam inteiros exatos e o arredondamento ao cêntimo só ao mostrar. — RF-16, constituição II

---

### User Story 4 - Valores e fonte (Priority: P1)

**Acceptance Scenarios**:

1. **Given** um item e um mercado com compras, **When** devolve, **Then** `expected` é a média ponderada paga nesse mercado na janela e `last` o preço do talão mais recente. — RF-16
2. **Given** qualquer valor, **When** devolve, **Then** `source` = `own_receipts` e não existe outro valor possível. — RF-16, §12.3
3. **Given** a resposta, **When** é lida, **Then** traz a janela em dias e, por mercado, o número de compras usadas. — RF-16, D11
4. **Given** linhas sem unidade, sem tamanho ou com agrupamento incerto, **When** calcula, **Then** ficam de fora e nunca se inventa um valor. — §12.4, constituição III
5. **Given** compras «só com total», **When** calcula, **Then** não entram. — §12.0 — [NEEDS CLARIFICATION: confirmar]

---

### User Story 5 - Histórico insuficiente (Priority: P1)

**Acceptance Scenarios**:

1. **Given** menos meses do que o mínimo configurado (`SMART_LIST_MIN_MONTHS`), **When** pede a lista, **Then** recebe `insufficient_history` com os meses que tem e o mínimo configurado, e nenhuma lista. — RF-16, constituição III
2. **Given** o mínimo, **When** é atingido, **Then** a lista passa a gerar-se sem outra ação. — RF-16
3. **Given** o progresso mostrado, **When** o mínimo configurado é 2, **Then** o denominador devolvido é 2 («0/2 meses»); o ecrã nunca usa outro número. — RF-16

---

### User Story 6 - Caixinhas e ligação ao talão (Priority: P2)

`GET /v1/lists/{id}`, `PATCH /v1/lists/{id}/items/{item}`, `DELETE /v1/lists/{id}`.

**Acceptance Scenarios**:

1. **Given** uma lista, **When** o utilizador marca um item, **Then** o estado persiste e volta a ler-se igual. — RF-16
2. **Given** uma lista aberta de hoje, **When** o utilizador confirma um talão do mesmo dia, **Then** os itens com linhas correspondentes ficam marcados como comprados com o preço pago. — RF-16
3. **Given** linhas do talão sem item na lista, **When** liga, **Then** são ignoradas sem erro. — RF-16
4. **Given** um talão de outro dia, **When** é confirmado, **Then** não toca na lista. — RF-16
5. **Given** a ligação, **When** o talão é eliminado, **Then** o item volta a por marcar. — RF-16, F-22 — [NEEDS CLARIFICATION: confirmar]
6. **Given** várias listas abertas no mesmo dia, **When** liga, **Then** a regra decidida escolhe uma. — RF-16 — [NEEDS CLARIFICATION]
7. **Given** a lista, **When** o utilizador a elimina, **Then** desaparece com os seus itens. — RF-16, RF-12
8. **Given** uma lista ou item de outro utilizador, **When** é lido, alterado ou eliminado, **Then** responde 404. — PT-AC-9, SEC-authz

---

### User Story 7 - Opção desligada, privacidade e desempenho (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a opção desligada, **When** qualquer endpoint da lista é chamado, **Then** não gera nem devolve listas. — RF-16 — [NEEDS CLARIFICATION: 404 ou 403]
2. **Given** a eliminação da conta, **When** termina, **Then** listas e itens desaparecem (teste de ponta a ponta). — F-23, RF-12
3. **Given** a lista, **When** é registada em logs ou auditoria, **Then** não leva nomes de produtos nem valores. — SEC-logs, constituição IV
4. **Given** a nova finalidade (lista), **When** o utilizador não aceitou a política atual, **Then** a geração é recusada com o mesmo código da spec 013 (`privacy_consent_required`). — RF-16, RF-12
5. **Given** 5 000 linhas, **When** gera, **Then** a resposta cumpre a meta de desempenho decidida. — §10 — [NEEDS CLARIFICATION: o plano não dá meta para este endpoint]
6. **Given** a opção ligada e histórico suficiente, **When** corre o algoritmo, **Then** os testes de unidade cobrem os três modos, as duas estratégias, o `~`, o «sem diferença clara» e o «faltam X compras», com dados sintéticos. — RF-16, constituição VI

---

### User Story 8 - Comparação entre mercados para o Início (Priority: P2)

*Substitui o `price-gaps` da Fase 3: o cartão «mesmo produto, mercados diferentes» só se constrói depois do passo C.*

**Acceptance Scenarios**:

1. **Given** um grupo de produto comprado em 2 mercados, cada um com pelo menos 3 compras na janela, **When** pede a comparação, **Then** vem o preço médio que o utilizador pagou em cada mercado, o número de compras de cada um, a janela e a base (kg, l ou unidade). — RF-15, D12
2. **Given** a diferença abaixo de 15 % da média do grupo ou abaixo de 0,20 € na compra típica, **When** pede, **Then** o grupo vem com «sem diferença clara». — RF-15, D12
3. **Given** um grupo com menos de 3 compras num dos mercados, **When** pede, **Then** vem «faltam X compras para comparar» e nenhum valor comparado. — RF-15, D12
4. **Given** um grupo comprado só num mercado, **When** pede, **Then** vem a média desse mercado e «só comprou em <mercado> neste período; sem outro para comparar». — RF-15, D12
5. **Given** o resultado, **When** é lido, **Then** não traz ordenação global de mercados nem a palavra «mais barato»; descreve preços pagos pelo utilizador e nunca o preço atual de um mercado. — RF-15, D12, §3
6. **Given** `limit`, **When** há mais candidatos, **Then** vêm os primeiros pela ordem decidida. — RF-15 — [NEEDS CLARIFICATION: critério de ordenação dos grupos]
7. **Given** compras por confirmar ou «só com total», **When** calcula, **Then** não entram. — RF-15, §12.0, §12.4
8. **Given** 5 000 linhas, **When** mede, **Then** p95 < 2 s. — §10

### Edge Cases

- Utilizador com um só mercado: `multi` não faz sentido e `single` mostra um mercado sem comparação.
- Item raro (uma compra): sem ritmo; regra a decidir.
- Mudança de preço grande entre a última compra e a média: ambos os valores aparecem, nunca só um.

## Requirements *(mandatory)*

*Por escrever em `/speckit-clarify` e `/speckit-plan`; a Fase 0 cobre só histórias e critérios.*

## Success Criteria *(mandatory)*

*Por escrever. O plano pede: testes de unidade do algoritmo com dados sintéticos, testes de UI dos dois modos, texto revisto e axe verde.*

## Assumptions

- Depende do passo C (spec 010), da spec 014 (histórico confirmado) e de `analytics/consumption`.
- A Especificação Funcional §12.3 aprova o RF-16 como nota de âmbito do passo D, depois do passo C.
