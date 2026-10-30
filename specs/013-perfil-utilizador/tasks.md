---

description: "Tarefas da spec 013: perfil, mercados e originais em bloco"
---

# Tasks: Perfil do utilizador, mercados e gestão de originais em bloco

**Input**: `/specs/013-perfil-utilizador/` — [spec.md](./spec.md), [plan.md](./plan.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/api.md](./contracts/api.md), [quickstart.md](./quickstart.md)

**Tests**: pedidos (constituição VI). Cada teste leva `@pytest.mark.req("F-xx")`. Os identificadores **F-100 a F-112** estão fixados na tabela da `spec.md` (faixa da experiência v2, Funcional §12.4, sem alargar), partilhados com o frontend pela convenção «Verificado no Frontend»; o isolamento usa `PT-AC-9` e `SEC-authz`.

**Comandos**: contentores Docker, como em `quickstart.md` (o `make` não corre nesta máquina). Não há commits nesta lista; o `/simplify` corre antes de cada commit (T031).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: pode correr em paralelo (ficheiros diferentes, sem dependência de tarefa por acabar)
- **[Story]**: US1 perfil · US2 validação · US3 onboarding · US4 mercados · US5 consentimento · US6 eliminar a conta · US7 originais em bloco · US8 opção da lista

## Phase 1: Setup

- [x] T001 Confirmar a linha de base sem alterar ficheiros: construir `basketiq-test` e correr a bateria, `ruff`, `mypy`, `uv lock --check` e a rastreabilidade (comandos de `quickstart.md`); registar o resultado antes de qualquer alteração

---

## Phase 2: Foundational (bloqueia as histórias)

- [x] T002 Criar a migração `alembic/versions/20261031_e5a8b3d1f2c7_user_profiles.py` (`down_revision = "d4f7a2c8e1b6"`): tabela `user_profiles` com as colunas, `CHECK (household_size BETWEEN 1 AND 12)` e `CHECK (monthly_goal_cents BETWEEN 5000 AND 80000)` de `data-model.md`; `downgrade` apaga a tabela
- [x] T003 Acrescentar o modelo `UserProfile` a `src/basketiq/db/models.py` conforme `data-model.md` (`user_id` chave primária e estrangeira `ON DELETE CASCADE`, `merchants` e `goals` JSONB com `server_default '[]'`)
- [x] T004 [P] Acrescentar `Goal` (`finances`, `shopping_list`, `savings`) e `MERCHANT_NAMES` (os 7 suportados) a `src/basketiq/domain/status.py`
- [x] T005 [P] Acrescentar a `src/basketiq/api/schemas.py` os esquemas `ProfileOut`, `ProfilePatch` (todos opcionais; `StrictInt` com `ge`/`le`; `merchants` só suportados e sem repetidos; `goals` só dos 3 valores; `onboarding_completed: Literal[True]`), `MerchantOut`, `StoredFilesOut` e `StoredFilesDeleted`, conforme `contracts/api.md`

**Checkpoint**: tabela, modelo, constantes e esquemas existem; migração com upgrade, downgrade e upgrade verde.

---

## Phase 3: User Story 1 — Guardar e ler o perfil (P1)

**Goal**: `GET` e `PATCH /v1/me/profile` com «omitido = manter, `null` = apagar».

**Independent Test**: chamadas à API com um utilizador de teste.

- [x] T006 [P] [US1] Escrever `tests/api/test_profile.py` (novo): `F-100` perfil vazio numa conta nova (200, sem erro); `F-101` guardar e ler os mesmos valores (2 pessoas, 3 mercados, 2 objetivos, meta 40 000) e repetir o `PATCH` dá o mesmo; `F-102` `PATCH` sem a meta mantém-na, com `null` apaga-a, `{}` não altera; isolamento (`PT-AC-9`, `SEC-authz`): utilizador B nunca vê o perfil de A e pedido sem sessão dá 401
- [x] T007 [US1] Criar `src/basketiq/services/profiles.py`: `get_profile` (vazio sem criar linha), criar com `INSERT … ON CONFLICT DO NOTHING` e `SELECT … FOR UPDATE`, e `apply_patch` só com `model_fields_set`
- [x] T008 [US1] Acrescentar `GET /v1/me/profile` (CU) e `PATCH /v1/me/profile` a `src/basketiq/api/routers/me.py`

---

## Phase 4: User Story 2 — Validar os valores (P1)

- [x] T009 [P] [US2] Em `tests/api/test_profile.py` (`F-103`): pessoas 0 e 13 recusadas, 1 e 12 aceites; meta 4 999 e 80 001 recusadas, 5 000, 80 000 e `null` aceites; meta com decimais (`400.5`), texto (`"400"`) e booleano recusadas; mercado fora da lista recusado e repetidos contam uma vez; objetivo fora dos 3 valores recusado; `merchants: null` e `goals: null` recusados; um pedido inválido não grava nem os campos válidos do mesmo corpo; a mensagem diz o limite
- [x] T010 [US2] Ajustar `ProfilePatch` e o mapeamento de erros de validação para cumprir T009 (mensagens com o limite; 422 com `field_errors` no formato existente)

