# Data Model: Visão geral do Início

**Sem tabelas nem migrações novas.** Tudo se calcula na leitura, sobre dados que já existem.

## Dados lidos

| Origem | Campos usados | Para quê |
|---|---|---|
| `receipts` | `user_id`, `processing_status = CONFIRMED`, `purchase_at`, `merchant`, `merchant_name`, `total_cents` | gasto, contagens, semanas, mercados, totais por mês |
| `receipts` (sem filtrar a confirmadas) | `processing_status` em `CONFIRMED`, `REVIEW_REQUIRED`, `COMPLETED`, `RECEIVED`, `VALIDATING`, `EXTRACTING`; `purchase_at` ou `created_at` | só as últimas 5 compras (R9) |
| `receipt_lines` | `receipt_id`, `line_total_cents`, `discount_cents` (e, nos produtos, `quantity_decimal`, `unit`, `unit_price_cents`, `normalized_product_id`) | gasto de uma compra sem `total_cents`; preço médio dos produtos |
| `user_profiles` (spec 013) | `monthly_goal_cents` | meta; sem linha ou nula → sem meta |
| `products` | `canonical_name`, `unit_type` | lista de produtos |

Regra do gasto de uma compra: `total_cents`, ou, se nulo, a soma de `line_total_cents − desconto` das suas linhas (R1). Só entram compras `CONFIRMED` do próprio utilizador (§12.4).

## Configuração nova (`config/app-config.yaml`, todas obrigatórias)

| Chave | Tipo | Valor | Regra |
|---|---|---|---|
| `GOAL_RING_GREEN_UP_TO_PCT` | inteiro estrito | 50 | `1 ≤ verde < laranja` |
| `GOAL_RING_ORANGE_UP_TO_PCT` | inteiro estrito | 75 | `laranja ≤ 100` |
| `AVERAGE_MIN_MONTHS` | inteiro estrito | 2 | de 1 a 12 |
| `AVERAGE_ON_TARGET_PCT` | inteiro estrito | 5 | de 0 a 50 |

## Respostas (esquemas Pydantic novos)

- `OverviewOut`: ver [contracts/api.md](./contracts/api.md). Itens: `WeekOut`, `MerchantShareOut`, `RecentReceiptOut`.
- `MonthTotalOut`: `month`, `confirmed_count`, `total_cents`.
- `ProductSearchItem`: + `avg_price_cents`, `price_basis`, `purchases`, `merchants`.

## Regras de cálculo (funções puras em `domain/overview.py`)

| Função | Entrada | Saída |
|---|---|---|
| `goal_pct(spent, goal)` | cêntimos inteiros | `(spent×100 + goal//2) // goal` |
| `ring_band(pct, green, orange)` | inteiros | `ok`, `attention` ou `high` |
| `monthly_average(month_totals, min_months)` | gasto dos meses completos com compras | cêntimos (meio para cima) ou `None` |
| `recent_vs_average(month_totals)` | idem | percentagem inteira com sinal ou `None` (menos de 3 meses) |
| `average_trend(pct, on_target)` | inteiros | `below`, `on_average`, `above` ou `None` |
| `week_blocks(year, month)` | mês civil | 4 ou 5 intervalos de 7 dias |
| `shares(values)` | gastos por mercado | percentagens inteiras que somam 100 (maior resto) |

## Estados e transições

Não há estado guardado. Casos que a resposta distingue:

| Situação | `empty` | `spent_cents` | `goal_*` | `monthly_average_cents` |
|---|---|---|---|---|
| Conta sem nenhuma compra confirmada | `true` | 0 | conforme o perfil | nulo |
| Compras noutros meses, nenhuma no mês pedido | `false` | 0 | conforme o perfil | conforme os meses |
| Sem perfil ou sem meta | — | — | nulos, `band = none` | — |
| Menos de `AVERAGE_MIN_MONTHS` meses com dados | — | — | — | nulo (e tendência nula) |
| Mês futuro | `false` ou `true` conforme a conta | 0 | conforme o perfil | conforme os meses |

## Privacidade

Nada guarda valores de compras fora das tabelas existentes; a eliminação da conta (cascata) apaga tudo o que alimenta estas respostas. Logs e etiquetas de métricas não levam valores.
