# Research: Perfil, mercados e originais em bloco

Verificado a 2026-10-30 no código do backend. Cada ponto resolve uma dúvida do plano.

**R1. Onde guardar o perfil?**
- Decisão: tabela própria `user_profiles` (1:1 com `users`, chave primária = `user_id`, `ON DELETE CASCADE`), não colunas em `users`.
- Porquê: `users` só tem identidade e consentimento; a spec e o plano pedem separar os dados novos, que têm finalidade e política próprias (D8). Apagar a conta apaga o perfil por cascata, sem código novo em `delete_account`.
- Alternativa: colunas em `users` (rejeitada: mistura dados com finalidades diferentes).

**R2. Como representar listas e limites?**
- Decisão: `merchants` e `goals` em JSONB (como `review_reasons` em `receipts`), com `server_default '[]'`; `household_size` `SMALLINT` e `monthly_goal_cents` `BIGINT`, ambos nulos por omissão, com `CHECK` (1 a 12; 5 000 a 80 000) como segunda defesa.
- Porquê: segue o estilo dos modelos atuais; a validação principal é do Pydantic, o `CHECK` impede dados fora de limites por outro caminho.

**R3. Semântica do `PATCH`.**
- Decisão: modelo Pydantic com todos os campos opcionais; aplica-se só o que está em `model_fields_set`. Campos que admitem `null` (`household_size`, `monthly_goal_cents`): `null` apaga. Listas (`merchants`, `goals`): `null` é recusado, `[]` limpa. `onboarding_completed` é `Literal[True]`: `false` e `null` são recusados.
- Porquê: é a decisão da Liz («campo omitido = manter»); as listas com `null` não estavam na spec, ver pergunta Q4 da análise.
- Inteiros estritos: `StrictInt` recusa `400.5`, `"400"` e booleanos, como pede a spec (II).

**R4. Como se exige o consentimento da versão atual? (decidido pela Liz)**
- Achado: `consented_user` (`api/deps.py`) só verificava `privacy_accepted_at is None`; **não comparava versões**. Com a versão da política alterada, quem aceitou a anterior continuaria a passar em todos os endpoints.
- Decisão: `consented_user` compara `privacy_version` com `PRIVACY_POLICY_VERSION` e recusa com 403 `privacy_consent_required` quando falta o consentimento **ou** a versão difere (versão em falta também difere). Aplica-se a **todos** os endpoints que o usam; não há dependência nova. `GET /v1/me`, `POST /v1/me/privacy-consent`, `DELETE /v1/me` e `GET /v1/me/profile` usam `CurrentUser` e não mudam.
- Verificado: na base de desenvolvimento há 2 contas, ambas na versão atual, nenhuma com consentimento sem versão; só `accept_privacy` grava os dois campos, juntos. Os *fixtures* de teste (`login(consent=True)`) usam a versão atual.
- Consequência: quando `PRIVACY_POLICY_VERSION` subir, **todas** as contas ficam bloqueadas em compras e análises até consentirem de novo (o frontend já as encaminha para `/privacidade`). Por isso a subida é tarefa de lançamento (T033), com o texto novo.
- Não se sobe `PRIVACY_POLICY_VERSION` nesta spec; o teste do cenário US5 altera a versão no próprio teste.

**R5. Mercados: de onde vêm os nomes?**
- Achado: o backend só tem os identificadores (`Merchant`, `SUPPORTED_MERCHANTS`); os nomes para mostrar estão no frontend (`merchantName`).
- Decisão: constante `MERCHANT_NAMES` em `domain/status.py` e `GET /v1/merchants` devolve `{id, name}` dos 7 suportados, por ordem fixa, sem `UNKNOWN`. O frontend 010 passa a ler os nomes da API.
- Porquê: a spec pede «nome para mostrar»; duplicar a lista entre repositórios tem de ficar numa só (a API).

