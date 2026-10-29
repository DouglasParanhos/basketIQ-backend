# Tasks: Leitura universal, fluxo A0 (medição)

Só o A0. Os fluxos A1 a A7 não começam sem a linha de base e a confirmação do utilizador.

- [x] T001 `PipelineDeps.classifier` e `PipelineDeps.parser_factory` (valores por omissão = produção) em
  `src/basketiq/pipeline/orchestrator.py`
- [x] T002 Modos `--unseen` e `--force-unknown`, métricas por rota e `overall`, correções por nota,
  `unreviewed_wrong`, tempo médio e máximo, aviso de notas sem `.expected.json`, pasta `_blind` em
  `src/basketiq/eval/golden.py`
- [x] T003 Alvo `make golden-unseen` (modo `--unseen`; `--force-unknown` fica manual)
- [x] T004 Testes F-33 em `tests/golden/test_golden_unseen.py`; F-33 em `PLAN_IDS` de
  `scripts/traceability.py` e no Plano de Testes
- [x] T005 Conferir por amostragem 3 dos 5 `.expected.json` existentes contra o documento original
- [x] T006 Rascunhar os 7 `.expected.json` em falta a partir da leitura dos documentos (fora do git)
- [x] T007 Utilizador confirma os 7 rascunhos (confirmados em 2026-10-25; ficheiros promovidos a `.expected.json`)
- [x] T008 Medir com os contentores do projeto parados e escrever `tests/golden/baseline_unseen.json` (commit,
  hash de `git diff HEAD`, resumo de `git status --short`, ficheiros não rastreados, ambiente). Sem
  baseline para `--force-unknown` (resultado idêntico)
- [ ] T009 Apresentar os números e as decisões D1 a D4; parar
- [x] T010 Tolerância de 5 minutos na hora; resultado por nota com `time_ms` e `corrections`

## A2.1 (PDF com texto)

- [x] T011 Regra do total (FR-007): `parsers/totals.py` (soma das linhas lida de dois modos, pagamentos como
  desempate, nunca a soma calculada); F-34
- [x] T012 Linha em duas linhas, títulos de secção, cabeçalho de coluna que não fecha os artigos,
  poupança sem sinal, antes ou depois da linha; F-35
- [x] T013 Poupança bruta ou líquida decidida pelo total (`settle_savings`); F-36
- [x] T014 Tabela por colunas em PDF com texto (`parsers/table.py`): cabeçalho em várias linhas, outras
  palavras e ordem; F-37
- [x] T015 Cinco PDF sintéticos (mutações) em `tests/golden/synthetic/mutations/`, gerados por
  `scripts/make_synthetic_fixtures.py`; `baseline.json` e `baseline_ocr.json` só mudam no `UNKNOWN`
- [x] T016 Antes e depois nos 6 PDF reais e nos 5 sintéticos; regras dos parsers calibrados que ficaram
  redundantes

## Alerta de unidade (D1b) e baseline

- [x] T017 `flag_ambiguous_unit` em `parsers/base.py`, usado pelo genérico e pela tabela; `unit` entra na
  decisão de revisão (`domain/confidence.py`); F-38
- [x] T018 Guardar a baseline do A0 como `baseline_unseen_A0.json` (histórico, sem uso na verificação) e
  regenerar `baseline_unseen.json` com o código novo

## A1 (QR fiscal): implementado

- [x] T020 Dependência `zxing-cpp>=3.1` no `pyproject.toml` e `uv lock` (Apache-2.0, confirmada no pacote)
- [x] T021 `pipeline/qr.py`: leitura local (escalas original, ×2, ×3, ×0,5; 2 s no total; PDF a 150 dpi, página 1
  e depois as seguintes; nunca levanta) e interpretação tolerante da mensagem AT (descarta B ao dividir; ignora
  campos novos; só A, D, F, G, H, N, O)
- [x] T022 `pipeline/classify.py`: tabela única NIF → cadeia (os 7 NIF que já existem como regex) usada também
  pelo NIF do QR (evidência 0,95); NIF fora da tabela não classifica e fica guardado
- [x] T023 `pipeline/orchestrator.py`: estágio `qr` antes da classificação; leitor injetável em `PipelineDeps`
- [x] T024 Validação: total do QR = total da nota menos saldo/vale/crédito impressos; senão o do QR prevalece
  (fonte `qr`) com motivo de revisão e os dois valores; data do QR (preenche se falta; diverge → motivo)
- [x] T025 Migração Alembic e modelo: `issuer_nif`, `document_type`, `document_number`, `atcud`,
  `qr_total_cents`, `qr_tax_cents`, `qr_date`, `field_sources`; índice para duplicados; sem B, sem texto bruto
- [x] T026 RF-08: chave (NIF do emitente, n.º do documento, ATCUD) por utilizador em
  `find_attribute_duplicate`
- [x] T027 ATCUD impresso contra o do QR só em PDF com texto (barato): nota informativa, não bloqueia
- [x] T028 `eval/golden.py`: `qr_read`, `qr_date_ok`, `qr_total_ok` por nota e por loja
- [x] T029 Spec, Plano de Testes (F-39 a F-45) e rastreabilidade
- [x] T030 Medição final nos 12 documentos reais: 11 QR lidos, data e relação do total certas nos 11, 10 a 468 ms
  por documento; estados, correções e falhas das 12 notas idênticos aos de antes (golden normal e sintético)
