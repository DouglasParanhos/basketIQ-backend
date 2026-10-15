from __future__ import annotations

import json
import logging

import pytest

from basketiq.domain.normalization import canonical_name, normalize_key
from basketiq.observability.logging import configure_logging, redact


@pytest.mark.req("SEC-logs")
def test_redaction_removes_personal_and_receipt_content() -> None:
    payload = {
        "email": "a@b.pt",
        "receipt_id": "r1",
        "nested": {"raw_description": "PAO", "token": "x"},
        "lines": [1],
    }
    assert redact(payload) == {
        "email": "[REDACTED]",
        "receipt_id": "r1",
        "nested": {"raw_description": "[REDACTED]", "token": "[REDACTED]"},
        "lines": "[REDACTED]",
    }


@pytest.mark.req("SEC-logs")
def test_structured_logs_redact_extra_fields(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    logging.getLogger("t").info("event", extra={"email": "user@example.pt", "job_id": "j1"})
    line = capsys.readouterr().out.strip().splitlines()[-1]
    data = json.loads(line)
    assert data["email"] == "[REDACTED]" and data["job_id"] == "j1"
    assert "user@example.pt" not in line


@pytest.mark.req("F-20")
def test_normalization_merges_spelling_variants() -> None:
    assert normalize_key("Pão  de Forma") == normalize_key("PAO DE FORMA") == "PAO DE FORMA"
    assert normalize_key("TEMP.S.LIM.PD 200ML") == "TEMP S LIM PD 200ML"
    assert canonical_name("  PÃO   12 CER ") == "PÃO 12 CER"
