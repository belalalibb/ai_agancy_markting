"""Unit tests — Settings loading and derived properties."""

from __future__ import annotations

import pytest
from src.core.config import Settings, get_settings

from tests.conftest import FAKE_APP_ID, GRAPH_VERSION

pytestmark = pytest.mark.unit


def test_settings_read_from_environment(settings: Settings) -> None:
    assert settings.app_env == "test"
    assert settings.meta_app_id == FAKE_APP_ID
    assert settings.meta_graph_api_version == GRAPH_VERSION
    assert settings.is_production is False


def test_secrets_are_masked_in_repr(settings: Settings) -> None:
    rendered = repr(settings)
    assert "fake-app-secret" not in rendered
    assert settings.token_encryption_key is not None
    assert settings.token_encryption_key.get_secret_value() not in rendered


def test_meta_graph_url_is_versioned(settings: Settings) -> None:
    assert settings.meta_graph_url == f"https://graph.facebook.com/{GRAPH_VERSION}"


def test_meta_scopes_list_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("META_OAUTH_SCOPES", " pages_show_list , instagram_basic,,")
    assert Settings(_env_file=None).meta_scopes_list == [  # type: ignore[call-arg]
        "pages_show_list",
        "instagram_basic",
    ]


@pytest.mark.parametrize("bad", ["19.0", "vX", "", "v"])
def test_invalid_graph_version_rejected(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("META_GRAPH_API_VERSION", bad)
    with pytest.raises(ValueError, match="meta_graph_api_version"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()
