# Contrato da API (spec 014)

Convenções do repositório: JSON, erros `{code, message, request_id, field_errors?}`, sessão por cookie. «Consentido» = `ConsentedUser`. Todos os valores monetários são **cêntimos inteiros**; todas as percentagens são **inteiros** (arredondados ao inteiro, meio para cima). Gasto, média, semanas, mercados e contagens só com compras confirmadas do próprio utilizador; `recent_receipts` também traz as que requerem revisão ou estão em processamento (Q7). Os mesmos nomes de campo são os da spec 014 (Fase 0); o que esta spec acrescenta está marcado **novo**.

## Endpoints

| Método e caminho | Autenticação | Resposta | Erros |
|---|---|---|---|
| `GET /v1/analytics/overview?month=YYYY-MM` | Consentido | `OverviewOut` | 401; 403 `privacy_consent_required`; 422 `month` inválido |
| `GET /v1/analytics/months` | Consentido | `list[MonthTotalOut]` | 401; 403 |
| `GET /v1/products?q=&limit=` (existente) | Consentido | `list[ProductSearchItem]` com 4 campos novos | 401; 403 |
| `GET /v1/admin/config` (existente) | operador | + 4 chaves de `ProductConfigOut` | 401, 403 |

`GET /v1/config` (pública) **não muda**: as faixas e os mínimos são do backend.

## Esquemas

```text
OverviewOut {
  month: "YYYY-MM"                         # novo: o mês devolvido (omissão = mês corrente de Lisboa)
  spent_cents: int                         # 0 em mês sem compras
  goal_cents: int | null                   # a meta ATUAL do perfil (um mês passado usa a meta de hoje)
  goal_pct: int | null                     # (spent*100 + goal//2) // goal; pode passar de 100
  band: "ok" | "attention" | "high" | "none"   # sobre goal_pct: ok <= verde, attention <= laranja, senão high; none = sem meta
  monthly_average_cents: int | null        # só com average_months >= average_min_months
  recent_vs_average_pct: int | null        # novo: com sinal, negativo = abaixo da média; nulo com menos de 3 meses
  average_trend: "below" | "on_average" | "above" | null   # novo (Q4)
  average_months: int                      # novo: meses completos com compras confirmadas
  average_min_months: int                  # novo: AVERAGE_MIN_MONTHS
  purchases_this_month: int
  purchases_last_month: int
  weeks: list[WeekOut]
  by_merchant: list[MerchantShareOut]      # gasto decrescente
  recent_receipts: list[RecentReceiptOut]  # até 5 do mês, da mais recente; confirmadas, em revisão e em processamento
  empty: bool                              # true = a conta não tem nenhuma compra confirmada, em qualquer mês
}

WeekOut          { index: int, from: date, to: date, spent_cents: int }   # index 1..5; blocos de 7 dias desde o dia 1; soma = spent_cents
MerchantShareOut { merchant: str | null, name: str, spent_cents: int, purchases: int, share_pct: int }   # share_pct soma 100; merchant nulo = loja sem leitor próprio
RecentReceiptOut { id: uuid, merchant: str | null, name: str, date: date, total_cents: int | null, status: ReceiptStatus }   # status: CONFIRMED | REVIEW_REQUIRED | COMPLETED | RECEIVED | VALIDATING | EXTRACTING (nunca os de erro); date = purchase_at, ou created_at se a leitura ainda não a trouxe

MonthTotalOut    { month: "YYYY-MM", confirmed_count: int, total_cents: int }

ProductSearchItem {                        # campos atuais mantêm-se (id, name, unit_type, lines, spent_cents, last_purchase_at)
  avg_price_cents: int | null              # novo: ponderado por quantidade, na base price_basis
  price_basis: "un" | "kg" | "l"           # novo
  purchases: int                           # novo: compras distintas
  merchants: int                           # novo: mercados distintos
}
```

## Regras

1. `month` com formato diferente de `YYYY-MM` ou mês fora de 01 a 12 → 422 `validation_error` com `field_errors` no campo `month`. Mês futuro → 200 com zeros.
2. Mês de Lisboa: uma compra às 00:30 de 1 de novembro conta só em novembro (F-18). Os totais por mês usam a mesma definição.
3. Gasto de uma compra: `total_cents`, ou a soma líquida das linhas se o total faltar. Compras por confirmar ou em revisão nunca entram no gasto, na média, nas semanas, nos mercados nem nas contagens; só em `recent_receipts`, com o seu `status`.
4. Sem meta: `goal_cents = goal_pct = null`, `band = "none"`, `spent_cents` na mesma.
5. Fronteiras (meta de 40 000): gasto 20 000 → 50/`ok`; 20 160 → 50/`ok`; 20 200 → 51/`attention`; 30 000 → 75/`attention`; 30 160 → 75/`attention`; 30 200 → 76/`high`.
6. Média e tendência: ver `research.md` R4 e R5. Sem média: `monthly_average_cents`, `recent_vs_average_pct` e `average_trend` nulos.
7. `months`: só meses com compras confirmadas, do mais recente para o mais antigo.
8. Nada de compras nem do perfil em logs, erros ou etiquetas de métricas. Outro utilizador nunca vê estes dados (PT-AC-9, SEC-authz).

## Respostas às perguntas do frontend 011 (C1 a C9)

O ficheiro `contracts/overview-api.md` do frontend 011, que devia listar C1 a C10, não existe; as perguntas foram recolhidas de `plan.md`, `research.md`, `data-model.md` e `spec.md` desse frontend e **C10 não foi encontrada** (pendente do frontend).

| Pergunta | Resposta |
|---|---|
| C1 meses com dados | `average_months` e `average_min_months` |
| C2 sinal de `recent_vs_average_pct` | negativo = abaixo da média |
| C3 «na média» | `average_trend` (limiar `AVERAGE_ON_TARGET_PCT`, 5) |
| C4 saldo da meta | não vem na API: `goal_cents − spent_cents` no cliente |
| C5 forma de `weeks[]` | `WeekOut` |
| C6 forma de `by_merchant[]` | `MerchantShareOut` |
| C7 forma de `recent_receipts[]` | `RecentReceiptOut` com `status`; inclui as que requerem revisão ou estão em processamento (Q7) |
| C8 meta de meses passados | a meta atual, sempre |
| C9 `month` explícito | aceite; a omissão usa o mês corrente de Lisboa |
