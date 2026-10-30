# Feature Specification: Leitura universal (ler qualquer nota, mesmo de um lugar nunca visto)

**Feature Branch**: `009-leitura-universal`

**Created**: 2026-10-25

**Status**: In progress (fluxo A0: medição)

**Input**: Plano "Passo A" (fluxos A0 a A7): dado um PDF ou uma foto de uma nota de qualquer
estabelecimento, devolver linhas, data, total e estabelecimento de forma que o utilizador corrija
pouco, e menos ainda na segunda nota do mesmo lugar. Especificação Técnica §5 (parsers),
Plano de Testes §6 e §13.

Fora do passo A: catálogo de produtos entre cadeias, exportação e relatório, lista inteligente.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Medir a leitura de layouts nunca vistos (A0) (Priority: P1)

A equipa corre `make golden-unseen` e vê, por loja, por rota (texto de PDF ou OCR de foto) e no
total, quanto o parser genérico acerta sozinho, sem o parser calibrado da cadeia e, no segundo modo,
sem sequer a identificação da cadeia.

**Why this priority**: sem medição não há como saber se as melhorias seguintes ajudam. Todo o
conjunto dourado de hoje é de cadeias calibradas.

**Independent Test**: correr o modo `--unseen` sobre o conjunto sintético e sobre `../Receipts`.

**Acceptance Scenarios**:

1. **Given** uma nota de uma cadeia calibrada, **When** a avaliação corre em modo `--unseen`, **Then**
   o parser usado é o genérico (confiança limitada a 0,6) e a nota vai para revisão; no modo normal
   continua a ser confirmada como hoje. — F-33 ·
   `tests/golden/test_golden_unseen.py::test_unseen_mode_uses_the_generic_parser_and_sends_everything_to_review`
2. **Given** `--force-unknown`, **When** a avaliação corre, **Then** o classificador não é consultado e
   a exatidão da loja deixa de ser medida (não há o que verificar). — F-33 ·
   `tests/golden/test_golden_unseen.py::test_force_unknown_bypasses_the_classifier`
3. **Given** um relatório, **When** é produzido, **Then** traz as métricas por loja, por rota e `overall`,
   incluindo correções por nota, notas erradas sem revisão e tempo médio e máximo. — F-33 ·
   `tests/golden/test_golden_unseen.py::test_report_has_per_route_overall_and_the_new_metrics`
4. **Given** uma linha mal lida, em falta ou a mais, **When** se contam as correções, **Then** uma linha
   mal lida custa 1 edição (não 2), uma em falta custa 1 e uma a mais custa 1. — F-33 ·
   `tests/golden/test_golden_unseen.py::test_a_misread_line_costs_one_edit_not_two`,
   `tests/golden/test_golden_unseen.py::test_several_wrong_fields_on_one_line_cost_one_edit`,
   `tests/golden/test_golden_unseen.py::test_missing_and_extra_lines_cost_one_edit_each`
5. **Given** uma baseline com métricas por rota e `overall`, **When** se comparam, **Then** uma
   regressão em qualquer uma é detetada e o tempo nunca conta. — F-33 ·
   `tests/golden/test_golden_unseen.py::test_regressions_cover_route_and_overall_and_ignore_timing`
6. **Given** documentos sem `.expected.json` e documentos em `_blind/`, **When** a avaliação corre,
   **Then** os primeiros são listados como não medidos (aviso) e os segundos só entram com `--blind`. —
   F-33 ·
   `tests/golden/test_golden_unseen.py::test_blind_documents_stay_out_unless_asked_and_unlabelled_ones_are_reported`
7. **Given** uma hora esperada, **When** a hora extraída difere até 5 minutos, **Then** conta como certa;
   com 6 minutos conta como errada (FR-008). — F-33 ·
   `tests/golden/test_golden_unseen.py::test_purchase_time_matches_within_five_minutes`

---

### User Story 2 - Ler o QR code fiscal (A1) (Priority: P1)

Identificar o estabelecimento pelo NIF do emitente, obter data e total independentes do OCR e uma
chave exata para duplicados (ATCUD + n.º do documento + NIF). Sem QR legível o fluxo segue como hoje.

**Fonte e leitura (teste de leitura do A1, 2026-10-25).** A especificação lida foi *Especificações
Técnicas, Código de Barras Bidimensional, Código QR* (Portaria n.º 195/2020), **versão 1.0 de 13-08-2020,
numa cópia de terceiros** (audico.pt), porque o PDF oficial da AT deu 404. **A v1.1 não foi confirmada.** O
leitor é tolerante: campos desconhecidos ou novos são ignorados e nunca derrubam a leitura; só se usam os
campos necessários. Os 11 QR reais são a referência prática. A biblioteca escolhida é **zxing-cpp 3.1.1**
(Apache-2.0, confirmada nos metadados do pacote instalado: `License-Expression` e `LICENSE`).

