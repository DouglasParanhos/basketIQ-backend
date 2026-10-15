# BasketIQ — Backend

API (FastAPI), document-processing worker (Dramatiq + PaddleOCR) and scheduler for BasketIQ:
import Portuguese supermarket receipts (PDF or photo), extract the lines with per-field confidence,
let the user review them, and serve purchase analytics.

Specification: `../Documentation/` (funcional, técnica, plano de testes). Feature specs with
acceptance criteria and their tests: `specs/` (spec-kit).

## Architecture

```
PWA (Frontend repo) ──/v1──▶ API (FastAPI, BFF cookies, Google OIDC)
                              │  presigned POST ─▶ S3-compatible storage (private)
                              │  job id ─▶ Redis (Dramatiq)
                              ▼
                     Worker: validate → PDF text | local OCR (PaddleOCR) → classify merchant
                             → deterministic parser → local LLM for ambiguous lines (optional, Agno+Ollama)
                             → external fallback (rules + budget, disabled by default) → review decision
                              ▼
                     PostgreSQL (receipts, lines, products, jobs, audit)
Scheduler: 30-day retention of originals, stuck-job recovery, abandoned uploads.
```

Key decisions (and deviations from the spec, with reasons):

- **Pipeline orchestration is plain Python** with typed stages (`src/basketiq/pipeline/orchestrator.py`).
  Agno is used for the LLM agent only. Deterministic steps stay testable without any AI runtime.
- **Analytics are SQL aggregates over confirmed receipts**, not materialised views. Measured:
  dashboard p95 47 ms with 5 000 lines and 298 ms with 50 000 lines (target < 2 s), so
  materialisation is unnecessary for the MVP. Spending = Σ(line total − discount).
- **SeaweedFS** replaces MinIO locally (MinIO stopped publishing images). Any S3 API works.
- **Sessions**: 15-minute JWT access cookie + rotating refresh token (hash stored, reuse detection
  revokes the session), CSRF double-submit header, all cookies `HttpOnly`/`SameSite=Lax`.
- Money is integer cents; quantities `NUMERIC(12,3)`; purchase times are Europe/Lisbon.

## Parsers and calibration

| Merchant | Parser | Calibrated on |
|---|---|---|
| Pingo Doce | `pingo_doce-1.0` | 2 real PDF receipts (100 % lines/totals) + synthetic text/scanned |
| Lidl | `lidl-1.0` | 1 real Lidl Plus screenshot via OCR (100 %) + synthetic; quantity/discount lines not yet seen on a real receipt (scored lower) |
| Continente, Auchan | `generic_pt-1.0` | no samples; every field capped at 0.6 → always reviewed |

Real receipts contain personal data (NIF, card digits) and are **never committed**. Keep them in
`../Receipts/<Merchant>/` with a `<name>.expected.json` next to each file and run `make golden-real
GOLDEN_REAL_DIR=../Receipts`. CI uses the synthetic, anonymised set in `tests/golden/synthetic`
(regenerate with `make fixtures`).

## Running locally (WSL / Linux)

Requirements: Docker with Compose, [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
make up                      # postgres, redis, storage, mock OIDC, migrate, api, worker, scheduler
curl localhost:8000/healthz  # API on :8000, OpenAPI docs on :8000/docs (local only)
```

Then start the frontend (`../Frontend`, `pnpm dev`) and open http://localhost:5173. The local
sign-in uses the mock OIDC server: type any user name; the account e-mail becomes
`<name>@basketiq.local` (local/test only). `admin` → `admin@basketiq.local` has the operator role by
default (`BASKETIQ_ADMIN_EMAILS`).

### Real Google sign-in

1. Google Cloud Console → APIs & Services → Credentials → *OAuth client ID* → *Web application*.
2. Authorised redirect URI: `http://localhost:5173/v1/auth/callback` (production: your HTTPS origin).
3. Put the client id/secret in `.env` (see `.env.example`), set `BASKETIQ_OIDC_ISSUER=https://accounts.google.com`
   and empty the two `BASKETIQ_OIDC_*_BASE_URL` variables, then `make up`.

Outside `local`/`test` the settings refuse any issuer other than Google, weak JWT secrets and
non-secure cookies.

### Optional local LLM

`make up-llm` starts Ollama (`qwen2.5:3b-instruct`) and enables ambiguity resolution. Proposals are
accepted only when every value appears verbatim in the receipt line and quantity × price matches.
`pytest -m llm` runs the real Agno → Ollama round trip (verified with qwen2.5:3b: a payment line is
rejected as a product; a product line is accepted only with evidence).

## Tests

```bash
make install                 # uv sync (OCR + LLM extras, dev tools)
docker compose up -d postgres redis storage
make test                    # unit + API (real PostgreSQL) + golden set, no OCR (~30 s)
make test-all                # + PaddleOCR golden cases and S3 integration
make lint typecheck security golden
```

Tests carry `@pytest.mark.req("<id>")` with the test-plan id (F-01…F-32, RF-xx, SEC-xx);
`python scripts/traceability.py` prints the requirement → test matrix.

Performance check: `make bench LINES=5000` (and `LINES=50000`).

## Configuration

All settings are environment variables prefixed `BASKETIQ_` (see `src/basketiq/config.py`).
Notable: `CONFIDENCE_THRESHOLD` (0.80), `RECONCILIATION_TOLERANCE_CENTS` (2), `RETENTION_DAYS` (30),
`EXTERNAL_FALLBACK` (`disabled`|`mock`), `EXTERNAL_MONTHLY_BUDGET_CENTS` (0), `LLM_ENABLED`.
Per-merchant thresholds/tolerance and enabling are managed by operators via `/v1/admin/merchants`.
