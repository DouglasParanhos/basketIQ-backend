# Feature Specification: Revisão e confirmação

**Feature Branch**: `004-revisao-confirmacao`

**Created**: 2026-10-15

**Status**: Implemented (MVP)

**Input**: Especificação Funcional RF-07, RF-08, RF-10, §6, §9; Especificação Técnica §5 passo 9, §6
(PATCH/confirm); Plano de Testes F-10…F-17.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Corrigir e confirmar uma nota (Priority: P1)

O utilizador abre uma nota em `REVIEW_REQUIRED`, vê os campos assinalados e os motivos, corrige
linhas, data, supermercado ou total, e confirma. A compra passa a contar nos agregados uma vez.

**Why this priority**: notas abaixo do limiar só entram nas análises depois da revisão.

**Independent Test**: fluxo API com a fixture sintética `pingo_doce_mismatch`.

**Acceptance Scenarios**:

1. **Given** uma nota abaixo do limiar, **When** termina o processamento, **Then** fica em revisão com
   motivos e não entra nos agregados. — F-11, F-12, F-15b ·
   `tests/api/test_receipts.py::test_low_confidence_receipt_waits_for_review`
2. **Given** uma linha, **When** o utilizador altera a quantidade, **Then** o total da linha é
   recalculado, a linha fica marcada como manual e a correção sobrevive ao recarregamento.
   — F-13, PT-AC-6 · `tests/api/test_receipts.py::test_edit_line_is_marked_manual_and_recalculated`,
   `tests/unit/test_confidence.py::test_manual_edit_counts_as_certain`
3. **Given** a lista de linhas, **When** adiciona, remove ou divide linhas, **Then** totais e estado de
   revisão são recalculados e a numeração refeita. — F-14 · `tests/api/test_receipts.py::test_add_remove_and_split_lines`
4. **Given** o total corrigido, **When** a reconciliação passa e confirma, **Then** fica `CONFIRMED`
   por `USER`; confirmar duas vezes conta uma única vez. — F-15 ·
   `tests/api/test_receipts.py::test_fix_total_then_confirm`, `tests/api/test_receipts.py::test_confirm_counts_once`
5. **Given** uma nota de alta confiança, **When** termina o processamento, **Then** é confirmada
   automaticamente e o original expira 30 dias depois. — F-15a ·
   `tests/api/test_receipts.py::test_high_confidence_receipt_is_confirmed_automatically`
6. **Given** uma compra confirmada com correções, **When** o utilizador termina sessão e volta a
   entrar, **Then** os dados e correções persistem. — F-16, PT-AC-6 ·
   `tests/api/test_persistence.py::test_confirmed_receipt_with_corrections_survives_logout_and_login`
7. **Given** uma linha sem quantidade identificada, **When** tenta confirmar, **Then** é recusado
   (`review_incomplete`) até a quantidade ser indicada. — PT-7 ·
   `tests/api/test_receipts.py::test_pending_quantity_blocks_user_confirmation`,
   `tests/unit/test_confidence.py::test_pending_quantity_blocks_confirmation`
8. **Given** duas edições concorrentes, **When** a segunda usa uma versão antiga, **Then** recebe 409
   `version_conflict`. — SEC-concurrency · `tests/api/test_receipts.py::test_stale_version_is_rejected`

---

### User Story 2 - Supermercado e duplicados (Priority: P2)

**Acceptance Scenarios**:

1. **Given** supermercado desconhecido, **When** o utilizador escolhe um, **Then** a nota é
   reprocessada com essa indicação; pode em alternativa marcá-la como não suportada. — F-10 ·
   `tests/api/test_receipts.py::test_user_chooses_merchant_or_marks_unsupported`,
   `tests/unit/test_confidence.py::test_unknown_merchant_and_missing_date_are_blocking`
2. **Given** a mesma compra importada noutro ficheiro (mesmo supermercado, minuto e total), **When**
   é processada, **Then** fica em revisão com `possible_duplicate`; o utilizador mantém ou descarta.
   — F-17, RF-08 · `tests/api/test_receipts.py::test_attribute_duplicate_requires_decision`,
   `tests/unit/test_confidence.py::test_suspected_duplicate_prevents_auto_confirm`

---

### User Story 3 - Normalização de produtos corrigível (Priority: P2)

**Acceptance Scenarios**:

1. **Given** uma linha, **When** o utilizador dá outro nome ao produto, **Then** a descrição original
   é preservada, a escolha é lembrada para essa descrição nas próximas notas do mesmo utilizador e
   não afeta outros utilizadores. — RF-10 · `tests/api/test_receipts.py::test_product_rename_creates_user_alias`

### Edge Cases

- Editar uma nota `COMPLETED` volta a avaliá-la; confirmar exige ausência de valores em falta
  (supermercado, data, quantidade, total da linha), mas aceita divergência de totais após alerta.
- Linhas não identificadas ficam "Produto não identificado" nas análises se o utilizador as mantiver.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `PATCH /v1/receipts/{id}` MUST exigir `version` e recusar versões antigas (409).
- **FR-002**: O sistema MUST recalcular derivados: total = quantidade × preço; preço = total ÷ quantidade.
- **FR-003**: Campos editados MUST ser marcados (`manually_edited`, `edited_fields`) e tratados como certos.
- **FR-004**: `POST /v1/receipts/{id}/confirm` MUST ser idempotente e definir `original_expires_at = confirmed_at + 30 dias`.
- **FR-005**: Duplicados por atributos MUST bloquear a confirmação automática até decisão do utilizador.
- **FR-006**: Correções de produto MUST ser guardadas como alias do utilizador.

### Key Entities

- **ReceiptLine**: descrição original, produto normalizado, quantidade, unidade, preços, desconto,
  confiança por campo, evidência, estado de revisão, campos editados.
- **Product / ProductAlias**: catálogo global automático + produtos e aliases do utilizador.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: uma compra confirmada aparece exatamente uma vez nos agregados (mês e ano corretos).
- **SC-002**: 0 correções manuais perdidas após recarregar ou voltar a entrar.

## Assumptions

- A interface de revisão (destaque não só por cor, motivos legíveis) é verificada no Frontend.