**R6. Apagar originais em bloco: volume e reutilização.**
- Achado: o apagar existe em dois sítios (`services/receipts.delete_original`, motivo `user_request`; `workers/scheduler.purge_expired_originals`, motivo `retention`, até 500 por *tick*).
- Decisão: extrair `_erase_original(db, storage, receipt, *, actor, reason)` (apaga o prefixo, marca `original_deleted_at`, limpa `original_expires_at`, `version += 1`, audita) e usá-lo nos três caminhos; o bloco usa o motivo `user_bulk_request`.
- Seleção: compras do utilizador com `processing_status = CONFIRMED` e `original_deleted_at IS NULL` (decisão da spec: as não confirmadas ficam de fora), com `FOR UPDATE`.
- Contagem de ficheiros: `1 + len(photos)` por compra.
- **Volume por chamada (decidido pela Liz):** no máximo 500 compras por chamada, como o *scheduler*; a resposta traz `files_deleted`, `receipts` e `remaining`, e o cliente repete até `remaining = 0`.

**R7. Opção da lista.**
- Decisão: `smart_list_enabled: StrictBool = Field(alias="SMART_LIST_ENABLED")` em `AppConfig` (obrigatória, sem *default*, `extra="forbid"` mantém-se), `SMART_LIST_ENABLED: false` no YAML, campo em `PublicConfigOut` **e** em `ProductConfigOut` (a vista do operador mostra cada chave, CFG-02). O teste `test_admin_config_shows_every_key_to_operators_only` compara o dicionário completo e tem de mudar.
- Achado: a spec 013 (US8) só fala da configuração pública; a vista do operador fica de fora (análise H2).

**R8. Rotas de originais em bloco.**
- Decisão: `GET` e `DELETE /stored-files` declarados **antes** de `/{receipt_id}` em `routers/receipts.py` (o parâmetro é `uuid.UUID`: se viesse depois, `stored-files` daria 422 em vez de chegar à rota). Distinto de `/{receipt_id}/original`.
- Verificação: teste que chama as rotas e confirma que não são tratadas como identificador.

**R9. Criar o perfil sem corridas.**
- Decisão: `INSERT … ON CONFLICT DO NOTHING` seguido de `SELECT … FOR UPDATE` do perfil; o `PATCH` aplica-se sobre a linha bloqueada. Dois `PATCH` simultâneos do mesmo utilizador não criam duas linhas nem perdem campos.

**R10. Migrações (duas).**
- Decisão: `alembic/versions/20261031_e5a8b3d1f2c7_user_profiles.py`, `down_revision = "d4f7a2c8e1b6"` (a revisão anterior), `upgrade` cria a tabela e os `CHECK`, `downgrade` apaga-a. O CI já corre upgrade, downgrade total e upgrade.
- Migração de dados (decisão 17 do plano; Liz, 2026-10-31): `20261031_f6b9c4e2a3d8_existing_accounts_onboarding_done.py`, `down_revision = "e5a8b3d1f2c7"`, sem alterar a primeira. Um só `INSERT … SELECT … ON CONFLICT DO UPDATE` cria o perfil vazio das contas com `privacy_accepted_at` preenchido e preenche `onboarding_completed_at` (com `now()`) nos perfis que o têm nulo; nunca altera um perfil já concluído nem grava outro valor. Contas sem `privacy_accepted_at` ficam sem perfil: não chegaram à aplicação e fazem o onboarding como contas novas.
- `downgrade` da segunda é vazio: uma linha criada pela migração não se distingue de uma concluída pela pessoa, e o `downgrade` da primeira apaga a tabela. O teste `F-118` (`tests/integration/test_migration_existing_accounts.py`) usa uma base descartável e dados sintéticos.
- Aplicar à base de desenvolvimento é um passo manual da Liz (T032), **só quando o frontend 014 (Perfil) estiver pronto**: quem é marcado sem meta só a define no Perfil e não há forma provisória de a definir.

**R11. Auditoria e logs.**
- Decisão (confirmada pela Liz): sem evento de auditoria para alterações do perfil; logs e erros nunca incluem pessoas, mercados nem meta (teste com o *middleware* de redação existente).

**R12. Identificadores de teste.**
- Decisão (Liz): partilhar os F-xx com o frontend pela convenção «Verificado no Frontend» e não alargar a faixa. Esta spec usa F-100 a F-112 (tabela na `spec.md`); o isolamento usa `PT-AC-9` e `SEC-authz`.
