# Data Model: Perfil do utilizador

## user_profiles (nova)

Uma linha por utilizador, criada no primeiro `PATCH` (ou pela migração de dados, nas contas anteriores ao onboarding); sem linha, o perfil lê-se como vazio. Apagada com o utilizador (`ON DELETE CASCADE`).

| Coluna | Tipo | Nulo | Regra |
|---|---|---|---|
| `user_id` | `UUID` | não | chave primária e chave estrangeira para `users.id`, `ON DELETE CASCADE` |
| `household_size` | `SMALLINT` | sim | `CHECK (household_size BETWEEN 1 AND 12)`; nulo = por definir |
| `merchants` | `JSONB` | não | lista de identificadores de `SUPPORTED_MERCHANTS` sem repetidos; `server_default '[]'` |
| `goals` | `JSONB` | não | lista de `finances`, `shopping_list`, `savings` sem repetidos; `server_default '[]'` |
| `monthly_goal_cents` | `BIGINT` | sim | `CHECK (monthly_goal_cents BETWEEN 5000 AND 80000)`; cêntimos inteiros; nulo = sem meta |
| `onboarding_completed_at` | `TIMESTAMPTZ` | sim | preenchido uma só vez e nunca desfeito; nulo = onboarding por concluir |
| `created_at` | `TIMESTAMPTZ` | não | `server_default now()` |
| `updated_at` | `TIMESTAMPTZ` | não | `server_default now()`, `onupdate now()` |

Índices: só a chave primária (acesso sempre por `user_id`).

## Constantes de domínio (novas, em `domain/status.py`)

- `Goal` (`StrEnum`): `FINANCES = "finances"`, `SHOPPING_LIST = "shopping_list"`, `SAVINGS = "savings"`. «Todas as anteriores» não se guarda: é a escolha dos três.
- `MERCHANT_NAMES`: identificador → nome para mostrar dos 7 mercados suportados (Continente, Pingo Doce, Lidl, Auchan, Aldi, Mercadona, El Corte Inglés).

## Transições

- Perfil inexistente → criado vazio no primeiro `PATCH` (ou lido como vazio sem criar linha no `GET`).
- `onboarding_completed_at`: `null` → data (primeira vez que chega `onboarding_completed: true`) → imutável.
- Contas anteriores ao onboarding: a migração de dados `f6b9c4e2a3d8` cria o perfil vazio (só `user_id` e `onboarding_completed_at`; pessoas e meta nulos, mercados e objetivos `[]`) das contas com `privacy_accepted_at` preenchido, e preenche a data nos perfis por concluir. Contas sem consentimento ficam sem linha.
- Eliminar a conta → a linha desaparece por cascata; nada fica na auditoria (a auditoria não leva valores do perfil).

## Entidades existentes tocadas (sem alterar o esquema)

- `Receipt` e `ReceiptPhoto`: lidas e atualizadas pelo apagar em bloco (`original_deleted_at`, `original_expires_at`, `version`); ficheiros por compra = 1 + número de fotos adicionais.
- `AuditEvent`: `ORIGINAL_DELETED` com `reason = "user_bulk_request"` (valor novo do campo `reason`, que é texto livre).
