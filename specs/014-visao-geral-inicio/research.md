# Research: Visão geral do Início, totais por mês e produtos enriquecidos

Verificado a 2026-10-31 no código do backend (`services/analytics.py`, `api/routers/analytics.py`, `app_config.py`, `db/models.py`) e nas specs 011 e 012 do frontend. Cada ponto resolve uma dúvida do plano. As decisões Q1 a Q11 foram **confirmadas pela Liz a 2026-10-31** (Q7 mudou); a lista está no fim.

**R1. Base do gasto do mês. Confirmada (Q1)**
- Achado: `analytics.summary` soma o valor líquido das **linhas** (`line_total − desconto`). Uma «compra só com total» (D3 do passo A, ainda por implementar: hoje `no_lines` bloqueia a confirmação) não tem linhas e ficaria fora; o total impresso (`receipts.total_cents`) pode ter descontos que não estão nas linhas.
- Decisão: o gasto de uma compra é `receipts.total_cents`; se for nulo, a soma líquida das suas linhas. O gasto do mês é a soma das compras `CONFIRMED` com `purchase_at` dentro do mês de Lisboa. Cumpre US1.15 (a «só com total» entra no gasto) sem esperar pelo passo A.
- Consequência a aceitar: o gasto do Início pode diferir em cêntimos do total do Resumo (que soma linhas) quando há desconto no total. A reconciliação já limita a diferença (tolerância por mercado).
- Alternativa: soma das linhas, igual ao Resumo (rejeitada: deixa de fora as compras só com total quando o passo A chegar).

**R2. Mês e fronteiras.**
- Decisão: o mês é o de Lisboa (`settings.timezone`): `[dia 1 às 00:00, dia 1 do mês seguinte às 00:00)` com `datetime.combine(..., tzinfo)`, como `_bounds` (F-18). `purchase_at` nulo não entra (a data em falta bloqueia a confirmação). Sem `month`, usa o mês corrente de Lisboa; `month` com formato inválido → 422 `validation_error` com `field_errors` (US1.17); mês futuro → zeros, sem erro.
- Decisão: o cliente envia sempre `month` explícito (pergunta C9 do frontend); a API aceita a omissão.

**R3. Percentagem da meta e faixa.**
- Decisão: `goal_pct = (spent × 100 + goal // 2) // goal` (inteiros, meio para cima, nunca `float`). `goal` nunca é 0 (perfil: 5 000 a 80 000). A faixa decide-se **sobre o `goal_pct` já arredondado**: `ok` se `goal_pct ≤ GOAL_RING_GREEN_UP_TO_PCT`, `attention` se `≤ GOAL_RING_ORANGE_UP_TO_PCT`, senão `high`; sem meta, `goal_cents`, `goal_pct` nulos e `band = none`. As fronteiras 50, 50,4, 50,5, 75, 75,4 e 75,5 % da spec dão 50/ok, 50/ok, 51/attention, 75/attention, 75/attention, 76/high.
- Gasto ≥ 100 % da meta: `goal_pct ≥ 100`, `band = high`, sem erro e sem limite superior. O «saldo» (e o texto sem linguagem de perda) é do frontend; a API devolve só números (resposta à pergunta C4: o saldo `goal_cents − spent_cents` é uma subtração de inteiros no cliente). Fecha a marca da US1.5.
- Limites: `GOAL_RING_GREEN_UP_TO_PCT` (50) e `GOAL_RING_ORANGE_UP_TO_PCT` (75), inteiros estritos com `1 ≤ verde < laranja ≤ 100`; fora disto a aplicação recusa arrancar.
- A meta é uma só e não tem histórico: um mês passado usa a meta de hoje (pergunta C8). A API devolve sempre a meta atual; o frontend decide como a mostra.

