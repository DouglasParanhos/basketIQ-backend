from __future__ import annotations

import socket
import urllib.request

import pytest

from basketiq.observability import metrics


@pytest.fixture(autouse=True)
def fresh_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metrics, "_server", None)  # serve() keeps one server per process


@pytest.mark.req("SEC-metrics")
def test_serve_exposes_prometheus_metrics_on_its_own_port() -> None:
    result = metrics.serve(0)
    assert result is not None
    server, _ = result
    try:
        url = f"http://127.0.0.1:{server.server_port}/metrics"
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read().decode()
        assert "basketiq_http_request_seconds" in body
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.req("SEC-metrics")
def test_a_port_in_use_costs_the_metrics_not_the_process() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        assert metrics.serve(taken.getsockname()[1]) is None
