# Feature Specification: Editar um talão já validado

**Feature Branch**: `feature/editar-talao-validado`

**Created**: 2026-11-07

**Status**: Implemented

**Input**: pedido do Douglas, 2026-11-07: «O sistema deve também permitir a edição de talões que já tenham sido validados. Antes da funcionalidade de edição aparecer deve aparecer uma confirmação informando que o talão já foi validado, se o utilizador quer realmente continuar.» Especificação Funcional RF-10/RF-11 (revisão e confirmação). Frontend: `../basketIQ-frontend/specs/018-editar-talao-validado`.

**Convenção de rastreabilidade**: cada critério cita um F-xx da faixa F-236 a F-245 (Plano de Testes §5.1) e o teste que o verifica.

## Clarifications

### Session 2026-11-07

- Q: O que acontece ao estado de um talão validado quando a pessoa escolhe editá-lo? → A: **Reabre para revisão** (decisão do Douglas). Volta a `REVIEW_REQUIRED` ou `COMPLETED` (o que a avaliação ditar), sai dos gastos e das análises, e volta a contar quando a pessoa o confirmar outra vez. Assim a edição usa a revisão e as regras de confirmação que já existem (um talão sem total ou com linhas por ler não pode ser confirmado).
- Q: A confirmação («o talão já foi validado, quer continuar?») é do backend? → A: Não: é do frontend. O backend expõe só a ação de reabrir, que exige a versão atual.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reabrir um talão validado para o editar (Priority: P1)

**Independent Test**: `tests/api/test_reopen.py`, com um talão sintético revisto e confirmado.

**Acceptance Scenarios**:

1. **Given** um talão confirmado, **When** a pessoa o reabre com a versão atual, **Then** volta a ser editável (`REVIEW_REQUIRED` ou `COMPLETED`, `can_confirm` como na revisão), perde `confirmed_at` e `confirmed_by`, a versão sobe, deixa de contar no resumo e a reabertura é auditada (`RECEIPT_REOPENED`). — F-236 · `test_a_confirmed_receipt_reopens_for_review_and_leaves_the_spending`
2. **Given** um talão reaberto, **When** a pessoa o edita e o confirma outra vez, **Then** volta a contar no resumo, uma só vez, com os valores editados. — F-237 · `test_a_reopened_receipt_is_edited_and_counts_again_once_confirmed`
3. **Given** uma versão antiga, um talão que não está confirmado ou o talão de outra pessoa, **When** pede para o reabrir, **Then** 409 `version_conflict`, 409 `invalid_state` ou 404, e nada muda. — F-238 · `test_reopening_needs_the_current_version_of_a_confirmed_receipt_of_ones_own`

### Edge Cases

- O ficheiro original mantém a data de apagamento que tinha; a nova confirmação volta a marcá-la para 30 dias depois, como qualquer confirmação.
- Reabrir não volta a ler o ficheiro: os valores são os que estavam confirmados, com as correções feitas antes.

## Requirements *(mandatory)*

- **FR-001**: `POST /v1/receipts/{id}/reopen` com `{"version": n}` reabre um talão confirmado da pessoa (F-236, F-238).
- **FR-002**: Um talão reaberto só volta aos gastos quando for confirmado de novo, pelas regras de sempre (F-237).

## Success Criteria *(mandatory)*

- **SC-001**: F-236 a F-238 verdes; `make lint typecheck test traceability` verdes.