**R4. Média mensal. Confirmada (Q2)**
- Decisão: a média é a média aritmética do gasto dos **meses completos com compras confirmadas**: os meses anteriores ao mês corrente de Lisboa, independentemente do `month` pedido (a média é do utilizador, não do mês que está a ver). O mês corrente, ainda incompleto, não entra (US2.4); um mês sem compras não conta como zero (US2.3). Arredondada ao cêntimo, meio para cima: `(soma × 2 + n) // (2n)`. 300 € e 336 € → 31 800 cêntimos.
- Abaixo de `AVERAGE_MIN_MONTHS` (2 por omissão, chave obrigatória) meses com dados: `monthly_average_cents` e `recent_vs_average_pct` nulos (US2.1). A API devolve também `average_months` (meses com dados) e `average_min_months`, para o frontend dizer «0/2 meses» sem inventar o 2 (pergunta C1).
- A meta nula não afeta a média e vice-versa (US2.6). Compras por confirmar não entram (US2.7).
- Alternativa: incluir o mês corrente (rejeitada: um mês a meio puxa a média para baixo e faz a tendência oscilar).

**R5. Tendência face à média. Confirmada (Q3)**
- Decisão: `recent_vs_average_pct` é o desvio, em percentagem inteira com sinal (meio para cima), entre a **média dos 2 meses completos com compras mais recentes** e a média: `negativo = abaixo`, `positivo = acima` (resposta à pergunta C2). `(recente − média) × 100 / média`, arredondado com inteiros.
- Achado: com exatamente 2 meses com dados (o mínimo) os «2 mais recentes» são todos os meses e o desvio seria sempre 0 %, o que seria enganador (RF-11). Decisão: com menos de 3 meses com dados a tendência é **nula** (a média aparece, a tendência não). `average_months` permite ao frontend dizer o que falta.
- Alternativa: devolver 0 % com 2 meses (rejeitada).

**R6. «Na média». Confirmada (Q4)**
- Achado: o frontend (011, C3) precisa de saber quando o desvio é «na média»; o protótipo usa o conceito sem o definir e o cliente não deve inventar limiares.
- Decisão: chave obrigatória `AVERAGE_ON_TARGET_PCT` (5, inteiro de 0 a 50) e campo `average_trend`: `below` se `recent_vs_average_pct < −X`, `above` se `> X`, senão `on_average`; nulo quando a tendência é nula. É o **único campo acrescentado** ao contrato da Fase 0; sem ele o frontend só diz «na média» com 0 %.

**R7. Semanas. Confirmada (Q5)**
- Decisão: blocos de 7 dias desde o dia 1 do mês de Lisboa: 1–7, 8–14, 15–21, 22–28 e 29–fim. Um mês de 28 dias tem 4 semanas; os outros têm 5, a última com 1 a 3 dias. Cada item: `index` (1 a 5), `from`, `to` (datas de Lisboa, inclusivas) e `spent_cents`; semanas sem compras a 0; a soma é igual ao gasto do mês. Mês futuro: as semanas do calendário, a zeros.
- Alternativa: semana ISO (rejeitada: a primeira e a última semana ficam partidas entre meses e a soma deixa de bater no mês sem regra extra).

**R8. Por mercado e partilhas. Confirmada (Q6)**
- Decisão: agrupa o gasto do mês (R1) por mercado. Mercados suportados: `merchant` é o identificador e `name` vem de `MERCHANT_NAMES` (spec 013). Outros (`UNKNOWN`): `merchant = null` e `name` é o `merchant_name` da compra, ou «Outros»; lojas diferentes têm linhas diferentes. Ordem: gasto decrescente, desempate pelo nome. Cada item: `merchant`, `name`, `spent_cents`, `purchases`, `share_pct`.
- Arredondamento para a soma ser 100: método do maior resto (partilha inteira `floor(gasto × 100 / total)`, os pontos em falta vão para os maiores restos; desempate pela ordem acima). Com gasto 0 a lista é vazia.

