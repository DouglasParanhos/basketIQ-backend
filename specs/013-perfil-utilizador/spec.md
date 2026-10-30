# Feature Specification: Perfil do utilizador, mercados e gestão de originais em bloco

**Feature Branch**: `013-perfil-utilizador`

**Created**: 2026-10-30

**Status**: Draft (Fase 0 do plano UX v2: só histórias e critérios de aceitação; sem requisitos técnicos, plano nem tarefas). Clarificada em 2026-10-30.

**Input**: Especificação Funcional v1.4 RF-13, RF-14, RF-12; `Documentation/Super Mercado_—_Plano_de_Implementação_UX_v2.md` (Fases 2 e 6, lacunas de backend 1, 6 e 7). Frontend: `../basketIQ-frontend/specs/010-onboarding`, `014-definicoes-v2`.

**Convenção de rastreabilidade**: cada critério cita o RF da Especificação Funcional. O identificador F-xx do Plano de Testes atribui-se na Fase 8, na faixa da experiência v2 (§12.4 do Funcional). Teste de cada critério: por criar.

## Clarifications

### Session 2026-10-30

Decisões da Liz (itens 4, 7, 10 e 11 da lista de 2026-10-30) e respostas recomendadas pela skill; todas confirmadas como definitivas pela Liz em 2026-10-30.

- Q: O perfil guarda-se com `PUT` ou `PATCH`? → A: **`PATCH`, campo omitido = manter; `null` explícito = apagar** (decisão da Liz).
- Q: Que valores têm os objetivos? → A: `finances`, `shopping_list` e `savings`. «Todas as anteriores» não se guarda: é a escolha dos três.
- Q: Como se marca o onboarding como concluído? → A: campo `onboarding_completed: true` no `PATCH`; a data só se preenche na primeira vez e mantém-se.
- Q: Quem já usa a aplicação vê o onboarding? → A: sim, uma vez, e pode saltá-lo.
- Q: Que mercados devolve a lista pública? → A: os 7 com parser próprio (sem o estado «desconhecido»). Intermarché, Coviran e E.Leclerc saem do onboarding até terem parser.
- Q: Código e estado quando falta o consentimento da política atual? → A: 403 com o código `privacy_consent_required`.
- Q: De quem é o critério «apagar tudo o que deriva das compras» (RF-12)? → A: da **spec 010 (passo C)**. Esta spec só garante que o perfil desaparece com a conta; o teste F-23 de ponta a ponta cobre as duas.
- Q: O resumo dos originais conta ficheiros ou compras? → A: ficheiros guardados (cada foto ou PDF).
- Q: Apagar todos inclui compras ainda por confirmar? → A: não; só os originais de compras confirmadas, para não impedir o reprocessamento (`original_deleted`).
- Q: Como se expõe a opção da lista? → A: `GET /v1/config` devolve `smart_list_enabled` (chave `SMART_LIST_ENABLED`, obrigatória, desligada por omissão). Enquanto este campo não existir, o frontend trata-o como desligado.
- Q: Nome e caminho dos originais em bloco? → A: `GET /v1/receipts/stored-files` (quantos ficheiros) e `DELETE /v1/receipts/stored-files` (apagar todos), declarados **antes** de `/{receipt_id}` e com nome distinto de `/{id}/original` (decisão da Liz).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Guardar e ler o perfil (Priority: P1)

O utilizador autenticado guarda o número de pessoas, os mercados habituais, os objetivos e a meta pessoal mensal (opcional) e volta a lê-los. Guarda-se só o que o utilizador confirmou.

**Why this priority**: o onboarding, o Início e as Definições dependem do perfil.

**Independent Test**: chamadas `GET` e `PATCH /v1/me/profile` com um utilizador de teste.

**Acceptance Scenarios**:

1. **Given** uma conta nova, **When** pede o perfil, **Then** recebe um perfil vazio (pessoas, meta e conclusão do onboarding nulos; mercados e objetivos vazios) e não um erro. — RF-13
2. **Given** um perfil válido (2 pessoas, 3 mercados, 2 objetivos, meta de 400 €), **When** o guarda e o volta a pedir, **Then** recebe os mesmos valores, com a meta em cêntimos (40 000). — RF-13
3. **Given** um perfil guardado, **When** guarda outra vez os mesmos dados, **Then** o resultado é igual (idempotente). — RF-13
4. **Given** um perfil com meta de 400 €, **When** envia um `PATCH` sem o campo da meta, **Then** a meta mantém-se. — RF-13, RF-14
5. **Given** um perfil com meta de 400 €, **When** envia um `PATCH` com a meta a `null`, **Then** a meta fica por definir. — RF-13
6. **Given** um `PATCH` vazio, **When** é enviado, **Then** nada muda e a resposta é bem-sucedida. — RF-14
7. **Given** dois utilizadores, **When** cada um pede o perfil, **Then** cada um vê só o seu. — RF-13, SEC-authz
8. **Given** um pedido sem sessão, **When** chama o perfil, **Then** recebe 401. — RF-13, SEC-authz

---

### User Story 2 - Validar os valores (Priority: P1)

Valores fora dos limites são recusados com mensagem acionável; nunca se grava um valor por omissão.

**Acceptance Scenarios**:

1. **Given** pessoas = 0 ou 13, **When** guarda, **Then** é recusado; 1 e 12 são aceites. — RF-13
2. **Given** meta = 4 999 ou 80 001 cêntimos, **When** guarda, **Then** é recusado; 5 000 e 80 000 são aceites; nulo é aceite. — RF-13
3. **Given** uma meta com casas decimais ou como texto, **When** guarda, **Then** é recusada (dinheiro só em cêntimos inteiros). — RF-13, constituição II
4. **Given** um mercado fora da lista suportada, **When** guarda, **Then** é recusado; um mercado repetido conta uma vez. — RF-13
5. **Given** um objetivo fora de `finances`, `shopping_list` e `savings`, **When** guarda, **Then** é recusado. — RF-13
6. **Given** um pedido inválido, **When** é recusado, **Then** a mensagem diz o limite e nada fica guardado, nem os campos válidos do mesmo pedido. — RF-13

---

### User Story 3 - Marcar o onboarding como concluído (Priority: P1)

Concluir ou saltar o onboarding fica registado para o utilizador não voltar a ser interrompido.

**Acceptance Scenarios**:

1. **Given** um perfil sem conclusão, **When** o `PATCH` traz `onboarding_completed: true`, **Then** `onboarding_completed_at` fica preenchido e é devolvido. — RF-14
2. **Given** um utilizador que saltou todos os passos, **When** o `PATCH` traz só `onboarding_completed: true`, **Then** a conclusão fica registada e nenhum valor de perfil é gravado. — RF-14, constituição III
3. **Given** um onboarding já concluído, **When** o utilizador refaz o percurso e o `PATCH` traz de novo `onboarding_completed: true`, **Then** a data original mantém-se e os dados guardados não se perdem. — RF-14
4. **Given** uma conta existente com compras e sem perfil, **When** pede o perfil, **Then** `onboarding_completed_at` é nulo e o frontend mostra o onboarding uma vez. — RF-14
5. **Given** `onboarding_completed: false`, **When** é enviado, **Then** é recusado (a conclusão não se desfaz). — RF-14

---

### User Story 4 - Lista de mercados suportados (Priority: P1)

O utilizador autenticado obtém a lista de mercados suportados para o onboarding, as Definições, o ecrã Adicionar e os filtros.

**Acceptance Scenarios**:

1. **Given** um utilizador autenticado sem papel de operador, **When** pede `GET /v1/merchants`, **Then** recebe os 7 mercados suportados, incluindo El Corte Inglés, com um identificador estável e o nome para mostrar, e sem o estado «desconhecido». — RF-13
2. **Given** um pedido sem sessão, **When** chama a lista, **Then** recebe 401. — RF-13
3. **Given** a lista pública, **When** é comparada com a de operador, **Then** não expõe parâmetros de configuração nem de parsing. — RF-13, RF-admin
4. **Given** um mercado que sai da lista, **When** um perfil o guardava, **Then** o perfil continua a ler-se sem erro. — RF-13

---

### User Story 5 - Nova versão da política e novo consentimento (Priority: P1)

Os dados novos (pessoas, mercados, objetivos, meta) só se recolhem depois de aceite a nova versão da política.

**Acceptance Scenarios**:

1. **Given** a versão da política incrementada, **When** `GET /v1/me` é chamado por quem aceitou a anterior, **Then** a versão atual e a aceite diferem e o consentimento fica pendente. — RF-13, RF-12
2. **Given** consentimento pendente, **When** guarda o perfil, **Then** recebe 403 com o código `privacy_consent_required` e nada é gravado. — RF-13, RF-12
3. **Given** o consentimento da versão atual dado, **When** guarda o perfil, **Then** é aceite. — RF-13
4. **Given** o consentimento, **When** é dado, **Then** fica auditado sem dados pessoais. — RF-12, constituição IV

---

### User Story 6 - Eliminar a conta apaga o perfil (Priority: P1)

**Acceptance Scenarios**:

1. **Given** uma conta com perfil, **When** pede a eliminação, **Then** o perfil desaparece com a conta e a auditoria fica pseudonimizada. — F-23, RF-12, RF-13
2. **Given** a eliminação, **When** termina, **Then** a tabela do perfil não mantém linhas ligadas ao utilizador (teste de ponta a ponta). — F-23, RF-12
3. **Given** o requisito de apagar tudo o que deriva das compras (produtos, aliases, correções, tamanhos), **When** é verificado, **Then** o critério pertence à spec 010 (passo C) e o mesmo teste de ponta a ponta cobre as duas specs. — F-23, RF-12

---

### User Story 7 - Ver e apagar todos os originais de uma vez (Priority: P2)

*Acrescentada nesta Fase 0: o plano (Fase 6, lacuna 6) pede o comportamento mas não indica spec de backend.*

**Acceptance Scenarios**:

1. **Given** originais guardados, **When** pede `GET /v1/receipts/stored-files`, **Then** recebe quantos ficheiros (fotos e PDF) estão guardados, 0 se nenhum. — RF-12
2. **Given** originais de compras confirmadas, **When** pede `DELETE /v1/receipts/stored-files`, **Then** os objetos são removidos, as compras e os dados estruturados mantêm-se e fica auditado com motivo próprio. — RF-12, F-22a
3. **Given** não haver originais, **When** pede para os apagar, **Then** o resultado é bem-sucedido com 0 removidos. — RF-12
4. **Given** compras ainda por confirmar, **When** apaga todos, **Then** os originais dessas compras não são tocados nem contados. — RF-12
5. **Given** o utilizador B, **When** apaga todos, **Then** só os seus originais são afetados. — RF-12, PT-AC-9
6. **Given** os caminhos `.../stored-files`, **When** são chamados, **Then** não são interpretados como identificador de compra (declarados antes de `/{receipt_id}`) e não se confundem com `/{id}/original`. — RF-12

---

### User Story 8 - A opção da lista inteligente é visível ao cliente (Priority: P1)

O frontend decide mostrar o destino **Lista** a partir da configuração pública (`GET /v1/config`).

**Acceptance Scenarios**:

1. **Given** a opção da lista desligada (omissão), **When** o cliente lê a configuração pública, **Then** recebe `smart_list_enabled` falso. — RF-16
2. **Given** a opção ligada, **When** o cliente lê a configuração, **Then** recebe verdadeiro, sem expor outras chaves internas. — RF-16
3. **Given** o ficheiro de configuração sem `SMART_LIST_ENABLED` ou com valor inválido, **When** a aplicação arranca, **Then** recusa arrancar com uma mensagem clara (padrão das 2 chaves atuais, sem *default* no código). — RF-16

### Edge Cases

- Perfil de utilizador eliminado durante um pedido em curso: resposta sem erro interno.
- Lista de objetivos vazia é válida.
- Nenhum registo de log contém pessoas, mercados ou meta (constituição IV).

## Requirements *(mandatory)*

*Por escrever em `/speckit-plan`; a Fase 0 cobre só histórias e critérios.*

## Success Criteria *(mandatory)*

*Por escrever. O plano pede, no fim da Fase 2: conta nova passa pelo onboarding, refazer o fluxo não perde dados, testes de contrato verdes.*

## Assumptions

- Dependência: a migração de `user_profiles` e a regeneração do `openapi.json` seguem o fluxo «contrato primeiro» do plano.
- A spec 010 está reservada ao passo C (grupos de produto). Ao criar essa spec com o `create-new-feature.sh`, usar `--number 10`; sem isso o spec-kit atribui o número seguinte ao maior existente.
