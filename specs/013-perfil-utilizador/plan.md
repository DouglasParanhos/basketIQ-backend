# Implementation Plan: Perfil do utilizador, mercados e gestão de originais em bloco

**Branch**: `013-perfil-utilizador` (sem ramo git próprio) | **Date**: 2026-10-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/013-perfil-utilizador/spec.md`

## Summary

Cinco acrescentos pequenos à API, sem mudar o pipeline de leitura:

1. **Perfil** (`GET` e `PATCH /v1/me/profile`): tabela nova `user_profiles` (1:1 com `users`, apagada em cascata com a conta), `PATCH` com «campo omitido = manter, `null` = apagar», validação estrita (cêntimos inteiros) e conclusão do onboarding registada uma só vez.
2. **Mercados** (`GET /v1/merchants`): os 7 suportados, com nome para mostrar.
3. **Consentimento**: `consented_user` passa a exigir a **versão atual** da política em todos os endpoints (403 `privacy_consent_required`; versão em falta conta como desatualizada). `PRIVACY_POLICY_VERSION` **não** sobe nesta spec: é tarefa de lançamento com o texto novo (T033).
4. **Originais em bloco** (`GET` e `DELETE /v1/receipts/stored-files`): contar e apagar os ficheiros das compras confirmadas, com a rota declarada antes de `/{receipt_id}`.
5. **Opção da lista** (`smart_list_enabled`): chave nova e obrigatória em `config/app-config.yaml`, exposta em `GET /v1/config` e na vista do operador.

Contrato primeiro: depois de implementar regenera-se `openapi.json` para o frontend (`pnpm gen:api`).

## Technical Context

**Language/Version**: Python 3.12 (`>=3.12,<3.13`)

**Primary Dependencies**: FastAPI, SQLAlchemy 2, Alembic, Pydantic 2

**Storage**: PostgreSQL (tabela nova, migração Alembic); objetos S3 compatíveis só para apagar originais (`ObjectStorage.delete_prefix`, já existente)

**Testing**: pytest (`@pytest.mark.req("F-xx")`), fixtures `client`, `login`, `storage`, `db` de `tests/conftest.py`; base de teste por execução (HANDOFF §7)

**Target Platform**: contentores Linux; nesta máquina o `make` não corre: usar Docker Desktop (comandos do HANDOFF §7 e `quickstart.md`)

**Project Type**: web-service (API + worker; o frontend é outro repositório)

**Performance Goals**: sem meta nova para perfil e mercados (uma linha por utilizador, consulta por chave primária). Apagar em bloco: no máximo 500 compras por chamada, com `remaining` (R6)

**Constraints**: dinheiro em cêntimos inteiros (II); nenhum campo do perfil em logs nem na auditoria (IV); chaves de configuração obrigatórias e sem *default* no código (spec 008); `PATCH` não grava valores por omissão (III)

**Scale/Scope**: 1 tabela, 1 migração, 5 endpoints novos (2 de perfil, 1 de mercados, 2 de originais), 1 campo novo em 2 respostas de configuração, ~13 identificadores de teste

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.* Constituição do backend v1.0.0.

| Princípio | Avaliação |
|---|---|
| I. Processamento local | N/A (não toca no pipeline; nada sai para serviços externos) |
| II. Dinheiro exato | Passa: `monthly_goal_cents` inteiro (`StrictInt`, 5 000 a 80 000), recusa decimais e texto |
| III. Nunca inventar valores | Passa: campos por omissão ficam `null`; o `PATCH` só grava o que vem no pedido; a conclusão do onboarding não grava valores |
| IV. Privacidade por omissão | Passa com verificação: todas as consultas pelo dono; perfil apagado em cascata com a conta; sem dados de perfil em logs (teste dedicado, T025). **Decisão:** versão atual exigida em todos os endpoints; subir `PRIVACY_POLICY_VERSION` com o texto novo é tarefa de lançamento (T033), antes de ativar a recolha (D8) |
| V. Mobile-first e acessível | N/A (API) |
| VI. Rastreabilidade | Passa: cada critério tem teste com `@pytest.mark.req`; F-100 a F-112 partilhados com o frontend (convenção «Verificado no Frontend»), faixa da experiência v2 (Funcional §12.4) sem alargar |
| VII. Simplicidade | Passa: uma tabela, um serviço novo pequeno, sem abstrações; um auxiliar partilhado `_erase_original` evita copiar o apagar pela terceira vez |

Resultado: **sem violações**.

## Project Structure

### Documentation (this feature)

```text
specs/013-perfil-utilizador/
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
alembic/versions/20261031_e5a8b3d1f2c7_user_profiles.py     # NOVO
src/basketiq/
├── app_config.py                      # + smart_list_enabled (SMART_LIST_ENABLED)
├── db/models.py                       # + UserProfile
├── domain/status.py                   # + Goal, MERCHANT_NAMES
├── api/
│   ├── deps.py                        # consented_user exige a versão atual da política
│   ├── schemas.py                     # + ProfileOut, ProfilePatch, MerchantOut, StoredFiles*
│   ├── main.py                        # regista o router de mercados
│   └── routers/
│       ├── me.py                      # + GET e PATCH /v1/me/profile
│       ├── merchants.py               # NOVO: GET /v1/merchants
│       ├── receipts.py                # + GET e DELETE /stored-files, ANTES de /{receipt_id}
│       ├── config.py                  # + smart_list_enabled em PublicConfigOut
│       └── admin.py                   # + smart_list_enabled em ProductConfigOut
├── services/
│   ├── profiles.py                    # NOVO: obter, criar se não existe, aplicar PATCH
│   └── receipts.py                    # + stored_files_summary, delete_stored_files, _erase_original
└── workers/scheduler.py               # purge_expired_originals passa a usar _erase_original
config/app-config.yaml                 # + SMART_LIST_ENABLED: false
tests/api/test_profile.py              # NOVO (perfil, mercados, consentimento, eliminação)
tests/api/test_stored_files.py         # NOVO
tests/api/test_config.py, tests/unit/test_app_config.py, tests/api/test_receipts.py   # ajustados
openapi.json, README.md                # regenerado; tabela de chaves
```

**Structure Decision**: estrutura atual do backend; só se acrescentam dois ficheiros de código (`services/profiles.py`, `routers/merchants.py`) e dois de teste.

## Complexity Tracking

Sem violações a justificar. Decisões que merecem atenção estão em `research.md` (R4 consentimento, R6 volume do apagar em bloco).

## Post-Design Constitution Re-check

Mantém-se: nenhum desenho acrescentou armazenamento, dependências ou dados fora da tabela `user_profiles`. O risco IV (nova versão da política) fica registado como pergunta, não como violação do desenho.