**R9. Últimas compras e `empty`. Q7 mudou (Liz, 2026-10-31)**
- Decisão: `recent_receipts[]` são as **5** compras mais recentes do mês pedido, da mais recente para a mais antiga: `id`, `merchant`, `name`, `date` (data de Lisboa), `total_cents` e `status`. Entram as `CONFIRMED` e também as que **requerem revisão** (`REVIEW_REQUIRED`, `COMPLETED`: falta confirmar) ou **estão em processamento** (`RECEIVED`, `VALIDATING`, `EXTRACTING`); as de erro (`INVALID_FORMAT`, `UNREADABLE`, `UNSUPPORTED_MERCHANT`, `TEMPORARY_FAILURE`, `PERMANENT_FAILURE`) ficam de fora, porque o Início não é o sítio onde se tratam. `status` usa os valores de `ReceiptStatus`, os mesmos da API de compras.
- Só esta lista alarga a seleção: gasto, média, semanas, mercados, partilhas e contagens do mês continuam **só com confirmadas** (§12.4). Corrige o texto da RF-15 (emenda 1.4 em curso).
- Data efetiva de uma compra por confirmar: `purchase_at`, ou `created_at` quando a leitura ainda não a trouxe; é a que decide o mês, a ordem e `date`. `merchant` e `total_cents` podem ser nulos nestas; `name` cai em «Outros» se não houver nome. `total_cents` das confirmadas segue R1 (total, ou soma das linhas); das outras é o `total_cents` lido, sem somar linhas (III).
- `empty` é verdadeiro quando a conta **não tem nenhuma compra confirmada em qualquer mês**; uma conta com compras e sem nenhuma no mês pedido tem `empty = false` e o mês vem a zeros (US3.5, US3.6).
- `purchases_this_month` e `purchases_last_month` contam compras confirmadas (mês pedido e mês civil anterior).

**R10. Totais por mês (US4). Confirmada (Q8)**
- Decisão: `GET /v1/analytics/months` (junto das análises, sem risco de colisão com `/{receipt_id}` em `/v1/receipts`). Devolve `[{month: "YYYY-MM", confirmed_count, total_cents}]`, do mais recente para o mais antigo, **só meses com compras confirmadas**, com o mesmo mês de Lisboa e a mesma base de gasto (R1) que a visão geral. Sem parâmetros: um utilizador acumula poucas dezenas de meses.
- A média (R4) usa a mesma função e a mesma consulta (`GROUP BY date_trunc('month', timezone('Europe/Lisbon', purchase_at))`), sem duplicar a regra.

**R11. Produtos enriquecidos (US5). Confirmada (Q9)**
- Achado: `product_analytics` calcula o preço médio por linha em Python (`_base_price`, base mais frequente, ponderado por quantidade); `search_products` só tem contagem e soma.
- Decisão: `ProductSearchItem` ganha `avg_price_cents`, `price_basis` (`un`, `kg` ou `l`), `purchases` (compras distintas) e `merchants` (mercados distintos, sem contar o desconhecido). Depois de escolhidos os `limit` produtos (máx. 100), uma só consulta traz as linhas desses produtos e o preço médio calcula-se com a **mesma função** que o detalhe do produto (extraída de `product_analytics`), por isso os números são iguais nas duas vistas. Campos e ordem atuais mantêm-se (F-20).
- Sem peso ou volume impresso não há preço por kg ou l (§12.4): a base fica `un` (como no detalhe) e `avg_price_cents` é nulo quando não há quantidade para ponderar (US5.2).

**R12. Chaves de configuração.**
- Decisão: `GOAL_RING_GREEN_UP_TO_PCT`, `GOAL_RING_ORANGE_UP_TO_PCT`, `AVERAGE_MIN_MONTHS` (inteiro de 1 a 12) e `AVERAGE_ON_TARGET_PCT`, todas obrigatórias e sem *default* no código (`extra="forbid"` mantém-se), comentadas no YAML. Não entram em `GET /v1/config` (o cliente não decide faixas), mas entram na vista do operador `GET /v1/admin/config`, que mostra cada chave (CFG-02); `test_admin_config_shows_every_key_to_operators_only` tem de mudar.
- Risco de operação (M6 da análise da 013): código novo com um ficheiro de configuração antigo não arranca, nem a API nem o *worker*. O ficheiro do repositório muda no mesmo *commit*; ambientes com cópia própria atualizam-na primeiro.

