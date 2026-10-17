# Feature Specification: Configuração do produto (nome para o cliente e feature flags)

**Feature Branch**: `008-configuracao`

**Created**: 2026-10-17

**Status**: Implemented

**Input**: Decisão do cliente: um ficheiro único com as decisões de configuração (valores e feature
flags); o código, os testes e a documentação refletem-no.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Uma única fonte para as decisões de produto (Priority: P1)

A equipa edita `config/app-config.yaml` e o sistema inteiro passa a refletir o valor, sem procurar
variáveis de ambiente espalhadas.

**Why this priority**: antes, o nome estava em quatro sítios do frontend e a flag do LLM numa variável
de ambiente repetida em compose, Makefile e documentação.

**Independent Test**: carregar o ficheiro do repositório e ficheiros inválidos.

**Acceptance Scenarios**:

1. **Given** o ficheiro do repositório, **When** é carregado, **Then** `APP_NAME_FOR_CUSTOMER` é
   `Super Mercado` e `LLM_ENABLED` é `false`. — CFG-01 ·
   `tests/unit/test_app_config.py::test_repository_file_holds_the_agreed_values`
2. **Given** um ficheiro com chave em falta, chave desconhecida, booleano escrito como texto, nome vazio
   ou com mais de 40 caracteres, ficheiro ilegível ou em falta, **When** a API ou o worker arranca,
   **Then** o processo falha com uma mensagem que indica a chave. — CFG-01 ·
   `tests/unit/test_app_config.py::test_invalid_file_stops_the_process_with_a_clear_message`,
   `tests/unit/test_app_config.py::test_missing_file`,
   `tests/api/test_config.py::test_api_refuses_to_start_with_an_invalid_file`
3. **Given** a variável antiga `BASKETIQ_LLM_ENABLED` no ambiente, **When** o processo arranca,
   **Then** falha a indicar que o valor passou para o ficheiro (o ficheiro é a única fonte). — CFG-01 ·
   `tests/unit/test_app_config.py::test_file_is_the_only_source_of_llm_enabled`

---

### User Story 2 - O cliente vê o nome configurado (Priority: P1)

**Acceptance Scenarios**:

1. **Given** um visitante sem sessão, **When** a aplicação pede `GET /v1/config`, **Then** recebe o nome
   do ficheiro. — CFG-02 · `tests/api/test_config.py::test_public_config_needs_no_session_and_comes_from_the_file`
2. **Given** outro valor no ficheiro, **When** a aplicação pede a configuração, **Then** o nome muda
   (não há valor fixo no backend). — CFG-02 · `tests/api/test_config.py::test_name_is_not_hard_coded`
3. **Given** a aplicação instalada como PWA, **When** o navegador lê o manifesto
   `/v1/config/manifest.webmanifest`, **Then** `name` e `short_name` são o nome configurado. — CFG-02 ·
   `tests/api/test_config.py::test_manifest_carries_the_configured_name`
4. **Given** uma flag, **When** o endpoint público responde, **Then** só devolve o nome. —
   `tests/api/test_config.py::test_public_config_does_not_expose_feature_flags`

---

### User Story 3 - Feature flag do agente LLM (Priority: P2)

**Acceptance Scenarios**:

1. **Given** `LLM_ENABLED: false`, **When** o worker constrói os seus componentes, **Then** não existe
   agente LLM; com `true` existe o agente Agno + Ollama. — CFG-03 ·
   `tests/unit/test_app_config.py::test_llm_flag_controls_whether_the_agent_exists`,
   `tests/unit/test_app_config.py::test_worker_components_follow_the_flag`
2. **Given** o operador, **When** abre a configuração, **Then** vê o valor carregado da flag. — CFG-02 ·
   `tests/api/test_config.py::test_admin_config_shows_every_key_to_operators_only`

### Edge Cases

- Alterar o ficheiro exige reiniciar a API e o worker (o ficheiro é lido uma vez no arranque; em
  Docker é montado em `/app/config`).
- Nome com espaços nas pontas é aparado.
- `short_name` do manifesto é igual ao nome; nomes com mais de ~12 caracteres podem ser truncados
  no ecrã inicial do telemóvel.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `config/app-config.yaml` MUST conter todas as chaves, sem valores por omissão no código.
- **FR-002**: Chaves desconhecidas, tipos errados ou valores inválidos MUST impedir o arranque.
- **FR-003**: Estes valores MUST ter o ficheiro como única fonte (sem override por ambiente). Segredos,
  URLs de ligação e definições de infraestrutura por ambiente continuam em variáveis `BASKETIQ_*`.
- **FR-004**: `GET /v1/config` e o manifesto MUST ser públicos e conter só valores destinados ao
  cliente. `GET /v1/admin/config` MUST ser restrito a operadores.
- **FR-005**: O frontend MUST obter o nome em runtime; o valor `Super Mercado` do frontend é só um
  fallback para antes da resposta ou para falhas.

### Key Entities

- **AppConfig**: `APP_NAME_FOR_CUSTOMER` (texto, 1 a 40 caracteres), `LLM_ENABLED` (booleano).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Mudar o nome no ficheiro e reiniciar a API muda o login, o título da página, o rodapé das
  definições e o nome da app instalada, sem alterar código.
- **SC-002**: 0 ocorrências de `BASKETIQ_LLM_ENABLED` em compose, `.env.example`, Makefile e código.

## Assumptions

- Novas decisões de produto e feature flags passam a ser chaves deste ficheiro.
- O fallback externo (`BASKETIQ_EXTERNAL_FALLBACK`, orçamento) mantém configuração por ambiente; pode
  passar a flag do ficheiro mais tarde. [NEEDS CLARIFICATION: mover o fallback externo e o orçamento
  para o ficheiro?]
- Os ícones e as cores do manifesto são constantes no backend (acopladas a `Frontend/public/icons`).
