# Contrato da API (spec 013)

Convenções do repositório: JSON, erros `{code, message, request_id, field_errors?}`, `csrf_protect` nos métodos que alteram, sessão por cookie. «CU» = `CurrentUser`; «Consentido» = `ConsentedUser`, que passa a exigir o consentimento da **versão atual** da política (versão em falta ou diferente: 403 `privacy_consent_required`).

## Endpoints

| Método e caminho | Autenticação | Corpo / resposta | Erros |
|---|---|---|---|
| `GET /v1/me/profile` | CU | `ProfileOut`; perfil vazio se não houver linha | 401 |
| `PATCH /v1/me/profile` | Consentido | `ProfilePatch` → `ProfileOut` | 401; 403 `privacy_consent_required`; 422 validação |
| `GET /v1/merchants` | CU | `list[MerchantOut]` (7 itens, ordem fixa) | 401 |
| `GET /v1/receipts/stored-files` | Consentido | `StoredFilesOut` | 401 |
| `DELETE /v1/receipts/stored-files` | Consentido | `StoredFilesDeleted` | 401 |
| `GET /v1/config` (existente) | nenhuma | + `smart_list_enabled: bool` | — |
| `GET /v1/admin/config` (existente) | operador | + `smart_list_enabled: bool` | 401, 403 |

`GET` e `DELETE /v1/receipts/stored-files` são declarados **antes** de `/{receipt_id}` em `routers/receipts.py`.

## Esquemas

```text
ProfileOut {
  household_size: int | null              # 1..12
  merchants: list[str]                    # ids de /v1/merchants
  goals: list["finances"|"shopping_list"|"savings"]
  monthly_goal_cents: int | null          # 5000..80000
  onboarding_completed_at: datetime | null
}

ProfilePatch {                            # todos opcionais; omitido = manter
  household_size?: int | null             # StrictInt 1..12; null apaga
  merchants?: list[str]                   # só suportados; repetidos contam uma vez; [] limpa; null recusado
  goals?: list[str]                       # só os 3 valores; repetidos contam uma vez; [] limpa; null recusado
  monthly_goal_cents?: int | null         # StrictInt 5000..80000; null apaga
  onboarding_completed?: true             # só true; false e null recusados
}

MerchantOut { id: str, name: str }

StoredFilesOut     { files: int }                         # ficheiros (fotos e PDF) de compras confirmadas com original guardado
StoredFilesDeleted { files_deleted: int, receipts: int, remaining: int }   # no máximo 500 compras por chamada; remaining = compras que faltam
```

## Regras

1. `PATCH` com corpo vazio (`{}`) não altera nada e responde 200 com o perfil atual.
2. Um pedido inválido não grava nada, nem os campos válidos do mesmo pedido (a validação é do corpo inteiro, antes de gravar).
3. `onboarding_completed_at` só se preenche na primeira vez; repetir `onboarding_completed: true` mantém a data.
4. Recusa de consentimento (em todos os endpoints «Consentido»): 403 com `code = "privacy_consent_required"` e nada gravado. `GET /v1/me`, `POST /v1/me/privacy-consent`, `DELETE /v1/me`, `GET /v1/me/profile` e `GET /v1/merchants` só exigem sessão.
5. Mercados guardados que deixem de ser suportados continuam a ler-se sem erro (o `GET` do perfil não filtra).
6. `DELETE /v1/receipts/stored-files` não toca em compras por confirmar, não elimina compras nem dados estruturados, e é idempotente (sem originais: 200 com `files_deleted = 0`).
7. Nada do perfil entra em logs, erros ou auditoria.

## Auditoria

`ORIGINAL_DELETED` por compra, `reason = "user_bulk_request"`, sem nomes de ficheiros nem valores. O perfil não gera eventos (R11).