Resultado do teste de leitura nos 12 documentos reais (6 fotos, 6 PDF): zxing-cpp leu 11/12 (fotos 5/6, PDF 6/6;
mediana 52 ms, máximo 405 ms), pyzbar 11/12, OpenCV 8/12. A única nota não lida é uma foto amarrotada (Aldi).
A data do QR (campo F, só a data) coincide em 11/11. O total do QR (campo O) coincide em 9/11; nos dois
Continente é o valor depois do saldo de cartão. O NIF do emitente coincide com a cadeia em 11/11, o ATCUD do
QR coincide com o impresso nos 6 PDF, e em 8 de 11 notas o campo B é o NIF de uma pessoa (nunca guardado).

**Regras decididas (2026-10-25):**

- **Total** (revista no mesmo dia, para nunca baixar o total em silêncio): o QR (O) é o total do documento, ou
  seja, o total da compra menos o saldo de cartão, vale ou crédito de depósito usados e impressos na nota
  (FR-007 não muda: saldo é pagamento, não desconto). Um QR **maior** que o total lido só pode ser erro de leitura
  (um desconto não aumenta o total): o QR (mais o saldo impresso) prevalece, fonte `qr`, com motivo de revisão
  e os dois valores. Um QR **menor** com saldo, vale ou crédito identificados nas linhas impressas: vale a relação
  O = total − saldo e, se bate, não há motivo. Um QR menor sem nada identificado **não substitui** o total lido:
  mantém-se e a nota vai para revisão com os dois valores e a explicação («pode ter havido saldo de cartão ou
  vale»). Sem total lido, o QR (mais o saldo impresso) preenche-o, também com motivo. A **data** do QR prevalece
  sempre que difere (o QR só traz a data; a hora continua a vir da nota), com motivo e os dois valores. A soma
  das linhas continua a ser conferida contra o total final. Confirmar o valor (editá-lo) encerra o motivo.
- **Biblioteca e tempo**: zxing-cpp; escalas por ordem (original, ×2, ×3, ×0,5) com orçamento total de 2 s;
  PDF a 150 dpi na primeira página e, se não ler, nas seguintes.
- **Falha em silêncio**: sem QR legível o pipeline segue como hoje; nunca bloqueia nem atrasa a nota.
- **Guardar só o que se usa**: NIF do emitente (A), data (F), tipo (D), n.º do documento (G), ATCUD (H), total
  de impostos (N) e total (O). Nunca o NIF do adquirente (B) nem o texto bruto do QR.
- **Usos**: classificação (NIF do emitente como evidência forte; tabela única NIF → cadeia; NIF desconhecido
  fica guardado como chave do estabelecimento, para o A4), validação (data e total) e duplicados (RF-08: chave
  NIF do emitente + n.º do documento + ATCUD, por utilizador; avisa e o utilizador decide).

**Cenários (implementados):**

1. **Given** um PDF ou uma foto com o QR fiscal, **When** a nota é lida, **Then** o QR lê-se localmente dentro
   de 2 s, mesmo pequeno numa foto grande, enorme, ou numa página de PDF depois da primeira; sem QR legível, ou
   com um QR que não é fiscal, não há nada; ler nunca levanta exceção e todos os ficheiros partilham o orçamento.
   — F-39 · `tests/unit/test_qr.py::test_reads_the_qr_of_a_photo_and_of_a_pdf_page_after_the_first`,
   `tests/unit/test_qr.py::test_a_small_qr_in_a_big_photo_and_a_huge_photo_are_both_read`,
   `tests/unit/test_qr.py::test_without_a_readable_fiscal_qr_there_is_nothing`,
   `tests/unit/test_qr.py::test_the_time_budget_is_respected`, `tests/unit/test_qr.py::test_reading_never_raises`,
   `tests/unit/test_qr.py::test_all_the_files_of_a_receipt_share_one_budget`
2. **Given** a mensagem da especificação, **When** é interpretada, **Then** só se guardam A, D, F, G, H, N e O;
   campos novos ou fora de ordem não estragam; sem emitente, data ou total válidos não é um recibo; B nunca é
   lido para lado nenhum. — F-40 · `tests/unit/test_qr.py::test_reads_the_fields_in_use_from_the_specification_example`,
   `tests/unit/test_qr.py::test_the_buyers_tax_number_has_nowhere_to_live`,
   `tests/unit/test_qr.py::test_unknown_new_and_out_of_order_fields_do_not_break_the_reading`,
   `tests/unit/test_qr.py::test_a_message_without_issuer_date_or_total_is_not_a_receipt`,
   `tests/unit/test_qr.py::test_optional_fields_that_are_malformed_are_left_out_not_fatal`
