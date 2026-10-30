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
- **Profile and consent (RF-13, RF-14)**: an optional `user_profiles` row per person (household, usual merchants,
  goals, monthly goal in cents), saved with `PATCH /v1/me/profile` (an omitted field is kept, `null` clears) and
  deleted with the account. A consent counts only for the `PRIVACY_POLICY_VERSION` it was given to: with another
  version, or none, every endpoint that needs consent answers 403 `privacy_consent_required` until the person
  consents again. `GET /v1/merchants` is the only source of the merchant names.
- **Stored originals (RF-12)**: `GET` and `DELETE /v1/receipts/stored-files` count and delete the original files of
  confirmed receipts, at most 500 receipts a call (`remaining` tells when to repeat). The routes are declared
  before `/{receipt_id}`, a UUID parameter that would otherwise answer 422.

## Parsers and calibration

| Merchant | Parser | Calibrated on |
|---|---|---|
| Pingo Doce | `pingo_doce-1.0` | 2 real PDF receipts (100 % lines/totals) + synthetic text/scanned |
| Lidl | `lidl-1.0` | 1 real Lidl Plus screenshot via OCR (100 %) + synthetic; quantity/discount lines not yet seen on a real receipt (scored lower) |
| Continente | `continente-1.0` | 2 real PDF receipts (100 % lines/totals; multi-line items, "POUPANCA", card-balance payment) + synthetic |
| Auchan | `auchan-1.0` | 1 real photo via OCR (100 %: two lines, total, VAT) + synthetic; only the item/quantity/total/VAT shapes of that receipt are calibrated, other priced lines (discounts, weighed items) are kept unresolved |
| Others | `generic_pt-1.0` | unknown merchants: every field capped at 0.6 → always reviewed |

Photos are straightened before lines are rebuilt: the OCR reports the baseline angle of each text and the
long ones give the local tilt (a curled receipt tilts one way near the top and the other near the bottom),
see `pipeline/layout.py`.

Real receipts contain personal data (NIF, card digits) and are **never committed**. Keep them in
`../Receipts/<Merchant>/` with a `<name>.expected.json` next to each file and run `make golden-real
GOLDEN_REAL_DIR=../Receipts`. CI uses the synthetic, anonymised set in `tests/golden/synthetic`
(regenerate with `make fixtures`).

`make golden-unseen` reads the same real receipts as if no shop had a parser, using the generic parser
(`--unseen`). `--force-unknown` additionally makes the classifier answer UNKNOWN; today it gives results
identical to `--unseen` (the eval uses the same thresholds for every shop and no external fallback), so it has
no baseline of its own. It reports per
shop, per route (PDF text vs photo OCR) and overall: line recall and precision, total and date, corrections
per note (one edit per misread, missing or extra line), notes wrong yet not sent to review, and time. Files
without a `.expected.json` are listed as not measured; files under `Receipts/_blind/` only run with
`--blind`. Read the numbers with care: the sample is small, every receipt is from a calibrated chain and the
calibrated parsers extend the generic one, so the figures are optimistic. `unreviewed_wrong` is 0 by
design while the generic parser caps confidence at 0.6. Timings come from Docker Desktop on a developer
machine (16 CPUs, 8 GB) and the first photo includes loading the OCR models. The "4 CPUs" figures were taken
with Docker's CPU limit (`docker run --cpus=4`, a CFS quota on those 16 CPUs), not on a 4-core machine: cache,
memory bandwidth and clock speed differ, so read them as an estimate for the target machine.

### Fiscal QR code

Portuguese receipts carry a QR code with the issuer's tax number, the document number, the ATCUD, the date and
the total (Portaria 195/2020). `pipeline/qr.py` reads it locally with zxing-cpp (Apache-2.0) in a stage of the
pipeline: scales original, ×2, ×3, ×0.5 within a 2 s budget for the whole receipt, a PDF at 150 dpi page by page.
A receipt without a readable QR goes on exactly as before: reading never raises, blocks or waits longer.

