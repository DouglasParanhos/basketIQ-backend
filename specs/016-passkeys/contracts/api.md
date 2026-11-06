# Contrato: passkeys (spec 016)

O contrato vivo é `openapi.json`. Corpos de credencial no formato JSON do WebAuthn (o de `@simplewebauthn/browser`: `id`, `rawId`, `type`, `response`, `clientExtensionResults`, campos binários em base64url).

## Registo (sessão + CSRF)

| Método e caminho | Quem | Resposta |
|---|---|---|
| `GET /v1/me/passkeys` | sessão | 200 `[{id, created_at, last_used_at}]` |
| `POST /v1/me/passkeys/options` | sessão + consentimento atual | 200 `PublicKeyCredentialCreationOptionsJSON`; cookie `biq_webauthn` |
| `POST /v1/me/passkeys` | sessão + consentimento atual | 201 `{id, created_at, last_used_at}`; 400 `passkey_invalid`; 409 `passkey_exists` |
| `DELETE /v1/me/passkeys/{id}` | sessão | 204; 404 `not_found` |
| `PATCH /v1/me/profile` `{"passkey_offer_dismissed": true}` | sessão + consentimento atual | 200 perfil com `passkey_offer_dismissed_at` |

## Entrada (sem sessão, sem CSRF)

| Método e caminho | Resposta |
|---|---|
| `POST /v1/auth/passkey/options` | 200 `PublicKeyCredentialRequestOptionsJSON` (sem `allowCredentials`); cookie `biq_webauthn`; 429 |
| `POST /v1/auth/passkey/verify` | 204 com `biq_at`, `biq_rt`, `biq_csrf`; 401 `passkey_invalid`; 429 |

O cookie `biq_webauthn` é HttpOnly, SameSite=Lax, path `/v1`, 300 s, e apaga-se quando o challenge é usado com sucesso.
