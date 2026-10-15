# Feature Specification: Operador / administração

**Feature Branch**: `007-operador-admin`

**Created**: 2026-10-15

**Status**: Implemented (MVP)

**Input**: Especificação Funcional §2.2, §10; Especificação Técnica §10, §11.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Acompanhar e reprocessar falhas (Priority: P1)

Um operador vê os jobs (estado, erro, rota, motor, versões, custo, durações) e reprocessa os que
falharam — sem acesso ao conteúdo das notas.

**Why this priority**: a operação do pipeline precisa de visibilidade sem expor dados pessoais.

**Independent Test**: utilizador comum gera jobs; operador consulta e reprocessa.

**Acceptance Scenarios**:

1. **Given** jobs concluídos e falhados, **When** o operador os lista, **Then** vê metadados técnicos
   sem descrições, e-mails ou valores das notas. — RF-admin ·
   `tests/api/test_admin.py::test_admin_sees_job_metadata_without_receipt_content`
2. **Given** um job `FAILED` por falha temporária, **When** o operador o reprocessa, **Then** é criado
   um novo job e a compra conclui. — RF-admin · `tests/api/test_admin.py::test_admin_reprocesses_a_failed_job`
3. **Given** um utilizador comum, **When** acede à administração, **Then** recebe 403. — SEC-authz ·
   `tests/api/test_admin.py::test_regular_user_cannot_use_admin`

---

### User Story 2 - Métricas e regras por supermercado (Priority: P2)

**Acceptance Scenarios**:

1. **Given** um período, **When** o operador pede métricas, **Then** obtém uploads por estado,
   confirmação automática, tempo mediano até confirmação, linhas sem alteração, duplicados,
   utilizadores ativos, rotas, chamadas e custo externos, uso de LLM e duração p95. — RF-admin ·
   `tests/api/test_admin.py::test_admin_metrics_and_merchant_config`
2. **Given** a configuração de um supermercado, **When** o operador sobe o limiar de um campo ou
   desativa o supermercado, **Then** as notas seguintes vão para revisão ou ficam
   `UNSUPPORTED_MERCHANT`; valores fora de [0,5; 1] são recusados. — RF-admin ·
   `tests/api/test_admin.py::test_admin_metrics_and_merchant_config`,
   `tests/unit/test_pipeline_routes.py::test_disabled_merchant_is_unsupported`
3. **Given** um e-mail configurado como operador, **When** entra, **Then** recebe o papel `admin`.
   — RF-admin · `tests/api/test_auth.py::test_admin_role_from_configuration`

### Edge Cases

- Reprocessar uma compra confirmada → 409 (`invalid_state`).
- Métricas de utilização do dashboard e filtros são expostas em `/metrics` (Prometheus), não na API.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: O papel `admin` MUST ser atribuído por configuração (`BASKETIQ_ADMIN_EMAILS`).
- **FR-002**: Endpoints `/v1/admin/*` MUST devolver apenas metadados técnicos e agregados.
- **FR-003**: Ações de operador MUST ser auditadas (`ADMIN_REPROCESS`, `MERCHANT_CONFIG_UPDATED`).
- **FR-004**: As "regras de parsing" geríveis são limiares por campo, tolerância de reconciliação e
  ativação por supermercado; as regras de parsing propriamente ditas são código versionado.

### Key Entities

- **MerchantConfig**: ativo, limiar por omissão, limiares por campo, tolerância, autor da alteração.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 0 campos de conteúdo de nota nas respostas de administração.
- **SC-002**: 100 % das chamadas externas associadas a compra, fornecedor e motivo.

## Assumptions

- [NEEDS CLARIFICATION: base legal e processo operacional para um operador aceder ao conteúdo de uma
  nota (Especificação Funcional §2.2) — não implementado.]
- Gestão de papéis por interface está fora do MVP.
