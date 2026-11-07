# Contrato: reabrir um talão validado (spec 018)

O contrato vivo é `openapi.json`.

| Método e caminho | Corpo | Resposta |
|---|---|---|
| `POST /v1/receipts/{id}/reopen` | `{"version": n}` | 200 `ReceiptOut` com `status` `REVIEW_REQUIRED` ou `COMPLETED`, `confirmed_at` e `confirmed_by` a `null`; 409 `version_conflict` (com `details.current_version`); 409 `invalid_state` (não está confirmado); 404 |

Sessão, CSRF e consentimento atual, como `POST /v1/receipts/{id}/confirm`.
