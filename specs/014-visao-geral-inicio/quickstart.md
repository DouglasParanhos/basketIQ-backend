# Quickstart: validar a spec 014

Nesta máquina o `make` não corre e não há WSL. Docker Desktop e Git Bash com `export MSYS_NO_PATHCONV=1` (comandos em `README.md`, secção «Without `make`», e na `quickstart.md` da spec 013). Pré-requisito: a *stack* local com a rede `basketiq_default` e o Postgres. **Não se aplicam migrações à base de desenvolvimento** (esta spec não tem nenhuma).

## Verificações automáticas

```bash
docker build --target test -t basketiq-test .

# regras puras primeiro (sem base de dados)
docker run --rm basketiq-test pytest tests/unit/test_overview_rules.py tests/unit/test_app_config.py -q -p no:cacheprovider

# bateria completa, com uma base de teste por execução
docker run --rm --network basketiq_default -e BASKETIQ_TEST_DATABASE_URL=postgresql+psycopg://basketiq:basketiq@postgres:5432/basketiq_test_run1 basketiq-test pytest -m "not golden_real and not llm" -q -p no:cacheprovider
docker exec basketiq-postgres-1 psql -U basketiq -d postgres -c "DROP DATABASE IF EXISTS basketiq_test_run1"

# lint, tipos, segurança, dependências, rastreabilidade
docker run --rm -e RUFF_CACHE_DIR=/tmp/r basketiq-test sh -c "ruff format --check src tests scripts alembic && ruff check src tests scripts alembic && mypy --cache-dir=/tmp/m"
docker run --rm basketiq-test uv lock --check
docker run --rm basketiq-test sh -c "bandit -q -r src -ll; pip-audit --skip-editable"
docker run --rm -v "C:/Users/lizwf/repositories/basketIQ/basketIQ-backend/specs:/app/specs:ro" basketiq-test python scripts/traceability.py

# contrato para o frontend
docker run --rm -v "C:/Users/lizwf/repositories/basketIQ/basketIQ-backend:/app" basketiq-test python scripts/export_openapi.py
```

O golden não é afetado (esta spec não toca no *parser*). Depois do `openapi.json`, no frontend: `corepack pnpm@9 gen:api` (spec 011).

## Desempenho (US6.3, US6.4)

Semear uma conta sintética com 5 000 linhas e medir `overview` (`scripts/seed_perf.py --lines 5000 --bench`, equivalente do `make bench`) numa base descartável; repetir com 50 000 e registar. Meta: p95 < 2 s com 5 000.

## Cenários manuais (API a correr, sessão de teste, dados sintéticos)

| # | Passo | Esperado |
|---|---|---|
| 1 | `GET /v1/analytics/overview` numa conta nova | 200, `empty = true`, `band = "none"` |
| 2 | Perfil com meta 40 000 e compras que somam 15 200 no mês | `goal_pct = 38`, `band = "ok"` |
| 3 | Mais compras até 24 800 e depois 35 200 | `62`/`attention` e `88`/`high` |
| 4 | `?month=2026-13` e `?month=abc` | 422 com `field_errors` em `month` |
| 5 | Compras em 3 meses completos anteriores | `monthly_average_cents` preenchido; com 2 meses a tendência é nula |
| 6 | `GET /v1/analytics/months` | meses com compras confirmadas, do mais recente |
| 6b | Uma compra em revisão e uma em processamento no mês | aparecem em `recent_receipts` com `status`; o gasto não muda |
| 7 | `GET /v1/products` | cada item com `avg_price_cents`, `price_basis`, `purchases`, `merchants` |
| 8 | Utilizador B pede os mesmos endpoints | só vê os seus dados |
