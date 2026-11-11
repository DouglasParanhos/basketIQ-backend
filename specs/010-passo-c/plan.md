# Plano: passo C (spec 010)

**Estrutura** (como a spec 014: regras puras em `domain/`, consultas em `services/`):
- `config/catalog.yaml`: classes, nós e grupos, com regras, casos, abreviaturas e intervalos plausíveis. É carregado por `basketiq/catalog.py`, estrito como o `app-config.yaml`: a API não arranca se o ficheiro estiver inválido.
- `domain/grouping.py` (classificar) e `domain/size.py` (tamanho, D9): puros, com testes de unidade.
- `services/groups.py`:
  - resolve cada linha (grupo, tamanho, quantidade na base);
  - D11 por nota, D12 por grupo, «Por completar» e regras.
- `user_line_rules`: a única tabela nova (migração `d3e7a91c5f20`), apagada com o utilizador (FK em cascata).
- `api/routers/groups.py`. `GET /v1/receipts/{id}` e a análise do produto ganham campos.

**Verificação da constituição:**
- I: tudo local, sem LLM.
- II: `Decimal` e cêntimos inteiros, arredondamento meio para cima só na resposta.
- III: sem tamanho por omissão; «por agrupar» em vez de palpite.
- IV: filtrado pelo dono; regras privadas; nada nos logs.
- VI: F-56 a F-75.
- VII: sem colunas novas nas linhas, porque o cálculo é feito na hora.

**Desempenho:**
- Cada pedido carrega as regras do utilizador uma vez e classifica cada descrição uma vez, por (cadeia, descrição normalizada).
- O D11 de uma nota lê só os 90 dias anteriores. O «Por completar» lê todas as linhas confirmadas do utilizador.
- A medir com `scripts/seed_perf.py` se ficar perto do limite (p95 < 2 s com 5 000 linhas).

**Verificações:** `ruff`, `mypy`, `bandit`, `uv lock --check`, alembic up/down/up, `pytest -m "not ocr and not golden_real and not llm"`, golden sintético, `scripts/traceability.py`, e regenerar o `openapi.json`.