---

## Phase 5: User Story 3 — Onboarding concluído (P1)

- [x] T011 [P] [US3] Em `tests/api/test_profile.py` (`F-104`): `onboarding_completed: true` preenche `onboarding_completed_at`; só isso não grava mais nenhum valor; repetir mantém a data original e os dados; `false` e `null` recusados; conta com compras e sem perfil lê-se com `onboarding_completed_at` nulo
- [x] T012 [US3] Em `src/basketiq/services/profiles.py`, preencher `onboarding_completed_at` só quando está nulo

---

## Phase 6: User Story 4 — Mercados suportados (P1)

- [x] T013 [P] [US4] Escrever `F-105` em `tests/api/test_profile.py`: `GET /v1/merchants` autenticado e sem papel de operador devolve os 7 mercados com `id` e `name`, incluindo El Corte Inglés e sem «desconhecido»; sem sessão dá 401; não expõe parâmetros de parsing; um perfil com um mercado que deixou de existir lê-se sem erro
- [x] T014 [US4] Criar `src/basketiq/api/routers/merchants.py` (`GET /v1/merchants`, CU) e registá-lo na tupla de routers de `src/basketiq/api/main.py`

---

## Phase 7: User Story 5 — Consentimento da versão atual, em todos os endpoints (P1)

- [x] T015 [P] [US5] Em `tests/api/test_profile.py` (`F-106`): com a versão da política alterada **no teste** (monkeypatch de `PRIVACY_POLICY_VERSION`), `GET /v1/me` mostra versão atual diferente da aceite; o `PATCH` do perfil, uma rota de compras e uma de análises dão 403 `privacy_consent_required` sem gravar; `GET /v1/me`, `POST /v1/me/privacy-consent`, `DELETE /v1/me` e `GET /v1/me/profile` continuam acessíveis; depois de consentir tudo é aceite; uma conta com `privacy_accepted_at` e sem `privacy_version` conta como pendente; o consentimento fica auditado sem dados pessoais
- [x] T016 [US5] Em `src/basketiq/api/deps.py`, `consented_user` passa a comparar `privacy_version` com `PRIVACY_POLICY_VERSION` (falta ou diferença: 403 `privacy_consent_required`); sem dependência nova; **não** subir `PRIVACY_POLICY_VERSION` (R4)

---

## Phase 8: User Story 6 — Eliminar a conta apaga o perfil (P1)

- [x] T017 [US6] Em `tests/api/test_receipts.py`, estender `test_account_deletion_removes_everything` (`F-107`): conta com perfil guardado; depois de `DELETE /v1/me` não resta nenhuma linha em `user_profiles` e a auditoria continua pseudonimizada
- [x] T018 [US6] Verificar que o `ON DELETE CASCADE` de T002 chega; só se T017 falhar, apagar o perfil explicitamente em `delete_account` (`src/basketiq/services/accounts.py`)

---

## Phase 9: User Story 7 — Originais em bloco (P2)

- [x] T019 [P] [US7] Escrever `tests/api/test_stored_files.py` (novo): `F-108` o resumo conta ficheiros (compra com 1 foto = 1; com 3 fotos = 3), 0 sem originais, só de compras confirmadas e do próprio utilizador; `F-109` apagar em bloco remove os objetos, mantém compras e dados estruturados, audita com motivo `user_bulk_request`, é idempotente (0 removidos), não toca em compras por confirmar e apaga no máximo 500 compras por chamada com `remaining` (repetir até 0); `F-110` as rotas `/stored-files` não são tomadas por `/{receipt_id}` (nunca 422) nem confundidas com `/{id}/original`
- [x] T020 [US7] Extrair `_erase_original(db, storage, receipt, *, actor, reason)` em `src/basketiq/services/receipts.py` e usá-lo em `delete_original`, em `purge_expired_originals` (`src/basketiq/workers/scheduler.py`) e no apagar em bloco; as baterias de F-22a e F-22b têm de continuar verdes sem alterações
- [x] T021 [US7] Acrescentar `stored_files_summary` e `delete_stored_files` a `src/basketiq/services/receipts.py` (compras `CONFIRMED` com `original_deleted_at` nulo, `FOR UPDATE`, ficheiros = 1 + fotos, no máximo 500 compras por chamada)
- [x] T022 [US7] Acrescentar `GET` e `DELETE /stored-files` a `src/basketiq/api/routers/receipts.py`, declarados **antes** de `/{receipt_id}` (`get` na linha do `GET /{receipt_id}` e `delete` antes do `DELETE /{receipt_id}`)

