# Feature Specification: Entrar com passkey (digital, rosto ou PIN do telemóvel)

**Feature Branch**: `feature/passkeys`

**Created**: 2026-11-04

**Status**: Implemented

**Input**: Especificação Funcional RF-01 (alterado em 04/11/2026), §8; Especificação Técnica §8; Plano de Testes F-03a e faixa das passkeys (§5.1). Frontend: `../basketIQ-frontend/specs/016-passkeys`.

**Convenção de rastreabilidade**: cada critério cita um F-xx da faixa F-206 a F-215 e o teste que o verifica.

## Clarifications

### Session 2026-11-04

Decisões do Douglas, 2026-11-04.

- Q: A passkey cria contas? → A: **Não**. A conta cria-se e recupera-se sempre com Google. A passkey só se regista numa sessão já aberta e é um método alternativo de entrada.
- Q: Onde se oferece o registo? → A: num cartão em Definições › Conta e numa oferta única no Início. A dispensa da oferta guarda-se no servidor (`passkey_offer_dismissed_at`), porque o frontend não usa armazenamento local.
- Q: Muda a política de privacidade? → A: **Sim**. A chave pública e as datas são dados novos (RF-13): nova versão `2026-11-04`, com novo consentimento.
- Q: Que plataformas? → A: Android e iPhone, no browser e na PWA instalada. Mais tarde, apps nativas. Por isso o RP ID é um domínio real, configurável e fixo (`BASKETIQ_WEBAUTHN_RP_ID`).
- Q: Onde se guardam os challenges? → A: no Postgres, com uso único (`DELETE … RETURNING`) e validade de 5 minutos. Um cookie assinado não impede reutilizar um challenge dentro da validade.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Registar uma passkey (Priority: P1)

Quem já entrou com Google regista uma passkey neste telemóvel. O telemóvel pede a digital, o rosto ou o PIN.

**Independent Test**: um autenticador de software (chave P-256, attestation `none`) em `tests/api/test_passkeys.py`.

**Acceptance Scenarios**:

1. **Given** uma sessão com consentimento atual, **When** pede as opções e envia a credencial criada, **Then** a passkey fica guardada com a chave pública, o contador e as transportes, e é auditada (`PASSKEY_ADDED`). As opções exigem credencial descobrível e verificação do utilizador, com attestation `none`; o `user.id` é o UUID interno, nunca o e-mail. — F-206 · `test_register_a_passkey_after_signing_in`
2. **Given** sem sessão, sem header CSRF ou sem o consentimento atual, **When** pede opções, **Then** 401/403 (`csrf_failed`, `privacy_consent_required`). — F-206 · `test_registering_needs_a_session_csrf_and_the_current_consent`
3. **Given** uma passkey já registada, **When** a regista de novo, **Then** as opções excluem-na e o envio responde 409 `passkey_exists`. — F-206 · `test_the_same_passkey_cannot_be_registered_twice`
4. **Given** um registo sem verificação do utilizador, **Then** 400 `passkey_invalid`. — F-206 · `test_a_registration_without_user_verification_is_rejected`

### User Story 2 - Entrar com a passkey (Priority: P1)

A pessoa toca em «Entrar com passkey», confirma com a digital e fica com sessão, sem passar pelo Google.

**Acceptance Scenarios**:

1. **Given** uma passkey registada, **When** pede opções e envia a asserção, **Then** 204 com os mesmos cookies de sessão do login Google (`biq_at`, `biq_rt`, `biq_csrf`); `last_used_at` e `last_login_at` atualizam-se; auditoria `LOGIN` com `method="passkey"`. — F-207 · `test_sign_in_with_a_passkey`
2. **Given** o e-mail passou a constar de `BASKETIQ_ADMIN_EMAILS`, **When** entra com passkey, **Then** o papel é recalculado como no login Google. — F-207 · `test_passkey_sign_in_recomputes_the_role`
3. **Given** um challenge já usado, expirado, ausente ou de registo, **Then** 401 `passkey_invalid`. — F-208 · `test_a_challenge_is_used_only_once`, `test_an_expired_missing_or_registration_challenge_is_rejected`
4. **Given** uma asserção de outra origem, de outro RP ID ou sem verificação do utilizador, uma assinatura forjada ou um `userHandle` de outra pessoa, **Then** 401 e nenhum cookie de sessão. — F-209 · `test_an_assertion_from_another_origin_rp_or_without_verification_is_rejected`, `test_a_forged_signature_or_another_users_handle_is_rejected`
5. **Given** uma passkey desconhecida, removida, ou de uma conta inativa, **Then** 401. — F-210 · `test_unknown_or_removed_passkey_or_inactive_user_cannot_sign_in`
6. **Given** uma passkey sincronizada (iCloud Keychain, Google Password Manager), que envia sempre o contador 0, **Then** entra sempre; um contador que recua é recusado. — F-213 · `test_sign_count_zero_is_accepted_and_a_regression_is_rejected`
7. **Given** pedidos repetidos, **Then** 429 `rate_limited` com o mesmo limite do login Google. — F-214 · `test_passkey_sign_in_is_rate_limited`

