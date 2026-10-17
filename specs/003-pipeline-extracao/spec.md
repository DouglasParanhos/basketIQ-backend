# Feature Specification: Pipeline de extração

**Feature Branch**: `003-pipeline-extracao`

**Created**: 2026-10-15

**Status**: Implemented (MVP)

**Input**: Especificação Funcional RF-05, RF-06, §6, §9; Especificação Técnica §2.1, §5, §7, §11;
Plano de Testes F-09…F-12, F-25…F-32, §6, §7, §8, §13.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Extrair uma nota pelo caminho mais barato (Priority: P1)

O worker processa a nota em background pela ordem: PDF pesquisável → OCR local (PaddleOCR) →
classificação do supermercado → parser determinístico versionado → reconciliação de totais.
Sem ambiguidades, não há chamada de LLM nem de serviço externo.

**Why this priority**: é o núcleo do produto e a principal fonte de custo.

**Independent Test**: `run_pipeline` com componentes falsos (modo determinístico) e conjunto dourado.

**Acceptance Scenarios**:

1. **Given** um PDF pesquisável, **When** é processado, **Then** o texto é extraído diretamente
   (`PDF_TEXT`) sem OCR. — F-26 · `tests/unit/test_pipeline_routes.py::test_searchable_pdf_uses_text_without_ocr`,
   `tests/api/test_receipts.py::test_high_confidence_receipt_is_confirmed_automatically`
2. **Given** um PDF digitalizado, **When** é processado, **Then** o OCR local corre antes de qualquer
   fallback (`LOCAL_OCR`). — F-27 · `tests/unit/test_pipeline_routes.py::test_scanned_pdf_runs_local_ocr_before_any_external_call`,
   `tests/api/test_receipts.py::test_image_goes_through_local_ocr`
3. **Given** OCR local com confiança suficiente, **When** o parser resolve tudo, **Then** não há LLM
   nem chamada externa. — F-28 · `tests/unit/test_pipeline_routes.py::test_confident_local_ocr_resolves_without_llm_or_external`
4. **Given** uma nota de Continente, Pingo Doce, Lidl ou Auchan, **When** é classificada, **Then** o
   supermercado e a confiança ficam registados. — F-09 · `tests/unit/test_parsers.py::test_classification`
5. **Given** um supermercado não detetado, **When** termina a extração, **Then** a nota fica em
   revisão com `merchant_unknown`. — F-10 · `tests/unit/test_pipeline_routes.py::test_unknown_merchant_requires_user_choice`
6. **Given** uma nota extraída, **When** chega ao cliente, **Then** traz linhas editáveis com valores
   e confiança por campo. — F-11 · `tests/api/test_receipts.py::test_low_confidence_receipt_waits_for_review`

---

### User Story 2 - Parsers por supermercado fiéis à nota (Priority: P1)

**Acceptance Scenarios**:

1. **Given** linhas Pingo Doce com peso (`0,513 X 6,19`), várias unidades (`2 X 3,19`) e
   "Poupança Imediata", **When** são interpretadas, **Then** quantidade, unidade, preço unitário e
   desconto ficam corretos e `10X32G` não é tomado como quantidade. — RF-06 ·
   `tests/unit/test_parsers.py::test_pingo_doce_items_weight_multi_and_discount`, `tests/unit/test_parsers.py::test_pingo_doce_package_size_is_not_a_quantity`
2. **Given** linhas Lidl de quantidade, peso e desconto, **When** são interpretadas, **Then** são
   associadas ao artigo anterior. — RF-06 · `tests/unit/test_parsers.py::test_lidl_quantity_and_discount_lines`
3. **Given** uma linha com preço que nenhuma regra entende, **When** é interpretada, **Then** é
   mantida como não resolvida (nunca desaparece). — RF-06 · `tests/unit/test_parsers.py::test_unparseable_priced_line_is_kept_unresolved`
4. **Given** quantidade × preço diferente do total da linha, **When** é avaliada, **Then** os campos
   ficam abaixo do limiar. — RF-06, F-12 · `tests/unit/test_parsers.py::test_inconsistent_quantity_line_gets_low_confidence`
5. **Given** Continente ou Auchan (sem amostras), **When** são interpretados pelo parser genérico,
   **Then** todos os campos ficam limitados a 0,6 e a nota vai sempre para revisão. — RF-06 ·
   `tests/unit/test_parsers.py::test_generic_parser_never_reaches_threshold`
