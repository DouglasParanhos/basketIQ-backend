# Feature Specification: Ingestão e upload de notas

**Feature Branch**: `002-ingestao-upload`

**Created**: 2026-10-15

**Status**: Implemented (MVP)

**Input**: Especificação Funcional RF-02…RF-05, RF-08, §4.2; Especificação Técnica §5 passos 1–2, §6;
Plano de Testes F-04…F-08, F-17, F-24.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Enviar um PDF ou uma fotografia (Priority: P1)

O utilizador escolhe PDF, imagem ou fotografa a nota; o cliente pede um upload temporário, envia o
ficheiro diretamente para o storage privado e pede o processamento com uma chave de idempotência.

**Why this priority**: é a entrada de todos os dados.

**Independent Test**: `upload-init` → POST para o storage → `process`, com storage em memória e S3 real.

**Acceptance Scenarios**:

1. **Given** um PDF válido, **When** o processamento é pedido duas vezes com a mesma
   `Idempotency-Key`, **Then** existe um único job. — F-04 ·
   `tests/api/test_receipts.py::test_valid_pdf_creates_a_single_job`, `tests/unit/test_validation.py::test_valid_pdf_and_image`
2. **Given** uma imagem válida, **When** é enviada, **Then** segue pela rota de OCR local.
   — F-05 · `tests/api/test_receipts.py::test_image_goes_through_local_ocr`
3. **Given** um upload temporário, **When** o browser envia o ficheiro, **Then** o storage aceita-o
   apenas com a chave, tipo e tamanho assinados. — RF-03, F-08 ·
   `tests/integration/test_s3_storage.py::test_presigned_post_upload_roundtrip`,
   `tests/integration/test_s3_storage.py::test_policy_enforces_size_type_and_key`
4. **Given** a captura com câmara, **When** o utilizador fotografa, **Then** a câmara abre com
   permissões tratadas e a imagem fica disponível para pré-visualizar, recortar e rodar. — F-06, RF-04

**Verificado no Frontend**: F-06

---

### User Story 2 - Rejeitar ficheiros inválidos com mensagem acionável (Priority: P1)

Tipo, extensão e tamanho são validados antes do upload; o conteúdo real (MIME por assinatura,
integridade do PDF, palavra-passe, páginas, resolução) é validado no worker.

**Independent Test**: fixtures negativas em `tests/fixtures/`.

**Acceptance Scenarios**:

1. **Given** tipo, extensão ou tamanho inválidos, **When** pede o upload, **Then** recebe 422 com
   `field_errors` e nenhum upload é criado. — F-08 ·
   `tests/api/test_receipts.py::test_invalid_type_or_size_rejected_before_upload`, `tests/unit/test_validation.py::test_size_and_page_limits`
2. **Given** um executável com extensão `.pdf`, **When** é processado, **Then** fica
   `INVALID_FORMAT/unsupported_type`. — F-08 · `tests/api/test_receipts.py::test_fake_extension_is_caught_by_content_sniffing`,
   `tests/unit/test_validation.py::test_executable_with_pdf_extension_is_rejected_by_content`,
   `tests/unit/test_validation.py::test_declared_type_must_match_content`
3. **Given** um PDF corrompido, **When** é processado, **Then** o estado indica a ação "escolha outro
   ficheiro" e não é criada nenhuma linha. — F-07, PT-AC-7 ·
   `tests/api/test_receipts.py::test_corrupted_pdf_creates_no_partial_purchase`, `tests/unit/test_validation.py::test_corrupted_pdf`
4. **Given** um PDF protegido por palavra-passe, **When** é processado, **Then** a mensagem pede uma
   versão sem proteção. — RF-03 · `tests/unit/test_validation.py::test_password_protected_pdf`
5. **Given** uma imagem HEIC, **When** é validada, **Then** é aceite; uma imagem minúscula é
   `UNREADABLE`. — RF-04, RF-05 · `tests/unit/test_validation.py::test_heic_is_accepted`,
   `tests/unit/test_validation.py::test_tiny_image_is_unreadable`

---

### User Story 3 - Uploads interrompidos e duplicados (Priority: P2)

**Acceptance Scenarios**:

1. **Given** perda de rede durante o envio, **When** o cliente pede processamento, **Then** recebe
   `upload_missing`, a compra fica `RECEIVED`, pode pedir nova URL e concluir sem duplicação.
   — F-24 · `tests/api/test_receipts.py::test_network_loss_during_upload_is_recoverable`
2. **Given** um ficheiro com o mesmo SHA-256 de uma compra existente, **When** pede o upload,
   **Then** recebe 409 `duplicate_suspected` com os dados da compra existente; com
   `allow_duplicate=true` guarda nova versão. — F-17, RF-08 ·
   `tests/api/test_receipts.py::test_duplicate_detected_before_upload`
3. **Given** uploads iniciados e nunca processados, **When** passam 24 h, **Then** são removidos
   (registo e objeto). — RF-12 · `tests/api/test_receipts.py::test_abandoned_uploads_are_cleaned`
4. **Given** um supermercado indicado antes do upload, **When** o documento não tem sinais, **Then**
   a indicação é usada; se contradiz o documento é assinalada. — RF-02 ·
   `tests/unit/test_parsers.py::test_hint_used_when_document_has_no_signals`, `tests/unit/test_parsers.py::test_hint_conflict_is_flagged`

### Edge Cases

- PDF com mais de 5 páginas → `INVALID_FORMAT/too_many_pages`.
- Ficheiro vazio → `empty_file`; imagem acima de ~60 MP → `image_too_large`.
- URL de upload expirada (10 min) → nova URL via `POST /v1/receipts/{id}/upload-url`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `POST /v1/receipts/upload-init` MUST criar a compra `RECEIVED` e devolver presigned POST
  com `content-length-range` e `Content-Type` fixos, chave sem nome de ficheiro do utilizador.
- **FR-002**: O sistema MUST aceitar PDF, JPEG, PNG e HEIC/HEIF até 15 MB (configurável) e PDF até 5 páginas.
- **FR-003**: O worker MUST validar o MIME real por assinatura e não confiar na extensão.
- **FR-004**: `POST /v1/receipts/{id}/process` MUST exigir `Idempotency-Key` e devolver sempre o mesmo job para a mesma chave.
- **FR-005**: O sistema MUST avisar duplicados por hash antes do upload e deixar a decisão ao utilizador.
- **FR-006**: Documentos inválidos MUST terminar num estado de erro com ação recomendada e sem linhas.

### Key Entities

- **Receipt**: proprietário, tipo de origem, content type, tamanho, chave do objeto, hashes declarado
  e real, estado, versão.
- **ProcessingJob**: chave de idempotência única, estado, tentativas.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: criação do upload p95 < 500 ms (medido: p95 ≈ 20 ms, `scripts/seed_perf.py --bench`).
- **SC-002**: 0 compras parciais criadas a partir de documentos inválidos.

## Assumptions

- O storage é privado e sem listagem pública (`test_download_urls_expire_and_cannot_be_tampered`).
- Localmente usa-se SeaweedFS (S3 API) porque o MinIO deixou de publicar imagens.
- [NEEDS CLARIFICATION: antivírus no upload — a especificação pede "vírus quando disponível"; não há
  motor definido.]
