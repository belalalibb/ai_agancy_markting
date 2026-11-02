"""Provider catalog services (SPRINT-11.1 B-4, D-077).

* :class:`ProviderCatalog` — ``resolve(purpose)``: enabled routes ⇒ enabled models ⇒ enabled
  providers, ordered by priority; empty ⇒ :class:`NoRouteError` (503 ``ai_route_unavailable``).
* :class:`CatalogPriceTable` — the ``PriceTable`` interface (``estimate`` / ``estimate_unit``)
  read from ``ai_models``; ``None`` price ⇒ unpriced (503 ``ai_model_unpriced`` in strict mode);
  ``estimate_audio`` for audio models (``seconds > max_audio_seconds`` ⇒ unpriced).
* :class:`CredentialResolver` — the **only** place a provider key is decrypted / read from the
  environment, at client-build time; never logged, never returned.
* :class:`CatalogGatewayResolver` — the gateway the ``BudgetGuard`` wraps when the kill switch
  is on. Each attempt is its own ``BudgetGuard.complete`` (its own ``ai_cost_log`` row);
  fallback only on ``FALLBACK_CATEGORIES``, max :data:`MAX_ATTEMPTS`, **never** for
  ``credential_mode='user_key'``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from src.core.netguard import host_port_key, is_global_ip_literal
from src.core.security import TokenEncryptionService
from src.modules.ai_gateway.budget_guard import BudgetGuardError
from src.modules.ai_gateway.catalog.domain import (
    CatalogSnapshot,
    Model,
    Provider,
    ResolvedModel,
    catalog_purpose,
)
from src.modules.ai_gateway.contract import AIError, ErrorCategory
from src.modules.ai_gateway.facades import FakeProvider, HttpOpenAICompatibleProvider
from src.modules.ai_gateway.gateway import AIGateway, CompletionRequest, CompletionResult

logger = logging.getLogger(__name__)

_USD = Decimal("0.000001")
MAX_ATTEMPTS = 3
FALLBACK_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.PROVIDER_UNAVAILABLE,
        ErrorCategory.MODEL_UNAVAILABLE,
        ErrorCategory.RATE_LIMITED,
        ErrorCategory.TIMEOUT,
        ErrorCategory.RETRYABLE_SERVER_ERROR,
    }
)


# ------------------------------------------------------------------ errors ----
class CatalogError(Exception):
    """Base of the catalog's HTTP-literal errors (no new ``ErrorCategory`` — D-067)."""

    error = "catalog_error"
    status = 500

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.error)
        self.message = message or self.error


class NoRouteError(CatalogError, BudgetGuardError):
    """No enabled route/model/provider for the purpose ⇒ 503 ``ai_route_unavailable``.
    Raised at **runtime** through the guarded gateway, hence also a ``BudgetGuardError`` so
    every consumer's existing ``budget_error_to_http`` maps it (B-6; no new category)."""

    error = "ai_route_unavailable"
    status = 503

    def __init__(self, purpose: str) -> None:
        super().__init__(f"no enabled route for purpose {purpose!r}")
        self.purpose = purpose


class ProviderHostNotAllowedError(CatalogError):
    """``base_url`` host is not public and not in ``AI_ALLOW_LOCAL_PROVIDER_HOSTS`` ⇒ 422."""

    error = "provider_host_not_allowed"
    status = 422

    def __init__(self, host: str) -> None:
        super().__init__("provider host is not a public address and is not allow-listed")
        self.host = host


class CatalogConflictError(CatalogError):
    error = "catalog_conflict"
    status = 409


class CatalogNotFoundError(CatalogError):
    error = "catalog_not_found"
    status = 404


class CatalogInvalidPricingError(CatalogError):
    """``PATCH /models/{id}`` with a pricing shape that does not fit the model's capability."""

    error = "catalog_invalid_pricing"
    status = 422


class CredentialUnavailableError(CatalogError, BudgetGuardError):
    """``credential_ref`` names an unset environment variable ⇒ provider unusable (503).
    Runtime error like ``NoRouteError`` ⇒ also a ``BudgetGuardError`` (B-6)."""

    error = "provider_credential_unavailable"
    status = 503


# ------------------------------------------------------------- repository port ----
class CatalogReader(Protocol):
    async def snapshot(self) -> CatalogSnapshot: ...


# --------------------------------------------------------------- host guard ----
_DEFAULT_PORTS = {"http": 80, "https": 443}