3. **Given** o NIF do emitente, **When** a nota é classificada, **Then** o do QR é evidência forte e a mesma
   tabela lê o NIF impresso (pontos, espaços, a letra O); um NIF desconhecido não classifica e fica guardado. —
   F-41 · `tests/unit/test_qr.py::test_the_issuer_in_the_qr_identifies_the_chain`,
   `tests/unit/test_qr.py::test_the_same_table_reads_the_number_printed_in_the_text`,
   `tests/unit/test_qr.py::test_text_and_qr_agreeing_raise_the_confidence_and_an_unknown_number_changes_nothing`,
   `tests/unit/test_qr.py::test_the_pipeline_classifies_with_the_qr_and_keeps_an_unknown_issuer`
4. **Given** o total e a data do QR, **When** se conferem com os lidos, **Then** valem as regras de cima. —
   F-42 · `tests/unit/test_qr.py::test_the_qr_total_is_the_read_total_minus_the_card_balance_and_nothing_is_flagged`,
   `tests/unit/test_qr.py::test_a_balance_that_was_not_read_never_lowers_the_total_in_silence`,
   `tests/unit/test_qr.py::test_a_qr_total_above_the_read_one_can_only_be_a_misreading_so_the_qr_prevails`,
   `tests/unit/test_qr.py::test_a_deposit_credit_that_was_identified_explains_the_difference`,
   `tests/unit/test_qr.py::test_without_a_read_total_the_qr_fills_it_in_and_the_receipt_is_reviewed`,
   `tests/unit/test_qr.py::test_the_date_follows_the_qr_and_the_time_stays_from_the_receipt`,
   `tests/unit/test_qr.py::test_what_the_qr_said_reaches_the_review_and_a_confirmed_value_settles_it`,
   `tests/unit/test_qr.py::test_the_sum_of_the_lines_is_still_checked_against_the_total_the_qr_left`,
   `tests/unit/test_qr.py::test_the_printed_atcud_is_compared_only_where_the_text_is_exact`,
   `tests/golden/test_golden_qr.py::test_real_receipts_qr` (notas reais, marcador `golden_real`)
5. **Given** o mesmo documento fiscal, **When** chega outro ficheiro ou outra leitura, **Then** a chave (NIF do
   emitente, n.º do documento, ATCUD) do mesmo utilizador avisa (RF-08); outro ATCUD ou outro utilizador não; e
   guarda-se só o que se usa. — F-43 · `tests/api/test_qr.py::test_the_same_fiscal_document_is_a_duplicate_even_when_the_file_and_the_reading_differ`,
   `tests/api/test_qr.py::test_what_is_stored_is_only_what_the_checks_use`,
   `tests/api/test_qr.py::test_a_total_that_the_qr_contradicts_is_reviewed_until_the_person_settles_it`
6. **Given** um QR com o NIF de um adquirente, **When** a nota é processada, **Then** nem o texto do QR nem o
   NIF do adquirente aparecem em logs, respostas da API ou base de dados, e o que se guarda não é exposto. —
   F-44 · `tests/api/test_qr.py::test_the_qr_text_and_the_buyers_number_appear_nowhere`
7. **Given** uma nota sem QR legível, **When** é processada, **Then** o resultado é igual ao de antes. — F-45 ·
   `tests/unit/test_qr.py::test_a_receipt_without_a_qr_goes_through_exactly_as_before`

---

### User Story 3 - Parser genérico que entende a tabela (A2) (Priority: P1)

Usar a geometria (cabeçalho e colunas) e a soma das linhas para ler layouts desconhecidos; a
confiança só sobe quando as linhas somam o total (e o QR concorda). Nunca inventar valores.

**A2.1 (PDF com texto), implementado.** Cenários:

1. **Given** vários valores impressos que podem ser o total, **When** se escolhe o total, **Then** é o que
   iguala a soma das linhas; se mais de um iguala (bruto e líquido), é o que iguala o que foi pago (cartão,
   saldo de cartão, vales, crédito de depósito); sem candidato que se sustente fica sem total, nunca a soma
   calculada; um único total impresso mantém-se mesmo que uma linha esteja mal lida. — F-34 ·
   `tests/unit/test_generic_pdf.py::test_card_balance_used_as_payment_does_not_reduce_the_total`,
   `tests/unit/test_generic_pdf.py::test_gross_and_net_both_fit_the_lines_and_what_was_paid_decides`,
   `tests/unit/test_generic_pdf.py::test_card_balance_and_card_add_up_to_the_total`,
   `tests/unit/test_generic_pdf.py::test_payments_decide_when_no_printed_total_fits_the_lines`,
   `tests/unit/test_generic_pdf.py::test_a_total_that_nothing_backs_is_left_empty_never_computed`,
   `tests/unit/test_generic_pdf.py::test_a_single_printed_total_stays_when_a_line_was_misread`
