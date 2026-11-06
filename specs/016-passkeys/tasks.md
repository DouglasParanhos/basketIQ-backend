# Tasks: Entrar com passkey

**Input**: [spec.md](./spec.md), [plan.md](./plan.md), [contracts/api.md](./contracts/api.md)

- [x] T001 Linha de base: `make test` verde antes de alterar (658 testes)
- [x] T002 Dependência `webauthn>=3.0` e `uv lock`
- [x] T003 Migração e modelos `WebAuthnCredential`, `WebAuthnChallenge`, `UserProfile.passkey_offer_dismissed_at`
- [x] T004 `BASKETIQ_WEBAUTHN_RP_ID`, `Settings.passkey_rp_id` e validação de produção
- [x] T005 `services/passkeys.py` e `accounts.record_sign_in`
- [x] T006 Endpoints de registo e gestão em `/v1/me/passkeys`
- [x] T007 Endpoints de entrada em `/v1/auth/passkey/*`, com limite de pedidos
- [x] T008 Dispensa da oferta em `PATCH /v1/me/profile`
- [x] T009 Chaves de log redigidas
- [x] T010 `PRIVACY_POLICY_VERSION = "2026-11-04"`
- [x] T011 `tests/api/test_passkeys.py` (F-206 a F-215) e F-03a atualizado
- [x] T012 `openapi.json`, README («Passkeys»), `.env.example`, `F_MAX = 225`
- [x] T013 `/simplify`, `make lint typecheck test traceability`, commit
