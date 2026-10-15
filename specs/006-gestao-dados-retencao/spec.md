# Feature Specification: Gestão de dados, retenção e privacidade

**Feature Branch**: `006-gestao-dados-retencao`

**Created**: 2026-10-15

**Status**: Implemented (MVP)

**Input**: Especificação Funcional RF-12, §7; Especificação Técnica §5 passo 9, §8; Plano de Testes
F-22…F-23, §9.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Eliminar compras e originais (Priority: P1)

**Why this priority**: minimização de dados e controlo do utilizador são obrigatórios (RGPD).

**Independent Test**: eliminação via API e job de retenção com relógio simulado.

**Acceptance Scenarios**:

1. **Given** uma compra confirmada, **When** é eliminada, **Then** sai dos agregados e o original e
   derivados são removidos do storage. — F-22, PT-AC-8 ·
   `tests/api/test_receipts.py::test_delete_receipt_removes_it_from_aggregates`
2. **Given** uma compra, **When** elimina só o original, **Then** o objeto é removido, os dados
   estruturados e agregados permanecem e o pedido do original responde 410. — F-22a ·
   `tests/api/test_receipts.py::test_delete_only_original_keeps_structured_data`
3. **Given** uma compra confirmada há mais de 30 dias, **When** corre o job de retenção, **Then** o
   original é removido, a compra mantém-se e fica auditado com motivo `retention`. — F-22b ·
   `tests/api/test_receipts.py::test_retention_job_purges_originals_after_30_days`
4. **Given** a lista de compras, **When** é paginada e filtrada por estado e supermercado, **Then**
   devolve apenas as compras do utilizador. — RF-12 · `tests/api/test_receipts.py::test_list_pagination_and_filters`

---

### User Story 2 - Eliminar a conta (Priority: P1)

**Acceptance Scenarios**:

1. **Given** uma conta com compras, **When** pede a eliminação, **Then** originais, compras, produtos
   e aliases próprios e sessões são removidos e a auditoria fica pseudonimizada. — F-23 ·
   `tests/api/test_receipts.py::test_account_deletion_removes_everything`

---

### User Story 3 - Isolamento e segurança dos dados (Priority: P1)

**Acceptance Scenarios**:

1. **Given** o utilizador B, **When** tenta ler, alterar, confirmar, eliminar ou processar compras ou
   produtos de A, **Then** recebe 404 (sem oráculo de existência). — PT-AC-9, SEC-authz ·
   `tests/api/test_receipts.py::test_user_cannot_read_change_or_delete_another_users_data`,
   `tests/api/test_receipts.py::test_endpoints_require_authentication`
2. **Given** URLs de documentos, **When** expiram ou são adulteradas, **Then** são recusadas e não há
   listagem pública. — SEC-urls · `tests/integration/test_s3_storage.py::test_download_urls_expire_and_cannot_be_tampered`
3. **Given** injeção em filtros e pesquisa, **When** chega à API, **Then** é inerte ou rejeitada por
   validação. — SEC-injection · `tests/api/test_receipts.py::test_injection_in_filters_is_inert`
4. **Given** erros e logs, **When** são produzidos, **Then** não contêm e-mail, tokens nem conteúdo da
   nota e incluem `request_id`. — SEC-logs · `tests/unit/test_privacy_logging.py::test_redaction_removes_personal_and_receipt_content`,
   `tests/unit/test_privacy_logging.py::test_structured_logs_redact_extra_fields`,
   `tests/api/test_receipts.py::test_errors_do_not_leak_content`

### Edge Cases

- Original eliminado → reprocessamento recusado (`original_deleted`).
- Storage indisponível durante a retenção → o tick seguinte repete (tarefa idempotente).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `DELETE /v1/receipts/{id}` MUST remover compra, linhas e todos os objetos com o prefixo da compra.
- **FR-002**: `DELETE /v1/receipts/{id}/original` MUST remover só os objetos, preservando dados estruturados.
- **FR-003**: O scheduler MUST eliminar originais quando `original_expires_at` passa (30 dias após confirmação).
- **FR-004**: `DELETE /v1/me` MUST eliminar todos os dados da conta e pseudonimizar a auditoria.
- **FR-005**: Todas as consultas MUST filtrar pelo proprietário.

### Key Entities

- **AuditEvent**: tipo, ator, ids, payload sem conteúdo nem dados pessoais.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 0 objetos remanescentes após eliminação de compra ou conta.
- **SC-002**: 100 % dos originais expirados removidos no tick seguinte do scheduler.

## Assumptions

- [NEEDS CLARIFICATION: validação formal RGPD — base legal, subcontratantes, localização UE e prazo de
  30 dias — antes do lançamento público.]
- [NEEDS CLARIFICATION: exportação de dados do titular (direito de portabilidade) — CSV/Excel fora do
  MVP; formato a definir.]
- Backups e teste de restauração pertencem à infraestrutura de produção (não cobertos aqui).
