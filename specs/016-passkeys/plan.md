# Implementation Plan: Entrar com passkey

**Branch**: `feature/passkeys` | **Date**: 2026-11-04 | **Spec**: [spec.md](./spec.md)

## Summary

WebAuthn com `webauthn` (py_webauthn 3.x, Duo Labs). A passkey liga-se a uma conta Google existente e cria a mesma sessão de cookies do login Google (`create_session` + `set_session_cookies`).

## Technical Context

- **Dependência nova**: `webauthn>=3.0` (traz `cbor2`, `asn1crypto`, `pyopenssl`).
- **Esquema**: migração `20261104_a7c2e9f4b1d6_passkeys.py`: tabelas `webauthn_credentials` e `webauthn_challenges`, coluna `user_profiles.passkey_offer_dismissed_at`. Tudo apaga em cascata com o utilizador.
- **Código**: `services/passkeys.py` (opções, verificação, challenges, listar, remover); endpoints em `api/routers/me.py` (registo) e `api/routers/auth.py` (entrada); `accounts.record_sign_in` (data, papel e auditoria `LOGIN`) partilhado pelo login Google e pela passkey; `Settings.passkey_rp_id` e validação de produção em `config.py`.
- **Política**: `PRIVACY_POLICY_VERSION = "2026-11-04"`.

## Constitution Check

- Restrição técnica de autenticação alterada (constituição 1.1.0): Google cria e recupera a conta; passkey é o método alternativo.
- IV (privacidade): só chave pública e datas; chaves de log novas em `REDACTED_KEYS`.
- VII (simplicidade): sem Redis nem tabela de sessões nova; a sessão é a mesma.

## Decisões

- Challenges no Postgres em vez de cookie assinado: uso único real.
- Sem nome do aparelho: listar por datas chega e evita guardar o User-Agent.
- Sem CSRF na entrada: justificado em spec.md (Edge Cases).