It is used to classify (the issuer's tax number is strong evidence, with a single NIF → chain table in
`classify.py`), to check the date and total (the QR total is the receipt total minus the card balance, voucher or
deposit credit printed on it; a QR above the read total prevails, a QR below it never lowers the total in silence,
it sends the receipt to review with both values) and to catch duplicates (issuer + document number + ATCUD, per
user). Only what those uses need is stored (`receipts.issuer_nif`, `document_type`, `document_number`, `atcud`,
`qr_date`, `qr_total_cents`, `qr_tax_cents`): never the buyer's tax number (field B) and never the raw text, and
none of it is exposed by the API. The reader is tolerant: unknown or new fields are ignored. The specification read
was v1.0 of 2020-08-13 (a third-party copy); v1.1 was not confirmed.

## Running locally (WSL / Linux)

Requirements: Docker with Compose, [uv](https://docs.astral.sh/uv/).

On Windows without WSL the `make` targets do not run: run their commands inside containers instead
(see `CLAUDE.md` at the repository root).

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

Off by default. To use it: set `LLM_ENABLED: true` in `config/app-config.yaml`, run `make up-llm`
(starts Ollama and pulls `qwen2.5:3b-instruct`) and restart the API and worker
(`docker compose restart api worker`). Proposals are
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

Tests carry `@pytest.mark.req("<id>")` with the test-plan id (F-01…F-33, RF-xx, SEC-xx);
`python scripts/traceability.py` prints the requirement → test matrix.

Performance check: `make bench LINES=5000` (and `LINES=50000`).

## Configuration

### Product configuration file: `config/app-config.yaml`

Product decisions and feature flags live in one file, the only source of these values:

| Key | Value | Effect |
|---|---|---|
| `APP_NAME_FOR_CUSTOMER` | `Super Mercado` | Name customers see: login, page title, settings, installed-app name. 1–40 characters. |
| `LLM_ENABLED` | `false` | Local LLM agent (Agno + Ollama) for receipt lines the parser could not read. |
| `SMART_LIST_ENABLED` | `false` | Shows the smart shopping list («Lista») in the app. Off until the list is built and approved; the client reads it from `GET /v1/config`. |

- Every key is required and has no default in code. Unknown keys, wrong types (`LLM_ENABLED` and `SMART_LIST_ENABLED` must be
  real booleans), an empty or over-long name, or a missing file stop the API and the worker at start-up.
- Changing a value means editing the file and restarting the API and worker. The file is mounted into the
  containers (`./config:/app/config:ro`) and copied into the images.
- The old `BASKETIQ_LLM_ENABLED` variable is rejected at start-up so it cannot be silently ignored.
- `BASKETIQ_APP_CONFIG_FILE` only changes the file's **path** (tests, deployments).
- The Frontend reads the name at runtime from `GET /v1/config` (public) and the installed-app manifest from
  `GET /v1/config/manifest.webmanifest`; operators see every value in `GET /v1/admin/config`. The public
  endpoint exposes the name only, never the flags.
- The repositories, packages, `BASKETIQ_*` variables and technical documents keep the project name, BasketIQ.

### Environment variables (infrastructure, secrets, tuning)

All other settings are environment variables prefixed `BASKETIQ_` (see `src/basketiq/config.py`).
Notable: `CONFIDENCE_THRESHOLD` (0.80), `RECONCILIATION_TOLERANCE_CENTS` (2), `RETENTION_DAYS` (30),
`EXTERNAL_FALLBACK` (`disabled`|`mock`), `EXTERNAL_MONTHLY_BUDGET_CENTS` (0),
`OCR_ENABLE_MKLDNN` (`true`): oneDNN CPU kernels for PaddleOCR, about 3x faster; set `false` on a host whose CPU
lacks the instructions it needs. `paddlepaddle` is pinned to 3.2.2 because 3.3.1 fails with oneDNN.
Per-merchant thresholds/tolerance and enabling are managed by operators via `/v1/admin/merchants`.