2. **Given** uma descrição seguida de «qtd X preço total», títulos de secção e um cabeçalho «IVA
   DESCRICAO VALOR», **When** se lêem os artigos, **Then** a descrição e a linha seguinte são um só artigo
   (com a etiqueta de IVA), os títulos não são produtos e o cabeçalho não fecha os artigos. — F-35 ·
   `tests/unit/test_generic_pdf.py::test_description_above_its_quantity_and_prices_is_one_item_and_titles_are_skipped`,
   `tests/unit/test_generic_pdf.py::test_a_column_header_does_not_end_the_items`
3. **Given** uma poupança sem sinal, **When** os preços vêm já líquidos, **Then** somam-se de volta ao
   preço e ficam como desconto da linha; **When** vêm brutos, **Then** ficam como impressos; **When** a
   poupança vem antes do artigo, **Then** pertence a esse artigo; **When** nenhuma leitura chega ao total,
   **Then** as linhas ficam como impressas. — F-36 ·
   `tests/unit/test_generic_pdf.py::test_prices_printed_net_of_the_saving_are_brought_to_gross_and_discount`,
   `tests/unit/test_generic_pdf.py::test_prices_printed_gross_keep_the_shelf_price_and_the_discount`,
   `tests/unit/test_generic_pdf.py::test_a_saving_printed_before_its_item_belongs_to_that_item`,
   `tests/unit/test_generic_pdf.py::test_lines_are_not_rewritten_when_no_reading_reaches_the_total`
4. **Given** um PDF com texto e um cabeçalho de colunas, **When** o cabeçalho está em várias linhas, usa
   outras palavras ou outra ordem, **Then** as colunas dão quantidade, preço, imposto e total; uma linha que
   a tabela não lê fica por resolver; sem cabeçalho lê-se pela forma da linha. — F-37 ·
   `tests/unit/test_generic_pdf.py::test_header_on_several_rows_gives_the_columns`,
   `tests/unit/test_generic_pdf.py::test_other_header_words_and_column_order`,
   `tests/unit/test_generic_pdf.py::test_a_row_the_table_cannot_read_is_kept_for_review`,
   `tests/unit/test_generic_pdf.py::test_without_a_header_the_lines_are_read_by_their_shape`
5. **Given** cinco PDF sintéticos de lojas inventadas (mutações dos formatos reais: colunas noutra ordem e
   cabeçalho em duas linhas, poupança antes ou depois da linha, sem etiqueta de IVA, saldo de cartão, vale,
   sem cabeçalho), **When** correm no golden normal, **Then** não há falhas e a baseline do `UNKNOWN`
   passa a cobri-los. — F-34, F-35, F-36, F-37 · `tests/golden/test_golden.py::test_synthetic_golden_set_without_ocr_has_no_regressions`

*A2.2 a A2.5 (foto/OCR, confiança conquistada, pesquisa de combinações, cabeçalho e rodapé): por escrever.*

---

### User Story 4 - LLM local só nas linhas que sobrarem (A3) (Priority: P2)

Só passa a ser recomendado se a medição mostrar linhas recuperadas sem aumentar as linhas erradas
aceitas e com p95 ≤ 60 s na máquina-alvo.

*Cenários por escrever.*

---

### User Story 5 - Aprender com as correções do estabelecimento (A4) (Priority: P2)

A segunda nota do mesmo estabelecimento exige menos correções que a primeira. O perfil guarda só
parâmetros de layout, nunca texto da nota, e desfaz-se após duas falhas seguidas.

*Cenários por escrever.*

---

### User Story 6 - Revisão fácil para lojas novas (A5) e cobertura (A6) (Priority: P2)

Mensagem de primeira nota de um lugar, confirmar o nome do estabelecimento, corrigir, adicionar e
remover linhas com uma mão, selo "os totais batem", opção "só registar o total".

*Cenários por escrever; matriz de cobertura em `coverage.md`.*

---

### User Story 7 - Checagem de descrição e alerta de unidade (D1) (Priority: P1)

Uma foto lida por OCR não se confirma sozinha enquanto a descrição não for checada, e uma quantidade como
"4,000" gera a pergunta "unidades ou kg?". Item novo, que o plano original não tinha.

**Alerta de unidade (D1b, FR-010), implementado no parser genérico.** Cenários:

1. **Given** uma linha com a quantidade impressa com três decimais e valor inteiro («QUEQUE 4,000 X 0,69»),
   **When** o genérico a lê, **Then** a leitura mantém-se (4 × 0,69) mas a unidade fica em dúvida (confiança
   0,5, abaixo de qualquer limiar) e a linha vai para revisão; quando só a unidade está em dúvida a razão
   pergunta «unidades ou kg?». — F-38 ·
   `tests/unit/test_unit_alert.py::test_whole_value_printed_like_a_weight_asks_units_or_kg`,
   `tests/unit/test_unit_alert.py::test_confirming_the_unit_clears_the_alert_and_other_edits_do_not`
2. **Given** um peso real («1,004»), uma unidade impressa («1,000 kg», «0,298 kg x …/kg») ou uma contagem
   («3 X …»), **When** o genérico a lê, **Then** não há alerta. — F-38 ·
   `tests/unit/test_unit_alert.py::test_a_real_weight_or_a_printed_unit_or_a_count_does_not_ask`
3. **Given** a forma de duas linhas, a linha de quantidade solta ou uma coluna de quantidade, **When** a
   quantidade é «4,000», **Then** há o mesmo alerta. — F-38 ·
   `tests/unit/test_unit_alert.py::test_the_alert_also_covers_the_two_line_shape_and_a_quantity_line`,
   `tests/unit/test_unit_alert.py::test_the_alert_also_covers_a_quantity_column`
4. **Given** o alerta, **When** a pessoa confirma a unidade, **Then** o alerta desaparece; editar só a
   quantidade não o limpa. — F-38 ·
   `tests/unit/test_unit_alert.py::test_confirming_the_unit_clears_the_alert_and_other_edits_do_not`

5. **Given** uma nota lida por OCR (foto, captura ou PDF digitalizado), mesmo com confiança alta e totais
   conciliados, **When** é processada, **Then** vai para revisão com o motivo `photo_not_verified` e a pessoa
   pode confirmá-la; um PDF de texto de cadeia calibrada continua a poder confirmar-se sozinho. **Regra
   provisória, decidida em 2026-10-30: sai com a checagem de descrição (D1a).** — F-55 ·
   `tests/unit/test_confidence.py::test_a_note_read_by_ocr_is_never_auto_confirmable`,
   `tests/unit/test_confidence.py::test_a_note_not_read_by_ocr_keeps_its_automatic_confirmation`,
   `tests/api/test_receipts.py::test_image_goes_through_local_ocr_and_waits_for_a_person`

Âmbito: só o parser genérico (e os calibrados que o herdam: Aldi, Mercadona, Corte Inglês). Pingo Doce,
Continente, Lidl e Auchan têm parser próprio e não alertam; estender o alerta a eles é decisão por tomar.
A checagem de descrição (D1a) fica para depois do A2.2.

### Edge Cases

- Nota sem `.expected.json`: aparece em `unlabelled` e num aviso, não é medida.
- Em modo `--unseen` o `status` e os códigos de revisão esperados não se verificam: descrevem o
  caminho calibrado e o teto de 0,6 manda tudo para revisão por desenho.
- `unreviewed_wrong` é 0 por desenho enquanto a confiança não puder ser conquistada (A2.3).
- O primeiro documento com OCR inclui o carregamento dos modelos: o tempo máximo está inflacionado.
- Uma nota paga em parte com saldo do cartão (ex.: Continente com "TOTAL A PAGAR 0,00") tem o total
  esperado definido como o subtotal: é a regra FR-007, não um facto impresso no documento.
- O modo `--force-unknown` dá hoje resultados idênticos ao `--unseen`: a avaliação usa os mesmos limiares
  para todas as lojas e não tem fallback externo, logo só mudariam os códigos de revisão, que este modo
  não verifica. Mantém-se o modo e o teste; não tem baseline própria.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A avaliação MUST poder forçar o parser genérico (`--unseen`) e também a classificação
  `UNKNOWN` (`--force-unknown`) sem alterar o comportamento de produção.
- **FR-002**: O relatório MUST separar por loja, por rota de processamento e total, e MUST indicar
  quantas notas há em cada linha (N).
- **FR-003**: As correções por nota MUST contar edições (linha mal lida, em falta, a mais, total,
  data), não campos.
- **FR-004**: Documentos sem verdade de referência MUST ser avisados, nunca ignorados em silêncio.
- **FR-005**: O conjunto cego (`_blind/`) MUST ficar fora das corridas normais.
- **FR-006**: Os recibos reais e os `.expected.json` reais MUST ficar fora do git; só as métricas
  agregadas das baselines são versionadas.