---

## Phase 10: User Story 8 — Opção da lista inteligente (P1)

- [x] T023 [P] [US8] Atualizar os testes de configuração (`F-111`, `F-112`): `tests/unit/test_app_config.py` (`VALID` e os casos de chave em falta, desconhecida e valor não booleano passam a incluir `SMART_LIST_ENABLED`) e `tests/api/test_config.py` (config pública devolve `smart_list_enabled` falso por omissão e verdadeiro quando ligado; a vista do operador (`GET /v1/admin/config`) mostra a chave nova, `test_admin_config_shows_every_key_to_operators_only`; ficheiro sem a chave ou com valor inválido recusa arrancar)
- [x] T024 [US8] Acrescentar `smart_list_enabled: StrictBool = Field(alias="SMART_LIST_ENABLED")` a `src/basketiq/app_config.py`, a chave `SMART_LIST_ENABLED: false` com comentário a `config/app-config.yaml`, o campo a `PublicConfigOut` (`routers/config.py`) e a `ProductConfigOut` (`routers/admin.py`), e a linha da tabela de chaves do `README.md`

---

## Phase 11: Polish & cross-cutting

- [x] T025 [P] Teste de privacidade (`SEC-logs`): os valores do perfil (pessoas, mercados, meta) não aparecem em logs nem em respostas de erro; acrescentar as chaves do perfil à lista de redação de `src/basketiq/observability/logging.py` só se o teste o mostrar necessário
- [x] T026 Regenerar `openapi.json` (`scripts/export_openapi.py`, em contentor) e confirmar que o diff só traz os endpoints e campos desta spec
- [x] T027 Correr a rastreabilidade com as specs montadas e confirmar que os F-100 a F-112 da tabela da `spec.md` têm teste e que não há ids citados sem teste
- [x] T028 Correr `ruff format --check`, `ruff check`, `mypy`, `bandit`, `pip-audit`, `uv lock --check`, a bateria completa e a migração (upgrade, downgrade total, upgrade); registar o resultado
- [x] T029 Confirmar que o golden sintético (`--baseline tests/golden/baseline.json`) não mudou (esta spec não toca no *parser*)
- [x] T030 Atualizar o `README.md` do backend (secção de endpoints, se existir tabela) com perfil, mercados e originais em bloco
- [x] T031 Correr `/simplify` e repetir T028; mostrar o `git diff --stat`
- [ ] T032 (manual, Liz, **depois de rever**) Reconstruir a imagem, aplicar a migração `e5a8b3d1f2c7` à base de desenvolvimento com o serviço `migrate` e recriar os contentores; sem isto, a API nova falha contra a base antiga
- [ ] T033 (lançamento, Liz) Subir `PRIVACY_POLICY_VERSION` em `src/basketiq/services/accounts.py` e publicar o texto novo da política (redigido pela Liz) **antes** de ativar a recolha do perfil; quando subir, todas as contas ficam bloqueadas em compras e análises até consentirem de novo

---

## Dependencies & Execution Order

- Phase 1 → Phase 2 → histórias → Polish. T032 e T033 são manuais e ficam fora do «pronto».
- **Ordem das histórias:** US1 → US2 → US3 → US4 → US5 → US6 → US7 → US8. As US1 a US6 partilham `tests/api/test_profile.py` e `services/profiles.py`: **não** correr em paralelo as tarefas que editam o mesmo ficheiro. US7 e US8 são independentes das anteriores e entre si (podem ser feitas por outra pessoa em paralelo).
- Dentro de cada história, escrever o teste (T006, T009, T011, T013, T015, T017, T019, T023) antes da implementação e vê-lo falhar.
- `src/basketiq/services/receipts.py` é editado por T020 e T021 (por esta ordem); `src/basketiq/api/routers/receipts.py` só por T022.
- T026 depende de todos os endpoints (T008, T014, T022, T024).

## Parallel opportunities

- Fase 2: T004 e T005 depois de T003.
- Testes novos de ficheiros diferentes: T006 ‖ T019 ‖ T023.
- US7 (T019 a T022) e US8 (T023 e T024) em paralelo com US1 a US6.

## Implementation strategy

- **MVP**: Phases 1 a 3 (US1) dão o perfil lido e escrito. Para o onboarding do frontend (spec 010) o mínimo é **US1 a US5 e US8**: perfil, validação, conclusão, mercados, consentimento e a opção da lista.
- A US7 serve só a spec 014 do frontend (Definições) e pode entrar depois.
- Entrega incremental: validar cada checkpoint com a bateria antes de seguir. Contrato primeiro: só depois de T026 o frontend corre `pnpm gen:api`.
- Fora desta lista: apagar o derivado das compras na eliminação da conta (spec 010, passo C) e a lista inteligente (spec 015).