6. **Given** datas lidas por OCR com erro (mês 18) ou contraditórias, **When** a data é escolhida,
   **Then** datas inválidas são descartadas e a confiança reflete a corroboração; a hora é de
   Lisboa (com mudança de hora). — RF-06, PT-7 ·
   `tests/unit/test_parsers.py::test_invalid_ocr_date_is_discarded_and_sources_are_cross_checked`,
   `tests/unit/test_parsers.py::test_conflicting_single_dates_get_low_confidence`,
   `tests/unit/test_parsers.py::test_purchase_time_is_lisbon_local_time_across_dst`
7. **Given** valores em formato europeu, com ponto lido pelo OCR, negativos entre parênteses e pesos,
   **When** são convertidos, **Then** usam cêntimos inteiros e decimais exatos, arredondamento meio
   para cima. — PT-7 · `tests/unit/test_money.py::test_parse_money_cents`, `tests/unit/test_money.py::test_parse_decimal_comma_and_dot`,
   `tests/unit/test_money.py::test_multiply_cents_rounds_half_up`, `tests/unit/test_money.py::test_no_float_rounding_drift`,
   `tests/unit/test_money.py::test_to_base_unit_converts_grams_and_millilitres`

---

### User Story 3 - Confiança, reconciliação e decisão de revisão (Priority: P1)

**Acceptance Scenarios**:

1. **Given** todos os campos ≥ 80 % e totais reconciliados, **When** a decisão é tomada, **Then** a
   nota pode ser confirmada automaticamente. — F-15a · `tests/unit/test_confidence.py::test_confident_and_reconciled_is_auto_confirmable`
2. **Given** um campo abaixo do limiar, **When** é avaliado, **Then** é destacado e a revisão é pedida;
   o limiar pode variar por campo. — F-12, RF-06 · `tests/unit/test_confidence.py::test_field_below_threshold_requires_review`,
   `tests/unit/test_confidence.py::test_threshold_can_vary_per_field`
3. **Given** soma das linhas − descontos fora da tolerância (2 cêntimos) ou resumo de IVA incoerente,
   **When** é avaliado, **Then** gera `totals_mismatch` e impede a entrada automática. — F-15b, RF-06, PT-7 ·
   `tests/unit/test_pipeline_routes.py::test_totals_mismatch_blocks_auto_confirmation`,
   `tests/unit/test_confidence.py::test_totals_outside_tolerance`, `tests/unit/test_confidence.py::test_vat_summary_must_match_total`,
   `tests/unit/test_confidence.py::test_discounts_are_part_of_reconciliation`
4. **Given** uma imagem escura ou desfocada, **When** é processada, **Then** recebe aviso de qualidade
   sem bloquear; uma imagem sem texto fica `UNREADABLE`. — RF-05 ·
   `tests/unit/test_pipeline_routes.py::test_dark_image_gets_quality_warning_but_is_processed`,
   `tests/unit/test_pipeline_routes.py::test_blank_image_is_unreadable`

---

### User Story 4 - LLM local e fallback externo controlados (Priority: P2)

**Acceptance Scenarios**:

1. **Given** uma linha ambígua e `LLM_ENABLED: true` em `config/app-config.yaml`, **When** o pipeline corre, **Then** só essa linha é
   enviada (Agno + Ollama), a resposta segue o schema e só valores presentes na linha são aceites;
   campos sem evidência ficam `null`. — F-29, PT-6 ·
   `tests/unit/test_pipeline_routes.py::test_ambiguous_line_goes_to_local_llm_and_is_validated`,
   `tests/unit/test_pipeline_routes.py::test_llm_values_without_evidence_are_rejected`,
   `tests/unit/test_pipeline_routes.py::test_llm_unavailable_falls_back_to_review`
   Com `LLM_ENABLED: false` (valor por omissão) o agente não existe e nenhuma linha vai para o LLM
   (ver `specs/008-configuracao`). — CFG-03 ·
   `tests/unit/test_app_config.py::test_llm_flag_controls_whether_the_agent_exists`,
   `tests/unit/test_app_config.py::test_worker_components_follow_the_flag`
2. **Given** OCR local insuficiente, fallback ativo e orçamento disponível, **When** o pipeline corre,
   **Then** o fornecedor externo é chamado e são registados fornecedor, motivo e custo.
   — F-30 · `tests/unit/test_pipeline_routes.py::test_external_called_only_when_local_ocr_is_insufficient_and_allowed`,
   `tests/unit/test_pipeline_routes.py::test_external_disabled_by_default`