def validate_provider_host(base_url: str, allow_local: Mapping[str, bool] | list[str]) -> str:
    """Return the normalised ``host:port`` key of ``base_url`` or raise
    :class:`ProviderHostNotAllowedError`. Accepted when the host is a **public IP literal or a
    DNS name** (DNS names are resolved by the HTTP client; an IP literal is checked here), or
    when ``host:port`` is listed verbatim in ``AI_ALLOW_LOCAL_PROVIDER_HOSTS``."""
    parts = urlsplit(base_url)
    if parts.scheme not in _DEFAULT_PORTS or not parts.hostname:
        raise ProviderHostNotAllowedError(parts.hostname or "")
    key = host_port_key(parts.hostname, parts.port, default_port=_DEFAULT_PORTS[parts.scheme])
    allowed = {h.strip().lower() for h in allow_local if h and h.strip()}
    if key in allowed:
        return key
    host = parts.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise ProviderHostNotAllowedError(host)
    try:
        import ipaddress

        ipaddress.ip_address(host)
    except ValueError:
        return key  # DNS name — resolved at call time by the HTTP client
    if not is_global_ip_literal(host):
        raise ProviderHostNotAllowedError(host)
    return key


# ---------------------------------------------------------------- catalog ----
class ProviderCatalog:
    def __init__(self, reader: CatalogReader) -> None:
        self._reader = reader

    async def snapshot(self) -> CatalogSnapshot:
        return await self._reader.snapshot()

    async def resolve(self, purpose: str) -> list[ResolvedModel]:
        """Enabled routes of ``purpose`` (a gateway purpose is mapped through the aliases),
        joined to enabled models and enabled providers, by priority. Empty ⇒ ``NoRouteError``."""
        target = catalog_purpose(purpose) or purpose
        snap = await self._reader.snapshot()
        providers = {p.id: p for p in snap.providers if p.enabled}
        models = {m.id: m for m in snap.models if m.enabled and m.provider_id in providers}
        resolved = [
            ResolvedModel(
                provider=providers[models[r.model_id].provider_id],
                model=models[r.model_id],
                priority=r.priority,
            )
            for r in sorted(snap.routes, key=lambda r: r.priority)
            if r.enabled and r.purpose == target and r.model_id in models
        ]
        if not resolved:
            raise NoRouteError(target)
        return resolved

    async def model_by_id(self, model_id: object) -> tuple[Provider, Model]:
        snap = await self._reader.snapshot()
        model = next((m for m in snap.models if m.id == model_id), None)
        if model is None:
            raise CatalogNotFoundError("model not found")
        provider = next((p for p in snap.providers if p.id == model.provider_id), None)
        if provider is None:  # only an in-memory reader can produce this (FK CASCADE on PG)
            raise CatalogNotFoundError("provider not found")
        return provider, model


class CatalogPriceTable:
    """``PriceSource`` over ``ai_models`` — same interface as ``pricing.PriceTable``."""

    def __init__(self, snapshot: CatalogSnapshot) -> None:
        self._by_name: dict[str, Model] = {}
        for model in snapshot.models:
            # Two providers may expose the same model name; the first enabled one wins and a
            # priced entry is preferred over an unpriced one.
            current = self._by_name.get(model.model_name)
            if current is None or (
                current.pricing.token_unit is None and model.pricing.token_unit is not None
            ):
                self._by_name[model.model_name] = model

    @property
    def models(self) -> frozenset[str]:
        return frozenset(self._by_name)

    def estimate_unit(self, model: str) -> tuple[Decimal, Decimal] | None:
        entry = self._by_name.get(model)
        return entry.pricing.token_unit if entry is not None else None

    def estimate(
        self, model: str, prompt_tokens: int | None, completion_tokens: int | None
    ) -> Decimal | None:
        unit = self.estimate_unit(model)
        if unit is None or prompt_tokens is None or completion_tokens is None:
            return None
        total = (Decimal(prompt_tokens) * unit[0] + Decimal(completion_tokens) * unit[1]) / 1000
        return total.quantize(_USD, rounding=ROUND_HALF_UP)

    def estimate_audio(self, model: str, seconds: int) -> Decimal | None:
        """Audio pricing (``cost_per_minute_usd``); ``None`` when unpriced **or** when
        ``seconds`` exceeds the model's ``max_audio_seconds``."""
        entry = self._by_name.get(model)
        if entry is None or entry.capability != "audio":
            return None
        pricing = entry.pricing
        if pricing.cost_per_minute_usd is None or pricing.max_audio_seconds is None:
            return None
        if seconds < 0 or seconds > pricing.max_audio_seconds:
            return None
        total = Decimal(seconds) * pricing.cost_per_minute_usd / 60
        return total.quantize(_USD, rounding=ROUND_HALF_UP)


