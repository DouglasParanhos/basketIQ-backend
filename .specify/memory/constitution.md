# BasketIQ Constitution

Princípios que governam a especificação, implementação e teste do BasketIQ (PWA de importação e
análise de notas de supermercado). Fontes: `Documentation/` — Especificação Funcional, Especificação
Técnica e Plano de Testes, v1.2.

## Core Principles

### I. Processamento local primeiro, por ordem de custo

O pipeline percorre sempre, por esta ordem: extração direta de texto de PDF pesquisável → OCR local
→ parser determinístico por supermercado → LLM local apenas para linhas ambíguas → fallback externo.
O fallback externo só é chamado quando uma regra explícita o justifica (qualidade OCR insuficiente,
mercado desconhecido, parser sem resultado ou pedido do utilizador) **e** o orçamento mensal o
permite. Cada job regista a rota usada, o motor, o fornecedor externo, o motivo e o custo estimado.
O caminho mais barato é escolhido sempre que produz resultado equivalente; a qualidade não é
sacrificada para evitar custos — na dúvida, o documento vai para revisão humana.

### II. Dinheiro exato

Valores monetários são inteiros em cêntimos; quantidades são decimais exatos (`NUMERIC(12,3)`).
Ponto flutuante nunca entra em cálculos financeiros. Arredondamentos são explícitos (meio para
cima, ao cêntimo, como impresso nas notas). Preços médios declaram a sua base (unidade, kg ou l) e
são ponderados pela quantidade.

### III. Nunca inventar valores

Um campo sem evidência no documento fica `null` e é assinalado para revisão. Nenhuma saída de IA é
aceite sem validação determinística (valores presentes literalmente na linha, quantidade × preço
coerente com o total). Linhas não identificadas nunca desaparecem: ficam na revisão ou como
"Produto não identificado". Diferenças de reconciliação acima da tolerância impedem a entrada
automática nos agregados. A descrição original da nota é sempre preservada.

### IV. Privacidade por omissão

Documentos não são enviados para serviços externos por omissão. Todas as consultas e objetos são
filtrados pelo proprietário; o acesso a dados de outra conta responde como inexistente. Logs, métricas
e auditoria não contêm imagem, texto da nota, e-mail nem tokens. O original é eliminado 30 dias após
a confirmação (ou antes, a pedido), preservando os dados estruturados; a eliminação da conta remove
tudo e pseudonimiza a auditoria. Notas reais com dados pessoais nunca são versionadas.

### V. Mobile-first e acessível

A interface é uma PWA pensada primeiro para ecrãs estreitos e uso com uma mão. Estados de
processamento e erros são compreensíveis e indicam a ação a tomar. Gráficos têm alternativa textual;
baixa confiança nunca é sinalizada apenas por cor.

### VI. Rastreabilidade

Cada critério de aceitação de uma especificação cita o identificador do Plano de Testes (F-xx, RF-xx,
SEC-xx, PT-x) e o teste que o verifica. Testes automatizados declaram o identificador
(`@pytest.mark.req("F-26")` no backend; prefixo `F-13:` nos testes do frontend). A matriz é gerada por
`scripts/traceability.py` e falha o CI se um cenário funcional do Plano de Testes ficar sem cobertura
nem delegação explícita.

### VII. Simplicidade

O mínimo de código que resolve o problema. Sem abstrações especulativas: adapters existem apenas onde
a especificação exige substituibilidade (OCR, LLM, fila, storage, fallback externo). Desvios à
especificação são documentados com a razão (README e specs).

## Restrições técnicas

- Backend Python 3.12 + FastAPI; PostgreSQL; fila Redis (Dramatiq); storage compatível com S3;
  OCR local PaddleOCR; LLM local via Agno + Ollama, opcional e desligado por omissão.
- Frontend React + TypeScript como PWA; a API é consumida na mesma origem (BFF com cookies).
- Autenticação exclusivamente Google OIDC fora dos ambientes local/teste; tokens de acesso curtos,
  refresh rotativo e revogável, CSRF por header.
- Metas: dashboard p95 < 2 s com 5 000 linhas; criação de upload p95 < 500 ms; processamento de nota
  comum p95 < 60 s.
- Limiar operacional inicial de confiança: 80 % por campo, configurável por campo e supermercado.

## Fluxo de desenvolvimento e qualidade

- spec-kit: requisito → cenário → critério de aceitação → teste (`specs/NNN-*/spec.md`).
- CI obrigatório: lint, typecheck, SAST e auditoria de dependências, migrações (upgrade/downgrade),
  testes unitários/API/integração, regressão do conjunto dourado sintético contra baseline e matriz
  de rastreabilidade.
- Qualquer alteração de parser, prompt, modelo ou schema corre o conjunto dourado e não pode regredir
  métricas por supermercado.

## Governance

Esta constituição prevalece sobre convenções locais. Alterações exigem atualização deste ficheiro com
nova versão (SemVer: MAJOR para remoção/redefinição de princípios, MINOR para novos princípios ou
secções, PATCH para clarificações) e revisão das specs afetadas. Revisões de código verificam a
conformidade; complexidade adicional tem de ser justificada.

**Version**: 1.0.0 | **Ratified**: 2026-10-15 | **Last Amended**: 2026-10-15
