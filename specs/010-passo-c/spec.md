# Feature Specification: Passo C, grupos de produto, tamanho e comparação de preços (backend)

**Feature Branch**: `feature/passo-c`

**Created**: 2026-11-10

**Status**: Implementado (backend). Frontend: `../basketIQ-frontend/specs/020-passo-c`.

**Input**:
- Especificação Funcional v1.5, §12.1 (passo C), §12.4, RF-10, RF-11 e RF-12.
- `Documentation/RASCUNHO_decisoes_spec010_2026-10-30.md`, decisões D1 a D14, **aprovado pela Liz a 2026-11-08**.
- `Documentation/AVALIACAO_grupos_passo_C_2026-11-09.md`: catálogo em árvore, folhas novas, divisões e casos de tamanho.

**Convenção de rastreabilidade**: o passo C usa a faixa **F-56 a F-85** (Plano de Testes §5.1).
- Backend: F-56 a F-75.
- Frontend: F-76 a F-85.

**Âmbito**:
- O plano Free, só com os talões confirmados do próprio utilizador.
- Fora desta spec:
  - a base de preços da comunidade e as fontes externas (RF-17, B-33);
  - as variantes de marca e tamanho por EAN (C2);
  - os níveis de marca da AVALIACAO §5;
  - o relatório e o CSV (passo B).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Cada linha confirmada cai num grupo, ou fica «por agrupar» (Priority: P1)

O catálogo é o ficheiro `config/catalog.yaml`, versionado (D2a). Tem as 15 classes da Funcional v1.5 no topo, nós intermédios e grupos nas folhas. Só os grupos se comparam.

**Acceptance Scenarios**:

1. **Given** uma descrição que casa com as regras de um só grupo, **When** a nota está confirmada, **Then** a linha tem esse grupo (`group_source = catalog`). — F-56, D1, D2
2. **Given** cada grupo do catálogo, **When** correm os testes, **Then** tem pelo menos 3 casos «pertence» e 1 «NÃO pertence», e todos passam. Os casos são só descrições soltas. — F-56, D6
3. **Given** uma descrição que casa com dois grupos, **When** se classifica, **Then** fica «por agrupar», com os dois grupos como sugestão; nunca vale a primeira regra. — F-57, D2b
4. **Given** uma descrição que só casa com um nó acima dos grupos (por exemplo «QUEIJO FETA»), **When** se classifica, **Then** fica «por agrupar» com o nó como pista e os grupos dele como sugestão. — F-57
5. **Given** um depósito ou um saco de compras, **When** se classifica, **Then** fica «não comparável» e nunca «por agrupar». — F-57
6. **Given** uma abreviatura de uma cadeia (por exemplo «MNT» no Pingo Doce), **When** a descrição vem doutra cadeia, **Then** a expansão não se aplica. A marca nunca entra na pertença. — F-58, D5, D2c
7. **Given** um ficheiro com uma chave desconhecida, uma expressão inválida, um *slug* repetido, uma cadeia desconhecida ou um grupo sem base, **When** a API arranca, **Then** recusa-se a arrancar. — F-59
8. **Given** uma nota por confirmar, **When** se lê, **Then** nenhuma linha tem grupo nem comparação (D7). — F-62

### User Story 2 - Tamanho da embalagem (Priority: P1)

**Acceptance Scenarios**:

1. **Given** uma descrição com tamanho, **When** se extrai, **Then** o tamanho normaliza-se para kg, l, un ou uma unidade de uso (rolo, dose). Inclui multipacks N×M, contagens e dúzias. — F-60, D9
2. **Given** uma descrição cortada («ARROZ CAROLINO 1»), N×M sem unidade, dois tamanhos diferentes ou um tamanho fora dos limites do catálogo, **When** se extrai, **Then** não há tamanho. Nunca há palpite nem tamanho por omissão. — F-61, D9
3. **Given** uma linha ao peso, **When** se compara, **Then** o preço por kg é o total líquido ÷ peso. — F-75, D9, D10
4. **Given** uma linha agrupada sem tamanho, **When** se lê a nota, **Then** a linha tem grupo e a comparação diz `missing_size`. — F-75

### User Story 3 - «Acima/abaixo do que costuma pagar» (D11) (Priority: P1)