**R13. Desempenho.**
- Decisão: `overview` faz no máximo 5 consultas pequenas: compras confirmadas do mês (linhas da lista, não das compras com linhas), contagem do mês anterior, totais por mês (média), existência de qualquer compra confirmada e meta do perfil. O subselect das linhas só corre para compras sem `total_cents`. Sem cache nem índice novo (`ix_receipts_user_purchase`). Medição: p95 < 2 s com 5 000 linhas e registo com 50 000 (US6.3, US6.4).
- **Medido a 2026-10-31** (`scripts/seed_perf.py --lines N --bench`, em contentor, numa base descartável, dados sintéticos, 30 pedidos por caso, sem rede):

  | Linhas | `overview` p50 / p95 | `months` p50 / p95 | `products?limit=100` p50 / p95 |
  |---|---|---|---|
  | 5 000 (347 compras) | 28 / 31 ms | 20 / 24 ms | 67 / 101 ms |
  | 50 000 (3 370 compras) | 34 / 62 ms | 25 / 28 ms | 342 / 457 ms |

  `overview` fica muito abaixo de 2 s. Os produtos crescem com as linhas (uma consulta das linhas dos 100 produtos escolhidos), sem consulta por produto.

**R14. Privacidade e métricas.**
- Decisão: `ConsentedUser` nas três rotas; todas as consultas por `user_id`; `metrics.ANALYTICS_REQUESTS.labels("overview", "False", "False")` e `("months", …)` como as rotas atuais (as etiquetas são booleanas, sem valores); teste de privacidade (SEC-logs) com um mês de compras: nenhum valor em logs nem em erros.

**R15. Identificadores de teste. Confirmada (Q10)**
- Achado: restam F-119 a F-125 (7) na faixa da experiência v2 para as specs 011 a 014 do frontend e esta; não chegam.
- Decisão: usar **F-176 a F-182**, da faixa «continuação da experiência v2» (F-176 a F-205, Funcional §12.4), um por história (US1 a US6 e o desempenho), com a convenção «Verificado no Frontend» para os cenários partilhados. Exige subir `F_MAX` de `scripts/traceability.py` de 175 para 205 (uma linha, tarefa T002). Alternativa rejeitada: F-119 a F-125.

## Decisões confirmadas pela Liz (2026-10-31)

| Q | Tema | Opção adotada |
|---|---|---|
| Q1 | Base do gasto | total da compra, com as linhas se o total faltar (R1) |
| Q2 | Meses da média | só meses completos com compras; o corrente fica de fora (R4) |
| Q3 | Tendência | 2 meses mais recentes face à média, com sinal; nula com menos de 3 meses (R5) |
| Q4 | «Na média» | chave `AVERAGE_ON_TARGET_PCT` (5) e campo `average_trend` (R6) |
| Q5 | Semana | blocos de 7 dias desde o dia 1; 5.ª semana parcial (R7) |
| Q6 | Partilhas | maior resto, soma 100 (R8) |
| Q7 | Últimas compras | **mudou:** 5, confirmadas e também em revisão ou em processamento, com `status`; o resto só confirmadas (R9) |
| Q8 | Totais por mês | `GET /v1/analytics/months` (R10) |
| Q9 | Produtos | 4 campos novos, mesmo cálculo do detalhe (R11) |
| Q10 | Identificadores | F-176 a F-182 e `F_MAX` = 205 (R15); o frontend 011 usa F-183 a F-188 |
| Q11 | Meta dos meses passados | a meta atual, sempre (R3) |