# ------------------------------------------------------------- credentials ----
class CredentialResolver:
    """Decrypts ``credential_cipher`` / reads ``credential_ref`` at **client-build time**. The
    returned ``SecretStr`` is handed straight to the HTTP facade (Authorization header only)."""

    def __init__(
        self,
        encryption: TokenEncryptionService | None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._encryption = encryption
        self._env = env if env is not None else os.environ

    def resolve(self, provider: Provider) -> SecretStr | None:
        if provider.credential_cipher is not None:
            if self._encryption is None:
                raise CredentialUnavailableError("token encryption is not configured")
            return SecretStr(self._encryption.decrypt_token(provider.credential_cipher))
        if provider.credential_ref is not None:
            raw = self._env.get(provider.credential_ref)
            if not raw:
                raise CredentialUnavailableError(
                    f"environment variable {provider.credential_ref!r} is not set"
                )
            return SecretStr(raw)
        return None


# --------------------------------------------------------- gateway resolver ----
GuardFactory = Callable[[AIGateway, str], AIGateway]
"""``(inner_provider, model_name) ⇒ BudgetGuard`` — built per attempt by ``dependencies``."""


def build_provider_client(
    resolved: ResolvedModel,
    *,
    credentials: CredentialResolver,
    http_client: httpx.AsyncClient | None,
    user_key: SecretStr | None = None,
) -> AIGateway:
    """The concrete facade for one (provider, model): ``fake`` ⇒ ``FakeProvider``;
    ``openai_compatible`` ⇒ ``HttpOpenAICompatibleProvider`` on the resolved key (or the
    caller's ``X-AI-Key``)."""
    provider, model = resolved.provider, resolved.model
    if provider.kind == "fake":
        return FakeProvider(model=model.model_name)
    key = user_key if user_key is not None else credentials.resolve(provider)
    if key is None:
        raise CredentialUnavailableError(f"provider {provider.slug!r} has no credential")
    return HttpOpenAICompatibleProvider(
        base_url=provider.base_url,
        api_key=key,
        model=model.model_name,
        http_client=http_client,
        timeout_seconds=float(provider.timeout_seconds),
    )


class CatalogGatewayResolver:
    """Satisfies :class:`AIGateway`. ``complete`` resolves the route list for the request's
    purpose and runs **each candidate through its own BudgetGuard** (``guard_factory``), so a
    503 primary + 200 fallback leaves two distinct ``ai_cost_log`` rows (``error`` then ``ok``)
    priced per model (§2-B-4 / Criterion #12)."""

    name = "catalog"

    def __init__(
        self,
        catalog: ProviderCatalog,
        *,
        credentials: CredentialResolver,
        guard_factory: GuardFactory,
        http_client: httpx.AsyncClient | None = None,
        user_key: SecretStr | None = None,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._catalog = catalog
        self._credentials = credentials
        self._guard_factory = guard_factory
        self._http = http_client
        self._user_key = user_key
        self._max_attempts = max(1, int(max_attempts))
        self.model = "catalog"  # the BudgetGuard reads the per-attempt model instead
        self.attempts: list[str] = []

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        candidates = await self._catalog.resolve(req.purpose)
        # ``user_key`` = the caller's own key for ONE provider ⇒ never fall back (§2-B-4).
        if req.credential_mode == "user_key" or self._user_key is not None:
            candidates = candidates[:1]
        *fallbacks, final = candidates[: self._max_attempts]
        for index, candidate in enumerate(fallbacks):
            try:
                return await self._attempt(candidate, req)
            except AIError as exc:
                if exc.category not in FALLBACK_CATEGORIES:
                    raise
                following = (fallbacks + [final])[index + 1]
                logger.info(
                    "catalog fallback: purpose=%s from=%s/%s category=%s next=%s",
                    req.purpose,
                    candidate.provider.slug,
                    candidate.model.model_name,
                    exc.category.value,
                    following.model.model_name,
                )
        return await self._attempt(final, req)

    async def _attempt(self, candidate: ResolvedModel, req: CompletionRequest) -> CompletionResult:
        """One candidate = one facade + one ``BudgetGuard`` ⇒ one ``ai_cost_log`` row."""
        inner = build_provider_client(
            candidate,
            credentials=self._credentials,
            http_client=self._http,
            user_key=self._user_key,
        )
        guarded = self._guard_factory(inner, candidate.model.model_name)
        self.attempts.append(candidate.model.model_name)
        return await guarded.complete(req)


__all__ = [
    "FALLBACK_CATEGORIES",
    "MAX_ATTEMPTS",
    "CatalogConflictError",
    "CatalogError",
    "CatalogGatewayResolver",
    "CatalogInvalidPricingError",
    "CatalogNotFoundError",
    "CatalogPriceTable",
    "CatalogReader",
    "CredentialResolver",
    "CredentialUnavailableError",
    "NoRouteError",
    "ProviderCatalog",
    "ProviderHostNotAllowedError",
    "build_provider_client",
    "validate_provider_host",
]