`GET /v1/receipts/{id}` passa a ter, por linha de uma nota confirmada, o campo `grouping`, com `price_check`.

**Acceptance Scenarios**:

1. **Given** pelo menos 3 compras anteriores do grupo, na mesma base, com tamanho e nos 90 dias antes da compra, **When** o preço difere da média em pelo menos 15 % **e** o efeito na linha é de pelo menos 0,20 €, **Then** a linha fica `above` ou `below`, com a média, o preço e o efeito. — F-63
2. **Given** a média, **When** se calcula, **Then** é ponderada pela quantidade, sobre o valor líquido, e exclui a compra comparada e as que estão fora da janela. — F-63
3. **Given** menos de 3 compras anteriores, **When** se lê, **Then** a linha fica `no_average`, com `purchases` e `needed`. — F-64
4. **Given** uma diferença abaixo de 15 %, ou de 15 % mas com menos de 0,20 € na linha, **When** se lê, **Then** a linha fica `normal`. — F-65

### User Story 4 - Comparação de um grupo entre cadeias (D12) (Priority: P2)

`GET /v1/groups/{slug}/comparison`.

**Acceptance Scenarios**:

1. **Given** 2 cadeias com pelo menos 3 compras cada na janela, **When** a diferença entre as médias é de pelo menos 15 % da média do grupo e de 0,20 € numa compra típica, **Then** o estado é `clear_difference`, com `typical_difference_cents`. As cadeias vêm por nome, nunca por preço. — F-66
2. **Given** nenhuma compra, uma só cadeia, cadeias com menos de 3 compras ou uma diferença pequena, **When** se pede, **Then** o estado é `no_purchases`, `single_chain`, `not_enough_purchases` (com o que falta a cada cadeia) ou `no_clear_difference`. — F-67
3. **Given** um *slug* que não é um grupo, **When** se pede, **Then** 404. — F-67

### User Story 5 - «Por completar» e regras do utilizador (D13) (Priority: P2)

`GET /v1/to-complete?tab=ungrouped|missing_size`, `GET/PUT /v1/line-rules`, `DELETE /v1/line-rules/{id}`.

**Acceptance Scenarios**:

1. **Given** linhas confirmadas «por agrupar», **When** se pede o separador, **Then** vêm as descrições com mais gasto primeiro, `TO_COMPLETE_PAGE_SIZE` de cada vez, com o total, a pista e até 5 sugestões. Não entram linhas por confirmar. — F-68
2. **Given** uma descrição, **When** a pessoa escolhe um grupo, **Then** a regra aplica-se a todas as linhas confirmadas dessa (cadeia, descrição normalizada), passadas e futuras, e devolve quantas são. Apagar a regra desfaz isto. — F-69
3. **Given** um tamanho dado pela pessoa, **When** se compara, **Then** a linha passa a ter preço por base, com a origem `user`. Limpar o único campo da regra apaga-a. — F-70
4. **Given** «não comparável», **When** se lê, **Then** a descrição sai da lista até ser desfeita. Um grupo inexistente, uma classe, um tamanho implausível ou um pedido vazio dão 422. — F-71
5. **Given** dois utilizadores, **When** um cria regras, **Then** só valem para ele, e o outro não as vê nem as apaga (404). — F-72
6. **Given** a conta eliminada, **When** se apaga, **Then** as regras vão com ela (RF-12). — F-73

### User Story 6 - Catálogo e produto (Priority: P3)

**Acceptance Scenarios**:

1. **Given** uma sessão, **When** pede `GET /v1/catalog/groups`, **Then** vêm as 15 classes com os seus grupos e bases. Sem sessão, 401. — F-74
2. **Given** um produto com compras, **When** pede a análise do produto, **Then** vem o grupo da última compra (`group`), para o ecrã pedir a comparação D12. — F-74

## Requirements *(mandatory)*

- **Calculado na hora (D14):**
  - O grupo, o tamanho e a comparação de uma linha calculam-se quando se leem, a partir da descrição confirmada, do catálogo e das regras do utilizador.
  - Nada disto se grava na nota. Por isso uma versão nova do catálogo ou uma regra nova aplica-se logo a tudo, e os totais de gasto nunca mudam.
