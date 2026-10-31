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


# ------------------------------------------------------- SPRINT-02 Part A (C1/C3)
def test_default_graph_version_is_v26(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("META_GRAPH_API_VERSION", raising=False)
    assert Settings(_env_file=None).meta_graph_api_version == "v26.0"  # type: ignore[call-arg]


@pytest.mark.parametrize("expired", ["v19.0", "v20.0", "v12.0", "v2.11"])
def test_rejects_expired_graph_version(monkeypatch: pytest.MonkeyPatch, expired: str) -> None:
    monkeypatch.setenv("META_GRAPH_API_VERSION", expired)
    with pytest.raises(ValueError, match="no longer supported.*v21.0.*2027-01-21"):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("alive", ["v21.0", "v24.0", "v26.0", "v30.0"])
def test_accepts_live_graph_versions(monkeypatch: pytest.MonkeyPatch, alive: str) -> None:
    monkeypatch.setenv("META_GRAPH_API_VERSION", alive)
    assert Settings(_env_file=None).meta_graph_api_version == alive  # type: ignore[call-arg]


def test_default_scopes_include_publishing_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    """SPRINT-07 B3 supersedes the SPRINT-03 read-only default: publishing (M4) needs
    ``pages_manage_posts`` + ``instagram_content_publish``. The read scopes stay first, and
    nothing beyond the five documented scopes is requested."""
    monkeypatch.delenv("META_OAUTH_SCOPES", raising=False)
    scopes = Settings(_env_file=None).meta_scopes_list  # type: ignore[call-arg]
    assert scopes == [
        "pages_show_list",
        "pages_read_engagement",
        "pages_manage_posts",
        "instagram_basic",
        "instagram_content_publish",
    ]
    assert "ads_management" not in scopes and "business_management" not in scopes


def test_oauth_state_settings(settings: Settings) -> None:
    assert settings.oauth_state_secret is not None
    assert settings.oauth_state_secret.get_secret_value() not in repr(settings)
    assert settings.oauth_state_ttl_seconds == 600


def test_database_url_default_uses_asyncpg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert Settings(_env_file=None).database_url.startswith(  # type: ignore[call-arg]
        "postgresql+asyncpg://"
    )
