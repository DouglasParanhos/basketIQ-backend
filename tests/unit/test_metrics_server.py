from __future__ import annotations

import urllib.request

import pytest

from basketiq.observability import metrics


@pytest.mark.req("SEC-metrics")
def test_serve_exposes_prometheus_metrics_on_its_own_port() -> None:
    server, _ = metrics.serve(0)
    try:
        url = f"http://127.0.0.1:{server.server_port}/metrics"
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read().decode()
        assert "basketiq_http_request_seconds" in body
    finally:
        server.shutdown()