- **Constantes de configuração** (`config/app-config.yaml`, obrigatórias): `COMPARISON_WINDOW_DAYS` (90), `COMPARISON_MIN_PURCHASES` (3), `COMPARISON_DIFFERENCE_PCT` (15), `COMPARISON_MIN_EFFECT_CENTS` (20), `COMPARISON_MIN_CHAINS` (2), `TO_COMPLETE_PAGE_SIZE` (10).
- **No catálogo:** os intervalos plausíveis de tamanho e a versão do catálogo.
- **Uma compra é um talão:** os mínimos contam talões distintos, não linhas.
- **Privacidade:**
  - Tudo é filtrado pelo dono.
  - As regras são só do utilizador, nunca alimentam o catálogo e apagam-se com a conta.
  - Nenhum valor vai para os logs.

## Identificadores de teste

| Id | Critérios | Teste |
|---|---|---|
| F-56 | US1.1, US1.2 | `tests/unit/test_catalog_rules.py` |
| F-57 | US1.3–US1.5 | `tests/unit/test_catalog_rules.py` |
| F-58 | US1.6 | `tests/unit/test_catalog_rules.py` |
| F-59 | US1.7 | `tests/unit/test_catalog_rules.py` |
| F-60, F-61 | US2.1, US2.2 | `tests/unit/test_size_rules.py` |
| F-62 | US1.8 | `tests/api/test_step_c.py` |
| F-63 a F-65 | US3 | `tests/api/test_step_c.py` |
| F-66, F-67 | US4 | `tests/api/test_step_c.py` |
| F-68 a F-73 | US5 | `tests/api/test_step_c.py` |
| F-74 | US6 | `tests/api/test_step_c.py` |
| F-75 | US2.3, US2.4 | `tests/api/test_step_c.py` |

## Decisões tomadas nesta spec (a confirmar pela Liz)

1. **Árvore:**
   - As 15 classes da v1.5 ficam como raízes da árvore da AVALIACAO.
   - Aplicaram-se as divisões claras da §4.3: leite sem lactose, batata-doce, fiambre de aves, mozzarella e queijo ralado.
   - Ficam por dividir: ovos por classe e criação, cereais e presunto.
   - O atum em lata está em Mercearia › Conservas.
2. **Marcas (D5):** só as expansões. A tabela de marcas fica para quando houver um uso (níveis de marca ou variantes).
3. **D14:** como o cálculo é feito na hora, não há a frase «N produtos foram agrupados automaticamente», que exigia guardar o estado anterior. Uma versão nova do catálogo aplica-se sem migração.
4. **D13:** as regras com «Desfazer» são um `PUT` (só os campos enviados mudam) e um `DELETE`. «Desfazer até sair do ecrã» é do cliente.
5. **Tamanho:**
   - Um número sem unidade nunca é tamanho («9X40», «FRASCO,40»).
   - «32 D» não é dose; só conta «DOSES» escrito.
   - «FOLHAS» do papel higiénico não se converte.
6. **D11, janela:** os 90 dias antes da compra comparada, contando as compras estritamente anteriores (uma nota do mesmo dia e hora não entra).

## Medição no golden set (spike US0 da spec 015, só agregados)

Resultado de `python scripts/spike_grouping.py ../Receipts` (39 notas, 476 linhas, catálogo v1):
- **191 agrupadas (40 %)**, 16 não comparáveis, 269 por agrupar, 0 conflitos entre dois grupos;
- **185 linhas com tamanho** (pesadas ou com o tamanho na descrição), das quais **112 agrupadas e comparáveis (24 %)**, com a mesma resolução da API (`services.groups.resolve`).

Confirma a AVALIACAO (41 %) e o BRAINSTORM (cerca de 36 % de linhas com tamanho). É por isso que o RF-17 prevê o catálogo externo, que traria o tamanho.

## Assumptions

- O catálogo de produtos (`products`, `product_aliases`) e a revisão não mudam. O grupo é um nível acima do produto.
- A lista de compras manual da Liz (`feat/lista-compras`) tem categorias por palavras-chave, provisórias até ao C1. Quando os dois ramos se juntarem, a categoria de um produto deve vir da classe do grupo.