3. **Given** orçamento mensal esgotado, **When** o fallback seria necessário, **Then** não há chamada
   paga e a nota vai para revisão. — F-31 · `tests/unit/test_pipeline_routes.py::test_budget_exhausted_means_no_paid_call`
4. **Given** fornecedor externo indisponível, **When** é chamado, **Then** a nota volta à revisão
   manual sem perder o documento. — F-32 · `tests/unit/test_pipeline_routes.py::test_external_unavailable_returns_to_manual_review`

---

### User Story 5 - Resiliência do processamento (Priority: P2)

**Acceptance Scenarios**:

1. **Given** falha temporária (storage, OCR), **When** o job falha, **Then** é repetido com backoff de
   forma idempotente; mensagens duplicadas não reprocessam; esgotadas as tentativas fica
   `TEMPORARY_FAILURE` com ação; jobs presos são recolocados pelo sweeper. — F-25 ·
   `tests/api/test_receipts.py::test_interrupted_job_is_retried_idempotently`,
   `tests/api/test_receipts.py::test_duplicate_queue_message_is_harmless`,
   `tests/api/test_receipts.py::test_retries_exhausted_end_in_temporary_failure`,
   `tests/api/test_receipts.py::test_stuck_job_is_requeued_by_sweeper`,
   `tests/unit/test_pipeline_routes.py::test_ocr_crash_is_temporary`
2. **Given** o conjunto dourado sintético (com e sem OCR), **When** um parser, prompt ou modelo muda,
   **Then** nenhuma métrica por supermercado regride face à baseline. — PT-6 ·
   `tests/golden/test_golden.py::test_synthetic_golden_set_without_ocr_has_no_regressions`,
   `tests/golden/test_golden.py::test_synthetic_golden_set_with_local_ocr`, `tests/golden/test_golden.py::test_real_receipts`

### Edge Cases

- OCR divide valores (`18, 17`) ou junta palavras (`PoupanaImediata(1,40)`) → normalizado antes do parsing.
- `V. Deposito Volta` é meio de pagamento, não desconto.
- Supermercado desativado pelo operador → `UNSUPPORTED_MERCHANT`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: O pipeline MUST seguir a ordem de custo da constituição e registar `processing_route`,
  motor OCR, versão do parser, versão do modelo, fornecedor/motivo/custo externo e duração por etapa.
- **FR-002**: Cada campo extraído MUST ter confiança; a decisão de revisão MUST combinar confiança,
  regras de consistência e reconciliação; limiar inicial 0,80, configurável por campo e supermercado.
- **FR-003**: Saídas de LLM MUST ser validadas deterministicamente; sem evidência → `null` + revisão.
- **FR-004**: O fallback externo MUST estar desligado por omissão e respeitar o orçamento mensal.
- **FR-005**: Retries MUST ser idempotentes (claim atómico do job em PostgreSQL).
- **FR-006**: A orquestração é Python simples com etapas tipadas; o Agno é usado só no agente LLM
  (desvio consciente à §7 da Especificação Técnica para manter o modo determinístico trivial).

### Key Entities

- **ParsedReceipt / ParsedLine**: esquema comum produzido por cada parser, com evidência textual.
- **ProcessingJob**: rota, motores, versões, custo, durações.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: conjunto dourado — supermercado, data, total, linhas: 100 % nas amostras reais Pingo Doce
  (2 PDFs) e Lidl (1 screenshot via OCR) e no conjunto sintético.
- **SC-002**: 100 % das discrepâncias acima do limiar geram revisão.
- **SC-003**: processamento de nota comum p95 < 60 s (OCR CPU ≈ 20 s numa imagem de 1600 px).

## Assumptions

- [NEEDS CLARIFICATION: fornecedor externo de OCR/IA — apenas adapter e mock implementados; escolha
  depende de spike de custo, qualidade, latência e RGPD.]
- [NEEDS CLARIFICATION: calibração dos limiares por campo e supermercado no piloto com amostras reais.]
- [NEEDS CLARIFICATION: amostras reais de Continente e Auchan para substituir o parser genérico.]
- As linhas Lidl de quantidade/desconto ainda não foram vistas numa nota real e pontuam com fator 0,85.
