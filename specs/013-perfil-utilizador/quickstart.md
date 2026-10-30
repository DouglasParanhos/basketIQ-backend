# Quickstart: validar a spec 013

Nesta máquina o `make` não corre e não há WSL. Usar Docker Desktop e Git Bash com `export MSYS_NO_PATHCONV=1` (comandos do `Documentation/HANDOFF_2026-10-29.md`, secção 7). Pré-requisito: a *stack* local (`docker compose up`) com a rede `basketiq_default` e o Postgres.

## Verificações automáticas

```bash
# imagem de teste (traz src, tests e scripts; NÃO traz specs)
docker build --target test -t basketiq-test .

# bateria completa, com uma base de teste por execução
docker run --rm --network basketiq_default -e BASKETIQ_TEST_DATABASE_URL=postgresql+psycopg://basketiq:basketiq@postgres:5432/basketiq_test_run1 basketiq-test pytest -m "not golden_real and not llm" -q -p no:cacheprovider
docker exec basketiq-postgres-1 psql -U basketiq -d postgres -c "DROP DATABASE IF EXISTS basketiq_test_run1"

# lint, tipos, segurança, dependências
docker run --rm -e RUFF_CACHE_DIR=/tmp/r basketiq-test sh -c "ruff format --check src tests scripts alembic && ruff check src tests scripts alembic && mypy --cache-dir=/tmp/m"
docker run --rm basketiq-test uv lock --check
docker run --rm basketiq-test sh -c "bandit -q -r src -ll; pip-audit --skip-editable"

# rastreabilidade (precisa das specs)
docker run --rm -v "C:/Users/lizwf/repositories/basketIQ/basketIQ-backend/specs:/app/specs:ro" basketiq-test python scripts/traceability.py

# migração: upgrade, downgrade total, upgrade (como no CI), numa base descartável
docker run --rm --network basketiq_default -e BASKETIQ_DATABASE_URL=postgresql+psycopg://basketiq:basketiq@postgres:5432/basketiq_mig basketiq-test sh -c "alembic upgrade head && alembic downgrade base && alembic upgrade head"
```

O golden (sintético e `--unseen`) não é afetado por esta spec; não precisa de correr.

## Contrato para o frontend

```bash
docker run --rm -v "C:/Users/lizwf/repositories/basketIQ/basketIQ-backend:/app" basketiq-test python scripts/export_openapi.py   # regenera openapi.json
```

Depois, no frontend: `corepack pnpm@9 gen:api` (spec 009, T023 remove o tipo local de `smart_list_enabled`).

## Cenários manuais (API a correr, sessão de teste)

| # | Passo | Esperado |
|---|---|---|
| 1 | `GET /v1/me/profile` numa conta nova | 200, perfil vazio |
| 2 | `PATCH` com `{"household_size":3,"monthly_goal_cents":45000}` e depois `GET` | mesmos valores |
| 3 | `PATCH` com `{"monthly_goal_cents":null}` | meta apagada, pessoas mantidas |
| 4 | `PATCH` com `{"household_size":13}` | 422, nada alterado |
| 5 | `PATCH` com `{"onboarding_completed":true}` duas vezes | a data da primeira mantém-se |
| 6 | `GET /v1/merchants` | 7 mercados com nome |
| 7 | `GET /v1/receipts/stored-files` e `DELETE` | contagem e apagar; compras e dados mantêm-se |
| 8 | `GET /v1/config` | inclui `smart_list_enabled: false` |
| 9 | Eliminar a conta | o perfil desaparece com ela |

Aplicar a migração à base de desenvolvimento (`alembic upgrade head` pelo serviço `migrate`) é um passo manual da Liz, fora desta spec.
