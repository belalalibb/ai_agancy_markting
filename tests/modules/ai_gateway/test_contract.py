"""SPRINT-07 B-0 — provider contract (D-067) and No-Bypass rule (D-068)."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import SecretStr
from src.modules.ai_gateway.contract import (
    RETRYABLE_DEFAULT,
    AIError,
    ErrorCategory,
    sanitize_provider_code,
)
from src.modules.ai_gateway.facades import HttpOpenAICompatibleProvider
from src.modules.ai_gateway.gateway import CompletionRequest

from tests.modules.ai_gateway.test_providers import BASE, KEY, _req

SRC = Path(__file__).resolve().parents[3] / "src"

# ------------------------------------------------------------------ B0-U1 No-Bypass ----------
BANNED_MODULES = ("httpx", "openai", "anthropic", "litellm", "google.generativeai")
# Must mirror ``[tool.ruff.lint.per-file-ignores]`` for TID251 in pyproject.toml.
ALLOWED_PREFIXES = ("modules/ai_gateway/", "core/")
ALLOWED_FILES = ("modules/social_connect/meta_client.py",)


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _is_allowed(rel: str) -> bool:
    return rel.startswith(ALLOWED_PREFIXES) or rel in ALLOWED_FILES


def test_b0_u1_no_bypass_imports() -> None:
    """Every ``src/**/*.py`` outside the allow-list is free of HTTP-client / model-SDK imports;
    the allow-list itself is used (so the rule is not vacuous)."""
    offenders: list[tuple[str, str]] = []
    allowed_hits: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        banned = {
            name
            for name in _imports_of(path)
            if any(name == b or name.startswith(b + ".") for b in BANNED_MODULES)
        }
        if not banned:
            continue
        if _is_allowed(rel):
            allowed_hits.add(rel)
        else:
            offenders.extend((rel, b) for b in sorted(banned))
    assert offenders == []
    assert {"core/http.py", "modules/social_connect/meta_client.py"} <= allowed_hits
    assert any(hit.startswith("modules/ai_gateway/") for hit in allowed_hits)


def test_b0_u1_pyproject_bans_the_same_modules() -> None:
    import tomllib

    cfg = tomllib.loads((SRC.parent / "pyproject.toml").read_text(encoding="utf-8"))
    lint = cfg["tool"]["ruff"]["lint"]
    assert "TID" in lint["select"]
    banned = lint["flake8-tidy-imports"]["banned-api"]
    assert set(banned) == set(BANNED_MODULES)
    assert all(v["msg"] == "Use AIGateway (D-068)" for v in banned.values())
    ignores = lint["per-file-ignores"]
    assert "TID251" in ignores["src/modules/ai_gateway/**"]
    assert "TID251" in ignores["src/modules/social_connect/meta_client.py"]
    assert "TID251" in ignores["src/core/**"]
    assert "TID251" in ignores["tests/**"]


# ---------------------------------------------------------------- B0-U2 contract freeze -----
FROZEN_CATEGORIES = (
    "auth_expired",
    "invalid_credential",
    "rate_limited",
    "quota_exceeded",
    "model_unavailable",
    "provider_unavailable",
    "unsupported_capability",
    "bad_request",
    "content_rejected",
    "timeout",
    "retryable_server_error",
    "non_retryable_error",
)
FROZEN_RETRYABLE = {
    "auth_expired": True,
    "invalid_credential": False,
    "rate_limited": True,
    "quota_exceeded": False,
    "model_unavailable": False,
    "provider_unavailable": False,
    "unsupported_capability": False,
    "bad_request": False,
    "content_rejected": False,
    "timeout": True,
    "retryable_server_error": True,
    "non_retryable_error": False,
}


def test_b0_u2_contract_freeze() -> None:
    """The twelve categories and their retry defaults are literally those of D-067 — any
    change here is an architecture decision, so this test is *meant* to fail on edits."""
    assert tuple(c.value for c in ErrorCategory) == FROZEN_CATEGORIES
    assert len(ErrorCategory) == 12
    assert {c.value: r for c, r in RETRYABLE_DEFAULT.items()} == FROZEN_RETRYABLE
    assert sum(FROZEN_RETRYABLE.values()) == 4
    for category in ErrorCategory:
        assert AIError(category).retryable is RETRYABLE_DEFAULT[category]
    assert AIError(ErrorCategory.BAD_REQUEST, retryable=True).retryable is True  # override


# ----------------------------------------------------------- B0-U3 response shapes ----------
def _provider() -> HttpOpenAICompatibleProvider:
    return HttpOpenAICompatibleProvider(base_url=BASE, api_key=SecretStr(KEY), model="gpt-x")


def _choice(content: str | None = "ok", **choice_extra: object) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}, **choice_extra}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


SHAPES: list[tuple[str, object, ErrorCategory, bool, int]] = [
    # id, response (httpx.Response or exception), category, retryable, calls
    (
        "401",
        httpx.Response(401, json={"error": {"code": "invalid_api_key"}}),
        ErrorCategory.INVALID_CREDENTIAL,
        False,
        1,
    ),
    (
        "403",
        httpx.Response(403, json={"error": {"message": "forbidden"}}),
        ErrorCategory.INVALID_CREDENTIAL,
        False,
        1,
    ),
    (
        "429",
        httpx.Response(429, headers={"Retry-After": "3"}, json={"error": {"code": "rate"}}),
        ErrorCategory.RATE_LIMITED,
        True,
        1,
    ),
    (
        "404",
        httpx.Response(404, json={"error": {"code": "model_not_found"}}),
        ErrorCategory.MODEL_UNAVAILABLE,
        False,
        1,
    ),
    (
        "400",
        httpx.Response(400, json={"error": {"code": "invalid_request_error"}}),
        ErrorCategory.BAD_REQUEST,
        False,
        1,
    ),
    (
        "413",
        httpx.Response(413, json={"error": {"code": "payload_too_large"}}),
        ErrorCategory.BAD_REQUEST,
        False,
        1,
    ),
    (
        "400-content",
        httpx.Response(400, json={"error": {"code": "content_policy_violation"}}),
        ErrorCategory.CONTENT_REJECTED,
        False,
        1,
    ),
    (
        "503",
        httpx.Response(503, json={"error": {"code": "overloaded"}}),
        ErrorCategory.RETRYABLE_SERVER_ERROR,
        True,
        2,
    ),
    (
        "500-text",
        httpx.Response(500, text="<html>boom</html>"),
        ErrorCategory.RETRYABLE_SERVER_ERROR,
        True,
        2,
    ),
    ("timeout", httpx.ReadTimeout("slow"), ErrorCategory.TIMEOUT, True, 2),
    ("network", httpx.ConnectError("refused"), ErrorCategory.PROVIDER_UNAVAILABLE, False, 1),
    (
        "200-no-choices-quota",
        httpx.Response(200, json={"error": {"message": "Insufficient credit balance"}}),
        ErrorCategory.QUOTA_EXCEEDED,
        False,
        1,
    ),
    (
        "200-empty-choices",
        httpx.Response(200, json={"choices": []}),
        ErrorCategory.QUOTA_EXCEEDED,
        False,
        1,
    ),
    (
        "200-finish-quota",
        httpx.Response(200, json=_choice("", finish_reason="insufficient_quota")),
        ErrorCategory.QUOTA_EXCEEDED,
        False,
        1,
    ),
    (
        "200-content-filter",
        httpx.Response(200, json=_choice("", finish_reason="content_filter")),
        ErrorCategory.CONTENT_REJECTED,
        False,
        1,
    ),
    (
        "200-refusal",
        httpx.Response(200, json={"choices": [{"message": {"content": None, "refusal": "no"}}]}),
        ErrorCategory.CONTENT_REJECTED,
        False,
        1,
    ),
    (
        "200-empty-text",
        httpx.Response(200, json=_choice("   ")),
        ErrorCategory.NON_RETRYABLE_ERROR,
        False,
        1,
    ),
    (
        "200-not-json",
        httpx.Response(200, text="not json"),
        ErrorCategory.NON_RETRYABLE_ERROR,
        False,
        1,
    ),
    (
        "418",
        httpx.Response(418, json={"error": {"code": "teapot"}}),
        ErrorCategory.NON_RETRYABLE_ERROR,
        False,
        1,
    ),
]


@pytest.mark.parametrize(
    ("response", "category", "retryable", "calls"),
    [s[1:] for s in SHAPES],
    ids=[s[0] for s in SHAPES],
)
async def test_b0_u3_response_shape_maps_to_category(
    response: object, category: ErrorCategory, retryable: bool, calls: int
) -> None:
    """Every provider answer shape ⇒ exactly one category with the frozen ``retryable`` value;
    a second attempt happens **only** for retryable transport/server failures."""
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        kwargs = {"side_effect": response} if isinstance(response, Exception) else {}
        if not kwargs:
            kwargs = {"return_value": response}
        route = router.post("/chat/completions").mock(**kwargs)
        with pytest.raises(AIError) as exc:
            await _provider().complete(_req())
    assert exc.value.category is category
    assert exc.value.retryable is retryable
    assert route.call_count == calls
    if category is ErrorCategory.RATE_LIMITED:
        assert exc.value.retry_after_ms == 3_000


async def test_b0_u3_success_carries_usage_and_credential_mode() -> None:
    with respx.mock(base_url=BASE) as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(200, json=_choice("done", finish_reason="stop"))
        )
        result = await _provider().complete(_req())
    assert result.text == "done" and result.usage.prompt_tokens == 1
    assert result.credential_mode == "platform" and result.finish_reason == "stop"


# ---------------------------------------------------------- B0-U4 provider_code sanitised ---
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("insufficient_quota", "insufficient_quota"),
        ("Model-Not.Found", "model_not_found"),
        (429, "429"),
        ("", None),
        (None, None),
        (True, None),
        ("Incorrect API key provided: " + KEY + ". You can find your key…", None),
        ("x" * 65, None),
        ("has space", None),
        ("ünïcode", None),
    ],
)
def test_b0_u4_provider_code_is_sanitised(raw: object, expected: str | None) -> None:
    assert sanitize_provider_code(raw) == expected


async def test_b0_u4_provider_message_with_key_never_reaches_the_error() -> None:
    """A provider error whose *message* echoes the key (OpenAI does this) must leave no trace
    in ``provider_code`` or ``str(exc)``."""
    leaky = {"error": {"code": f"Incorrect API key provided: {KEY}", "message": f"key {KEY}"}}
    with respx.mock(base_url=BASE) as router:
        router.post("/chat/completions").mock(return_value=httpx.Response(401, json=leaky))
        with pytest.raises(AIError) as exc:
            await _provider().complete(_req())
    assert exc.value.category is ErrorCategory.INVALID_CREDENTIAL
    assert exc.value.provider_code is None
    assert KEY not in str(exc.value) and KEY not in repr(exc.value.__dict__)
    assert str(exc.value) == "ai error: invalid_credential"


def test_completion_request_defaults_operation_and_credential_mode() -> None:
    req: CompletionRequest = _req()
    assert req.operation == "generate_text" and req.credential_mode == "platform"
    with pytest.raises(ValueError, match="operation"):
        _req().model_copy(update={}).__class__(**{**req.model_dump(), "operation": "embed"})
