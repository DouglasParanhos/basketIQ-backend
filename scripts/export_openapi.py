"""Write the OpenAPI contract consumed by the frontend (`pnpm gen:api`)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("BASKETIQ_ENV", "test")
os.environ.setdefault("BASKETIQ_STORAGE_BACKEND", "memory")
os.environ.setdefault("BASKETIQ_QUEUE_BACKEND", "inline")

from basketiq.api.main import app

target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("openapi.json")
target.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"OpenAPI written to {target}")