- **FR-007 (total da nota)**: o total esperado é o **valor das compras depois dos descontos reais**,
  independente da forma de pagamento. Não o reduzem: saldo de cartão (Continente, "Desconto Cartão
  Utilizado"), crédito de vasilhame ou depósito (Aldi, "Valor depósito"), vales usados como pagamento nem
  crédito para compra futura. Reduz-no a poupança que baixa o preço do artigo (Pingo Doce: 8,76 → 8,16).
  Vale para o gabarito (`.expected.json`) e é o critério com que se avalia qualquer parser.
- **FR-008 (hora)**: a data e a hora esperadas são as do cabeçalho do documento fiscal. Na avaliação a
  hora compara-se com tolerância de 5 minutos, porque o talão imprime mais de um relógio (cabeçalho e
  terminal de pagamento). Os parsers não mudam.
- **FR-009 (baseline)**: a baseline regista o commit (`HEAD`), o resumo de `git status --short`, o hash de
  `git diff HEAD` e a lista de ficheiros não rastreados, para se saber que árvore foi medida. Não se criam
  refs nem commits para a guardar; para reproduzir o número exato faz-se commit antes (com `/simplify`).
- **FR-010 (alerta de unidade)**: uma quantidade impressa com três decimais e valor inteiro MUST gerar o
  alerta de unidade e enviar a linha para revisão (D1b).

### Key Entities

- **Baseline unseen** (`tests/golden/baseline_unseen.json`): `meta` (modo, data, commit, árvore de
  trabalho, ambiente, aviso de amostra pequena), `per_merchant`, `per_route`, `overall`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

Metas **propostas** (D4 adiado), medidas no conjunto de desenvolvimento e confirmadas no conjunto cego,
com layout desconhecido. São hipóteses, não resultados. Separadas por rota, porque o PDF com texto tem
texto exato e o OCR de foto não.

- **SC-001 (PDF com texto)**: recall de linhas ≥ 98 %; precisão de linhas ≥ 98 %; total correto ≥ 98 %.
- **SC-002 (foto, OCR)**: recall de linhas ≥ 90 %; precisão de linhas ≥ 95 %; total correto ≥ 98 %.
- **SC-003**: data correta ≥ 95 %.
- **SC-004**: correções por nota ≤ 3 na 1.ª nota de um lugar e ≤ 1 na 2.ª.
- **SC-005**: 0 notas erradas aceitas sem revisão em lojas desconhecidas.
- **SC-006 (cadeias)**: nº de cadeias com recall ≥ 80 %, reportado ao lado da média. Ressalva: cada cadeia
  tem N = 1 a 3 notas, por isso é um indicador de cobertura, não uma estimativa estatística.
- **SC-007**: tempo por nota p95 ≤ 60 s em 4 vCPU (a medir; as corridas locais são em Docker Desktop).

## Assumptions

- **D1 (decidido em 2026-10-25)**: regras de confirmação automática.
  (a) Nota lida por **OCR de foto** não se confirma sozinha enquanto não existir uma checagem de descrição
  (confiança do OCR na descrição e comparação com descrições já vistas). Vale para cadeias conhecidas e
  desconhecidas: a conferência de totais não vê erros de descrição (Auchan e Mercadona na medição do A0).
  (b) **Alerta de unidade**: quantidade impressa com três decimais e valor inteiro (ex.: "4,000") vai para
  revisão com a pergunta "unidades ou kg?". É barato e determinístico e apanha o caso do queque.
  (c) **PDF com texto** de cadeia calibrada pode ser confirmado automaticamente se os totais baterem e não
  houver alerta. Para loja desconhecida mantém-se: sempre revisão na 1.ª nota; a partir da 2.ª só com totais
  certos, QR coincidente (se existir) e perfil já confirmado por uma pessoa.
- **D2 (decidido)**: o perfil de layout é **global** e guarda só parâmetros de layout (posição das colunas,
  marcadores), nunca texto da nota nem dados pessoais; nasce só de notas confirmadas. Antes de lançar,
  anotar este fluxo na documentação de privacidade.
- **D3 (decidido)**: aceitar "compra só com total", claramente marcada e fora dos preços por produto.
- **D4 (adiado)**: as metas ficam como hipótese até haver 30 notas e o conjunto cego (N=12 de cadeias já
  ajustadas não dá base). Mudança já feita: metas separadas por rota e uma métrica de cadeias (SC-006).
- O NIF do adquirente nunca é guardado. Nenhum documento é enviado a API externa de QR ou OCR.

## Achados do A0 (registados, nada corrigido no A0)

Medidos sobre 12 notas reais; os números do A0 estão em `tests/golden/baseline_unseen_A0.json` (histórico,
que a verificação de regressão não usa; a corrente é `baseline_unseen.json`) e no
relatório do fluxo. Alimentam os fluxos indicados.

- **A2.1 / A2.3 (genérico em PDF)**: nos dois Continente e no Auchan o parser genérico não lê nenhuma
  linha. Nos PDF os totais erram por convenção: lê "TOTAL A PAGAR" (Continente, onde o saldo do cartão
  conta como pagamento) ou "TOTAL" antes da poupança (Pingo Doce). Falhas reais do genérico face ao FR-007.
- **A2.2 (foto, Aldi)**: o parser Aldi calibrado só acerta 74 % das linhas (15,5 correções por nota). O
  OCR junta linhas e cola o preço à descrição. É o ponto mais fraco entre as cadeias suportadas.
- **A1 (QR)**: no Corte Inglês (foto) o OCR leu 4,26 em vez de 4,25. A soma deixou de bater e a nota foi
  para revisão, como devia. O QR daria o total exato.
- **D1**: ver acima; o modo normal confirmou notas com descrição mal lida (Auchan "WHITE" lido "VHIIE",
  Mercadona, 2 descrições).
- **Limitação conhecida (A2 e passo C): unidade de "4,000 X 0,69".** A nota Pingo Doce eletrónica
  imprime "QUEQUE 4,000 X 0,69" (4 unidades compradas), com o mesmo formato dos itens ao peso
  ("0,513 X 6,19"), enquanto as unidades vêm sem decimais ("3 X 5,99"). O parser lê kg por convenção do
  formato e não distingue sozinho. A resolver depois (preço plausível por kg, dicionário de produtos ou
  pergunta ao utilizador). O gabarito mantém 4 unidades. Não se corrige no A0.
- **Tempo (A7)**: cada foto demorou entre 39 s e 71 s (média 54,5 s, máximo 71,4 s no `--unseen`; média
  54,1 s, máximo 68,2 s no modo normal), medido em Docker Desktop no Windows (16 CPUs, 8 GB), com os
  contentores do projeto parados e mais nada a correr: não é a máquina-alvo de 4 vCPU. Os PDF com texto
  demoram menos de 0,5 s. Não se otimiza no A0.
- **Perfil do OCR (só medição, 2026-10-25)**, 6 fotos, sem alterar nada no projeto:
  - A **recognição** é o custo: cerca de 0,2 s por linha de texto, em lotes de 6 (21 s dos 27 s de um tile de
    95 linhas). A detecção custa cerca de 3 s por tile de 900×2250, quase constante. O resto é cerca de 5 %.
  - O **número de núcleos não ajuda**: um tile demora 20,0 s com 1 thread, 19,1 s com 2, 19,4 s com 4,
    22,4 s com 8 e 24,4 s com 16 (`cpu_threads`); com `OMP_NUM_THREADS=16` demora 22,7 s. O Paddle põe
    `OMP_NUM_THREADS=1` ao ser importado. O tempo depende da velocidade de um núcleo e da configuração do
    Paddle, não do número de CPUs; por isso o alvo de 4 vCPU conta menos do que parecia.
  - O **código de blocos** (recorte da folha e tiles, ainda não commitado) pesa 7 % no total das 6 fotos
    (291,8 s numa chamada única, 311,0 s em tiles), com 0,2 a 0,6 s de preparação por foto. Acrescenta
    20 a 30 s às fotos pequenas e longas (Aldi, Mercadona: leem 6 Mpx em vez de 1,4) e poupa tempo às fotos
    grandes (Auchan 91 → 38 s, Corte Inglês 54 → 44 s). Lê mais texto: Aldi 136 → 221 linhas, Mercadona
    183 → 232. Na foto Lidl (900×5393, as dimensões da medição do README) passa de 31,5 s para 39,6 s; os
    cerca de 20 s do README eram de outra máquina, não do número de núcleos.
  - Suspeitos a investigar mais tarde, sem testar: `enable_mkldnn=False` (desligado por um erro do oneDNN
    no Paddle 3.3), o lote de recognição e o modelo.
- **Ajuste do OCR, medição (2026-10-29)**, mesma árvore, Docker Desktop (16 CPUs, 8 GB), contentores da app
  parados, `--unseen` nas 12 notas; nada foi alterado no projeto (o ajuste foi feito só numa cópia efémera):
  - **`enable_mkldnn=True` é a alavanca.** Falha no Paddle 3.3.1 com o erro `ConvertPirAttribute2RuntimeAttribute`,
    mas funciona no 3.0.0, 3.1.1 e 3.2.2, com texto idêntico (AUCHAN: 117 linhas, 100 % de tokens iguais).
    Com o Paddle 3.2.2: fotos de 44–73 s para **11–24 s** (média 58,8 → 16,2 s) a 16 CPUs; a 4 CPUs
    (`--cpus=4`) de 56–104 s para **17–47,5 s**. Recall, precisão, total, data e correções por nota
    **idênticos** à referência (OCR 83,7 % / 91,5 %, 6,5 correções por foto; PDF 100 %/100 %). O número de
    núcleos já conta com o oneDNN (4 CPUs: 17–47 s; 16 CPUs: 11–24 s), ao contrário da conclusão anterior.
  - **Lote de recognição: sem efeito útil.** Lote 1 parece mais rápido numa corrida, mas o `base` varia 37–58 s
    na mesma foto entre corridas, mais do que o ganho; o lote 1 e os maiores mudam 2 a 17 % do texto.
  - **Modelo de deteção `server`**: falha por falta de memória (pede ~10 GB, o Docker tem 8 GB).
    **`text_det_limit_type=max` a 1600**: perde linhas (221 → 200 no ALDI) e muda o texto. Ambos excluídos.
  - Ressalva de medição: o ambiente tem oscilação de até 20 s por foto entre corridas; só mudanças desta ordem
    (3–4×) são fiáveis.
- **Ajuste do OCR, aplicado (2026-10-29)**: `paddlepaddle==3.2.2` em `pyproject.toml` e `uv.lock` (`paddleocr` 3.4.1
  compatível), `enable_mkldnn` ligado por omissão e desligável com `BASKETIQ_OCR_ENABLE_MKLDNN=false` (testes em
  `tests/unit/test_ocr_engine_config.py`). Medição final com a imagem do worker reconstruída, contentores da app parados:
  - Fotos a 16 CPUs: 8,4–19,2 s (média 13,2 s), contra 44,1–72,8 s (média 58,8 s). Fotos a **4 CPUs**: 17,3–42,1 s
    (média 25,4 s), contra 55,8–103,7 s (média 79,6 s). **«4 CPUs» é o limite de CPUs do Docker (`--cpus=4`), não uma
    máquina de 4 núcleos**: é uma estimativa.
  - `make golden-unseen` com `--baseline`: sem regressões, métricas idênticas (recall 0,8712, precisão 0,9342, total
    e data 100 %, 3,33 correções por nota, QR 11/12). Golden sintético com e sem OCR: sem regressões.
  - Memória: pico de RSS do processo por foto entre 1,02 e 1,11 GB (6 fotos, um contentor por foto, inclui o carregamento
    dos modelos); a stack em repouso usa cerca de 640 MiB; cabe nos 7,4 GiB do Docker Desktop. Não inclui o Ollama.
  - `pip-audit`: 66 avisos em `certifi`, `filelock`, `pillow`, `pypdf` e `setuptools`, **os mesmos** com o Paddle 3.3.1;
    a descida não trouxe nenhum aviso novo (nenhum é do Paddle).
- **[NEEDS CLARIFICATION] Meta de tempo para foto**: o alvo de 60 s fica válido para PDF com texto. Para
  foto fica uma meta provisória (hoje 39 a 71 s; 17 a 47,5 s a 4 CPUs com o ajuste proposto, por aplicar) até haver otimização do OCR, com indicador de progresso na
  interface enquanto a nota é lida.
- **Ordem do escopo (decidida em 2026-10-25)**: A2.1 → A1 → investigação e ajuste do OCR (`enable_mkldnn`,
  lote de recognição, modelo) → A2.2. A otimização do OCR vem antes do A2.2 porque muda o texto lido, e o
  parser de foto é calibrado sobre esse texto.

## Melhorias futuras (registadas, não implementadas)

- **Total sem linhas legíveis (A2.3)**: quando não há linhas legíveis e há vários candidatos a total, usar a
  soma do que foi pago (cartão + saldo de cartão + vales + crédito de depósito) para escolher o candidato,
  porque não depende das linhas. Ex.: Continente 2, 3,17 no cartão + 7,39 de saldo = 10,56. **Estado**:
  `choose_total` já faz isto quando nenhum candidato bate com as linhas, incluindo o caso sem linhas
  resolvidas (`tests/unit/test_generic_pdf.py::test_payments_decide_when_there_are_no_readable_lines`). O que
  continua por fazer é o caso em que só parte das linhas se lê e a soma dessas linhas coincide por acaso com
  um candidato errado: aí o que foi pago deveria prevalecer. Não implementado.

## Notas para o passo C (preço por unidade)

O "preço pago por unidade" usado nas comparações é sempre o **líquido**:

- Continente imprime a linha já líquida ("2 X 2,99 = 5,98") e a poupança (4,00) só informativa. O gabarito
  do Continente 2 guarda 4,99 por unidade e `line_total` 9,98 com `discount` 4,00: valores deduzidos
  (preço bruto e total bruto), não impressos. O preço líquido é 2,99.
- Pingo Doce imprime o preço de prateleira e a poupança à parte: preço líquido = (bruto − poupança) / quantidade.
- Os números do fluxo A0 vêm de poucas notas, todas de cadeias calibradas, com parsers ajustados a
  essas mesmas notas: servem de controlo da mecânica, não de prova de leitura de lojas novas.
