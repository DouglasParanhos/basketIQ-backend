# Contrato: passo C (spec 010)

Todos os pedidos pedem sessão e consentimento de privacidade. Os que mudam estado pedem também o cabeçalho CSRF. Os valores vêm em cêntimos inteiros por unidade da base (`basis`: `kg`, `l`, `un`, `rolo` ou `dose`), arredondados meio para cima. O texto para a pessoa compõe-o o cliente a partir dos estados.

## `GET /v1/receipts/{id}` (campos novos)

- `lines[].grouping`: só numa nota confirmada; nas outras é `null`.
  - `status`: `grouped`, `ungrouped` ou `not_comparable`.
  - `group`: `{slug, name, class_slug}` ou `null`.
  - `group_source`: `catalog` ou `user`.
  - `size`: `{value, unit, source}` ou `null`. A origem é `description`, `user` ou `receipt`; numa linha ao peso, o tamanho é o peso impresso no talão (origem `receipt`, «talão» no CSV).
  - `price_check`: só quando o estado é `grouped`.
    - `status`: `above`, `below`, `normal`, `no_average` ou `missing_size`.
    - Também: `basis`, `purchases`, `needed` e `window_days`.
    - `price_cents` vem com base conhecida.
    - `average_cents` e `effect_cents` vêm só com média.
- `ungrouped_descriptions`: quantas descrições da nota ficaram «por agrupar» (0 antes de confirmar).

## `GET /v1/products/{id}/analytics` (campo novo)

- `group`: o grupo da última compra confirmada do produto, ou `null`.

## `GET /v1/catalog/groups`

`{version, classes: [{slug, name, groups: [{slug, name, bases, comparable}]}]}`. Tem as 15 classes; uma classe pode não ter grupos.

## `GET /v1/groups/{slug}/comparison`

Devolve:
- `group`, `status`, `basis`, `window_days`, `date_from`, `date_to`;
- `purchases`, `average_cents` (média do grupo), `needed`;
- `chains: [{merchant, merchant_name, purchases, average_cents, missing_purchases}]`, por nome;
- `typical_difference_cents`, só com `clear_difference`.

`status` toma um destes valores: `no_purchases`, `single_chain`, `not_enough_purchases`, `no_clear_difference` ou `clear_difference`. Um *slug* que não é grupo dá 404.

## `GET /v1/to-complete?tab=ungrouped|missing_size`

Devolve `{tab, total, page_size, items}`. Os itens vêm por gasto, do maior para o menor. Cada item tem:
- `chain`, `merchant_name`, `description` (a grafia mais recente), `description_key` e `unit`;
- `lines`, `purchases` e `spent_cents`;
- `group` (só em `missing_size`), `hint` (`{slug, name}` ou `null`) e `suggestions` (`[{slug, name, class_slug}]`, até 5);
- `rule_id`.

## `GET /v1/line-rules`

Devolve «Atribuídos por si», do mais recente para o mais antigo: `[{id, chain, description_key, description, group, not_comparable, size, applied_lines, updated_at}]`.

## `PUT /v1/line-rules`

`{targets: [{chain?, description}] (1 a 50), group?, not_comparable?, size?: {value, unit}}`.
- Só os campos enviados mudam.
- `group: null` e `size: null` limpam o campo.
- `group` põe `not_comparable` a falso, e `not_comparable: true` limpa o grupo.
- Uma regra que fica vazia apaga-se.

Resposta: `{rules, applied_lines}`. `applied_lines` é o número de linhas confirmadas a que as regras se aplicam.

Erros 422:
- `unknown_group`: o *slug* não existe ou não é grupo;
- `implausible_size`: tamanho fora dos limites do catálogo;
- `nothing_to_change`: o pedido não traz nenhum campo a mudar.

## `DELETE /v1/line-rules/{id}`

204, ou 404 se a regra não for do utilizador.
