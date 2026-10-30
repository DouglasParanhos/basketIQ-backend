from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from basketiq.app_config import AppConfig, get_app_config


@pytest.fixture
def config_is(client: TestClient) -> Iterator[Any]:
    """Serve a different configuration to the API, as if the file had other values."""
    from basketiq.api.main import app

    def use(name: str, llm: bool = False, smart_list: bool = False) -> None:
        cfg = AppConfig.model_validate(
            {"APP_NAME_FOR_CUSTOMER": name, "LLM_ENABLED": llm, "SMART_LIST_ENABLED": smart_list}
        )
        app.dependency_overrides[get_app_config] = lambda: cfg

    yield use
    app.dependency_overrides.pop(get_app_config, None)


@pytest.mark.req("CFG-02")
def test_public_config_needs_no_session_and_comes_from_the_file(client: TestClient) -> None:
    resp = client.get("/v1/config")
    assert resp.status_code == 200
    assert resp.json() == {"app_name": "Super Mercado", "smart_list_enabled": False}  # as in the file


@pytest.mark.req("CFG-02")
def test_name_is_not_hard_coded(client: TestClient, config_is: Any) -> None:
    config_is("Mercado do Zé")
    assert client.get("/v1/config").json() == {"app_name": "Mercado do Zé", "smart_list_enabled": False}


@pytest.mark.req("F-111")
def test_public_config_tells_the_client_whether_the_smart_list_is_on(
    client: TestClient, config_is: Any
) -> None:
    assert client.get("/v1/config").json()["smart_list_enabled"] is False  # off in the repository file
    config_is("Loja", smart_list=True)
    resp = client.get("/v1/config")
    assert resp.json() == {"app_name": "Loja", "smart_list_enabled": True}  # nothing else of the file leaks


@pytest.mark.req("CFG-02")
def test_manifest_carries_the_configured_name(client: TestClient, config_is: Any) -> None:
    manifest = client.get("/v1/config/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    body = manifest.json()
    assert body["name"] == body["short_name"] == "Super Mercado"
    assert body["display"] == "standalone" and body["start_url"] == "/"
    assert {icon["sizes"] for icon in body["icons"]} == {"192x192", "512x512"}
    config_is("Mercado do Zé")
    assert client.get("/v1/config/manifest.webmanifest").json()["name"] == "Mercado do Zé"


def test_public_config_exposes_the_name_and_the_smart_list_option_and_no_other_flag(
    client: TestClient, config_is: Any
) -> None:
    config_is("Loja", llm=True, smart_list=True)
    assert set(client.get("/v1/config").json()) == {"app_name", "smart_list_enabled"}  # never `llm_enabled`


@pytest.mark.req("F-111")
def test_admin_config_shows_every_key_to_operators_only(
    client: TestClient, login: Any, config_is: Any
) -> None:
    assert client.get("/v1/admin/config").status_code == 401
    login(client)
    assert client.get("/v1/admin/config").status_code == 403

    admin = TestClient(client.app)
    login(admin, role="admin")
    assert admin.get("/v1/admin/config").json() == {
        "app_name_for_customer": "Super Mercado",
        "llm_enabled": False,
        "smart_list_enabled": False,
    }
    config_is("Loja", llm=True, smart_list=True)
    assert admin.get("/v1/admin/config").json() == {
        "app_name_for_customer": "Loja",
        "llm_enabled": True,
        "smart_list_enabled": True,
    }


@pytest.mark.req("CFG-01")
@pytest.mark.req("F-112")
@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("APP_NAME_FOR_CUSTOMER: Loja\nSMART_LIST_ENABLED: false\n", "LLM_ENABLED"),  # a key missing
        ("APP_NAME_FOR_CUSTOMER: Loja\nLLM_ENABLED: false\n", "SMART_LIST_ENABLED"),  # the new key missing
        (
            "APP_NAME_FOR_CUSTOMER: Loja\nLLM_ENABLED: false\nSMART_LIST_ENABLED: 'true'\n",
            "SMART_LIST_ENABLED",
        ),
    ],
)
def test_api_refuses_to_start_with_an_invalid_file(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, text: str, key: str
) -> None:
    from basketiq.api.main import create_app
    from basketiq.app_config import AppConfigError

    bad = tmp_path / "app-config.yaml"
    bad.write_text(text, encoding="utf-8")
    monkeypatch.setenv("BASKETIQ_APP_CONFIG_FILE", str(bad))
    get_app_config.cache_clear()
    try:
        with pytest.raises(AppConfigError, match=key):
            create_app()
    finally:
        monkeypatch.delenv("BASKETIQ_APP_CONFIG_FILE")
        get_app_config.cache_clear()
