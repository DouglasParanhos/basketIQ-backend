# Feature Specification: Autenticação Google e sessão

**Feature Branch**: `001-autenticacao`

**Created**: 2026-10-15

**Status**: Implemented (MVP)

**Input**: Especificação Funcional RF-01, §4.1; Especificação Técnica §8; Plano de Testes F-01…F-03a, §9.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Entrar com Google (Priority: P1)

Uma pessoa abre a aplicação, escolhe **Entrar com Google** e chega ao dashboard com o seu perfil
criado ou recuperado. É o único método de autenticação do MVP.

**Why this priority**: sem sessão não há upload, revisão nem análises.

**Independent Test**: fluxo OIDC completo contra um fornecedor falso (discovery, JWKS, token) em
`tests/api/test_auth.py`.

**Acceptance Scenarios**:

1. **Given** um utilizador sem conta, **When** conclui o login Google, **Then** o perfil é criado
   apenas com sub, e-mail e nome, a sessão fica ativa e é redirecionado para a aplicação.
   — F-01 · `tests/api/test_auth.py::test_first_login_creates_profile_and_session`
2. **Given** um utilizador com conta, **When** entra novamente, **Then** o mesmo perfil é recuperado
   sem duplicação. — F-02 · `tests/api/test_auth.py::test_second_login_recovers_profile_without_duplicate`
3. **Given** o ecrã do Google, **When** o utilizador cancela, **Then** permanece fora da conta e a
   aplicação recebe `error=cancelled` para mostrar uma mensagem clara.
   — F-03 · `tests/api/test_auth.py::test_cancelled_login_returns_clear_error`
4. **Given** a API, **When** se procura outro método (palavra-passe, outro IdP em produção),
   **Then** não existe endpoint e a configuração recusa emissores diferentes de Google fora de
   local/teste. — F-03a, RF-01 · `tests/api/test_auth.py::test_no_password_or_other_login_method_exists`,
   `tests/api/test_auth.py::test_production_rejects_non_google_issuer`

---

### User Story 2 - Sessão segura (Priority: P1)

A sessão usa token de acesso curto (15 min) em cookie `HttpOnly`, refresh rotativo e revogável, e
proteção CSRF por header.

**Why this priority**: requisito de segurança obrigatório (Especificação Técnica §8).

**Independent Test**: testes de tokens adulterados/expirados, rotação, reutilização e logout.

**Acceptance Scenarios**:

1. **Given** um `state` ou `nonce` adulterado, ou um ID token para outra audiência, **When** o
   callback é processado, **Then** o login falha sem criar sessão. — SEC-tokens ·
   `tests/api/test_auth.py::test_state_mismatch_is_rejected`, `tests/api/test_auth.py::test_wrong_audience_or_nonce_is_rejected`
2. **Given** uma sessão, **When** o refresh é usado, **Then** o token é rodado; reapresentar o token
   antigo revoga a sessão inteira. — SEC-tokens · `tests/api/test_auth.py::test_refresh_rotates_and_detects_reuse`
3. **Given** logout, **When** um token de acesso copiado ainda válido é usado, **Then** é recusado de
   imediato. — SEC-tokens · `tests/api/test_auth.py::test_logout_revokes_immediately`
4. **Given** um token expirado ou com assinatura inválida, **When** chama a API, **Then** recebe 401
   com código distinto. — SEC-tokens · `tests/api/test_auth.py::test_expired_and_tampered_tokens`
5. **Given** um pedido mutável sem header CSRF, **When** chega à API, **Then** é rejeitado (403).
   — SEC-csrf · `tests/api/test_auth.py::test_unsafe_requests_need_csrf_header`
6. **Given** abuso do login, **When** excede o limite por endereço, **Then** recebe 429 no formato
   de erro uniforme. — SEC-ratelimit · `tests/api/test_auth.py::test_login_is_rate_limited`

---

### User Story 3 - Consentimento e papel de operador (Priority: P2)

Antes de enviar documentos o utilizador aceita a política de privacidade; e-mails configurados
recebem o papel de operador.

**Independent Test**: endpoints de compras recusam utilizador sem consentimento; papel derivado da
configuração.

**Acceptance Scenarios**:

1. **Given** um utilizador sem consentimento, **When** acede às compras, **Then** recebe
   `privacy_consent_required`; após aceitar, acede normalmente. — RF-12 ·
   `tests/api/test_receipts.py::test_privacy_consent_is_required_for_receipts`
2. **Given** um e-mail em `BASKETIQ_ADMIN_EMAILS`, **When** entra, **Then** o perfil tem papel
   `admin`. — RF-admin · `tests/api/test_auth.py::test_admin_role_from_configuration`

### Edge Cases

- Fornecedor indisponível no início do login → redireciona para `/login?error=provider_unavailable`.
- Conta Google com e-mail não verificado → login recusado (`email_not_verified`).
- Rotação de chaves do IdP → JWKS é recarregado uma vez quando o `kid` é desconhecido.
- Token de acesso expirado mas refresh válido → o cliente chama `/v1/auth/refresh`; logout funciona
  mesmo com token de acesso expirado (sessão encontrada pelo refresh).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: O sistema MUST autenticar exclusivamente com Google OIDC (código de autorização + PKCE,
  `state`, `nonce`) fora dos ambientes local/teste; localmente usa um servidor OIDC simulado pelo
  mesmo código, sem bypass.
- **FR-002**: O sistema MUST guardar apenas `sub` (prefixado pelo emissor), e-mail, nome, papel,
  datas de login e consentimento.
- **FR-003**: O sistema MUST emitir token de acesso de 15 minutos e refresh de 30 dias rotativo,
  com hash guardado e deteção de reutilização.
- **FR-004**: O sistema MUST validar a sessão em cada pedido para revogação imediata.
- **FR-005**: O sistema MUST exigir header `X-CSRF-Token` igual ao cookie `biq_csrf` em POST/PUT/PATCH/DELETE.
- **FR-006**: O sistema MUST limitar a taxa de login, criação de upload e processamento.
- **FR-007**: O sistema MUST exigir aceitação da política de privacidade antes de operações sobre compras.
- **FR-008**: Em staging/produção a configuração MUST recusar segredos JWT fracos, cookies não seguros
  e reescrita de URLs OIDC.

### Key Entities

- **User**: identificador interno, sujeito Google, e-mail, nome, papel, estado, consentimento.
- **UserSession**: hash do refresh atual e anterior, expiração, rotação, revogação.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100 % dos endpoints de dados respondem 401 sem sessão
  (`tests/api/test_receipts.py::test_endpoints_require_authentication`).
- **SC-002**: Logout invalida a sessão no pedido seguinte (0 s de janela).
- **SC-003**: Nenhum método de autenticação além de Google está exposto no contrato OpenAPI.

## Assumptions

- O cliente OAuth Google é criado pela equipa (redirect `https://<origem>/v1/auth/callback`).
- Frontend e API partilham a origem (proxy Vite em dev, reverse proxy em produção).
- [NEEDS CLARIFICATION: texto final da política de privacidade e base legal validados pelo DPO antes
  do lançamento público.]