### User Story 3 - Gerir as passkeys (Priority: P2)

**Acceptance Scenarios**:

1. **Given** passkeys de duas pessoas, **When** uma lista ou remove, **Then** só vê e só remove as suas; a de outra pessoa responde 404 (sem oráculo de existência). Remover é auditado (`PASSKEY_REMOVED`). — F-211 · `test_only_ones_own_passkeys_are_listed_and_removed`
2. **Given** uma conta com passkeys e um challenge pendente, **When** é eliminada, **Then** tudo desaparece em cascata. — F-212 · `test_deleting_the_account_removes_its_passkeys`
3. **Given** a oferta no Início, **When** a pessoa a dispensa, **Then** `passkey_offer_dismissed_at` grava-se uma vez e mantém-se. — F-215 · `test_dismissing_the_passkey_offer_is_kept_once`

### Edge Cases

- Os dois endpoints de entrada não exigem CSRF (ainda não há sessão). O cookie do challenge é SameSite=Lax e não segue num POST cross-site, e a assinatura cobre a origem.
- Um pedido falhado não consome o challenge (a transação é revertida). Não é um risco: sem a chave privada a asserção não passa.
- Os erros de entrada não dizem se a passkey existe: todos respondem `passkey_invalid`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: O sistema MUST registar passkeys só com sessão, CSRF e consentimento atual; a entrada com passkey MUST valer apenas para contas existentes.
- **FR-002**: O sistema MUST exigir `userVerification: required`, credenciais descobríveis e attestation `none`.
- **FR-003**: Os challenges MUST ser de uso único, guardados no servidor e válidos 5 minutos; o id vai no cookie HttpOnly `biq_webauthn` (path `/v1`).
- **FR-004**: O sistema MUST verificar origem (`BASKETIQ_FRONTEND_BASE_URL`), RP ID (`BASKETIQ_WEBAUTHN_RP_ID`, por omissão o host do frontend), assinatura, `userHandle` e contador (0 aceite).
- **FR-005**: Uma entrada com passkey MUST criar a mesma sessão do login Google e recalcular o papel.
- **FR-006**: O sistema MUST guardar apenas a chave pública, o id da credencial, o contador, as transportes e as datas; nunca biometria.
- **FR-007**: Em staging/produção a configuração MUST recusar um RP ID que seja IP, `localhost`, ou que não seja o host do frontend nem um domínio pai dele.
- **FR-008**: Os logs MUST omitir credenciais, chaves, challenges, assinaturas e `userHandle`.

### Key Entities

- **WebAuthnCredential**: utilizador, id da credencial, chave pública, contador, transportes, criação, último uso.
- **WebAuthnChallenge**: id, challenge, propósito (`register` | `login`), utilizador (só no registo), expiração.
- **UserProfile.passkey_offer_dismissed_at**: data em que a oferta do Início foi dispensada.

## Success Criteria *(mandatory)*

- **SC-001**: Os únicos métodos de entrada expostos no OpenAPI são Google e passkey (F-03a · `tests/api/test_auth.py::test_no_password_or_other_login_method_exists`).
- **SC-002**: A configuração de produção recusa um RP ID inválido (RF-01 · `test_production_requires_a_real_rp_domain`, `test_production_accepts_the_frontend_host_or_a_parent_domain`).

## Assumptions

- O frontend e a API partilham a origem (BFF), por isso a origem esperada é `BASKETIQ_FRONTEND_BASE_URL`.
- As apps nativas futuras acrescentarão as suas origens (`android:apk-key-hash:…`) quando existirem; nada é construído para elas agora.

## Identificadores de teste

| ID | Cenário |
|---|---|
| F-206 | Registar passkey numa sessão (opções, verificação, exclusão de duplicados, CSRF, consentimento) |
| F-207 | Entrar com passkey: sessão, datas, auditoria, papel recalculado |
| F-208 | Challenge de uso único: reutilizado, expirado, ausente ou de outro propósito |
| F-209 | Origem, RP ID, verificação do utilizador, assinatura e `userHandle` inválidos |
| F-210 | Passkey desconhecida ou removida, conta inativa |
| F-211 | Listar e remover só as próprias passkeys |
| F-212 | Eliminar a conta remove as passkeys |
| F-213 | Contador 0 aceite (passkeys sincronizadas); regressão recusada |
| F-214 | Limite de pedidos na entrada com passkey |
| F-215 | Dispensa da oferta do Início guardada uma vez |

**Verificado no Frontend**: F-216, F-217, F-218, F-219, F-220, F-221
