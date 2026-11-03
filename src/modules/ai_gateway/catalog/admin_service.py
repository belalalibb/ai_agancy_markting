"""Admin operations of the provider catalog (SPRINT-11.1 C-1/C-2, D-077).

* Keys are **encrypted on receipt** (``TokenEncryptionService``) and the plaintext is dropped
  before this layer returns; nothing here logs or echoes a key (G4).
* ``base_url`` passes :func:`validate_provider_host` (public IP literal / DNS name, or an entry
  of ``AI_ALLOW_LOCAL_PROVIDER_HOSTS``) ⇒ otherwise 422 ``provider_host_not_allowed``.
* Every write calls ``invalidate()`` on the in-process snapshot cache.
* ``probe`` runs the named model through a **fresh BudgetGuard** with
  ``purpose="admin_probe"`` / ``brand_id=None`` ⇒ one ``ai_cost_log`` row, ``brand_id NULL``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Protocol
from uuid import UUID

import httpx
from pydantic import SecretStr

from src.core.netguard import Resolver
from src.core.security import TokenEncryptionService
from src.modules.ai_gateway.catalog.domain import (
    CatalogSnapshot,
    Model,
    ModelPricing,
    Provider,
    ResolvedModel,
    Route,
    utcnow,
)
from src.modules.ai_gateway.catalog.schemas import (
    ModelCreate,
    ModelPatch,
    ProbeOut,
    ProbeRequest,
    ProviderCreate,
    ProviderPatch,
    RoutingPut,
)
from src.modules.ai_gateway.catalog.service import (
    CatalogEncryptionUnavailableError,
    CatalogInvalidPricingError,
    CatalogNotFoundError,
    CatalogPriceTable,
    CredentialResolver,
    build_provider_client,
    ensure_provider_host_resolves_public,
)
from src.modules.ai_gateway.contract import AIError
from src.modules.ai_gateway.gateway import AIGateway, CompletionRequest

logger = logging.getLogger(__name__)

PROBE_SYSTEM = "You are a connectivity probe. Reply briefly."
PREVIEW_CHARS = 200


class CatalogRepository(Protocol):
    async def list_providers(self) -> list[Provider]: ...
    async def get_provider(self, provider_id: UUID) -> Provider | None: ...
    async def add_provider(self, provider: Provider) -> Provider: ...
    async def update_provider(self, provider: Provider) -> Provider: ...
    async def delete_provider(self, provider_id: UUID) -> bool: ...
    async def list_models(self, provider_id: UUID | None = None) -> list[Model]: ...
    async def get_model(self, model_id: UUID) -> Model | None: ...
    async def add_model(self, model: Model) -> Model: ...
    async def update_model(self, model: Model) -> Model: ...
    async def delete_model(self, model_id: UUID) -> bool: ...
    async def list_routes(self, purpose: str | None = None) -> list[Route]: ...
    async def replace_routes(self, purpose: str, routes: list[Route]) -> list[Route]: ...
    async def snapshot(self) -> CatalogSnapshot: ...


GuardFactory = Callable[[AIGateway, str, CatalogSnapshot], AIGateway]
"""``(inner_provider, model_name, snapshot) ⇒ BudgetGuard`` priced from that snapshot."""


def _audit(action: str, actor_user_id: UUID, target_id: UUID | str) -> None:
    """C-1: the admin audit line — actor, action, target; **never** a credential."""
    logger.info(
        "admin_ai action=%s actor_user_id=%s target_id=%s", action, actor_user_id, target_id
    )


class CatalogAdminService:
    def __init__(
        self,
        repo: CatalogRepository,
        *,
        encryption: TokenEncryptionService | None,
        allow_local_hosts: Sequence[str],
        invalidate: Callable[[], None],
        resolver: Resolver | None = None,
    ) -> None:
        self._repo = repo
        self._encryption = encryption
        self._allow_local = list(allow_local_hosts)
        self._invalidate = invalidate
        self._resolver = resolver

    # ------------------------------------------------------------ helpers --
    def _cipher(self, key: SecretStr) -> str:
        if self._encryption is None:  # SPRINT-12 A-6c (H6c): 503, not 404
            raise CatalogEncryptionUnavailableError()
        # The only unwrap outside ``CredentialResolver``: the credential is encrypted on
        # receipt and the plaintext is dropped with this frame (G4).
        return self._encryption.encrypt_token(key.get_secret_value())  # credential ⇒ cipher

    async def _check_host(self, base_url: str) -> None:
        """SPRINT-12 A-4 (H4): literal **and** DNS answers must be global (``getaddrinfo``)."""
        await ensure_provider_host_resolves_public(
            base_url, self._allow_local, resolver=self._resolver
        )

    # ---------------------------------------------------------- providers --
    async def list_providers(self) -> list[Provider]:
        return await self._repo.list_providers()

    async def create_provider(self, payload: ProviderCreate, *, actor_user_id: UUID) -> Provider:
        await self._check_host(payload.base_url)
        provider = Provider(
            slug=payload.slug,
            kind=payload.kind,
            base_url=payload.base_url,
            credential_cipher=self._cipher(payload.api_key) if payload.api_key else None,
            credential_ref=payload.credential_ref,
            enabled=payload.enabled,
            timeout_seconds=payload.timeout_seconds,
        )
        created = await self._repo.add_provider(provider)
        self._invalidate()
        _audit("provider.create", actor_user_id, created.id)
        return created

    async def patch_provider(
        self, provider_id: UUID, payload: ProviderPatch, *, actor_user_id: UUID
    ) -> Provider:
        current = await self._repo.get_provider(provider_id)
        if current is None:
            raise CatalogNotFoundError("provider not found")
        update: dict[str, object] = {"updated_at": utcnow()}
        if payload.sent("enabled") and payload.enabled is not None:
            update["enabled"] = payload.enabled
        if payload.sent("base_url") and payload.base_url is not None:
            await self._check_host(payload.base_url)
            update["base_url"] = payload.base_url
        if payload.sent("timeout_seconds") and payload.timeout_seconds is not None:
            update["timeout_seconds"] = payload.timeout_seconds
        if payload.sent("api_key"):
            if payload.api_key is None:
                update["credential_cipher"] = None
            else:
                update["credential_cipher"] = self._cipher(payload.api_key)
                update["credential_ref"] = None
        if payload.sent("credential_ref"):
            update["credential_ref"] = payload.credential_ref
            if payload.credential_ref is not None:
                update["credential_cipher"] = None
        updated = await self._repo.update_provider(current.model_copy(update=update))
        self._invalidate()
        _audit("provider.patch", actor_user_id, provider_id)
        return updated

    async def delete_provider(self, provider_id: UUID, *, actor_user_id: UUID) -> None:
        if not await self._repo.delete_provider(provider_id):
            raise CatalogNotFoundError("provider not found")
        self._invalidate()
        _audit("provider.delete", actor_user_id, provider_id)

    # ------------------------------------------------------------- models --
    async def list_models(self, provider_id: UUID | None = None) -> list[Model]:
        return await self._repo.list_models(provider_id)

    async def create_model(self, payload: ModelCreate, *, actor_user_id: UUID) -> Model:
        if await self._repo.get_provider(payload.provider_id) is None:
            raise CatalogNotFoundError("provider not found")
        model = Model(
            provider_id=payload.provider_id,
            model_name=payload.model_name,
            capability=payload.capability,
            pricing=payload.pricing.to_domain(),
            max_tokens_cap=payload.max_tokens_cap,
            enabled=payload.enabled,
        )
        created = await self._repo.add_model(model)
        self._invalidate()
        _audit("model.create", actor_user_id, created.id)
        return created

    async def patch_model(
        self, model_id: UUID, payload: ModelPatch, *, actor_user_id: UUID
    ) -> Model:
        current = await self._repo.get_model(model_id)
        if current is None:
            raise CatalogNotFoundError("model not found")
        update: dict[str, object] = {"updated_at": utcnow()}
        if payload.sent("enabled") and payload.enabled is not None:
            update["enabled"] = payload.enabled
        if payload.sent("pricing") and payload.pricing is not None:
            pricing = payload.pricing.to_domain()
            if not pricing.is_shape_for(current.capability):
                raise CatalogInvalidPricingError(
                    f"pricing shape does not match capability {current.capability!r}"
                )
            update["pricing"] = pricing
        if payload.sent("max_tokens_cap"):
            update["max_tokens_cap"] = payload.max_tokens_cap
        updated = await self._repo.update_model(current.model_copy(update=update))
        self._invalidate()
        _audit("model.patch", actor_user_id, model_id)
        return updated

    async def delete_model(self, model_id: UUID, *, actor_user_id: UUID) -> None:
        if not await self._repo.delete_model(model_id):
            raise CatalogNotFoundError("model not found")
        self._invalidate()
        _audit("model.delete", actor_user_id, model_id)

    # ------------------------------------------------------------ routing --
    async def list_routes(self, purpose: str | None = None) -> list[Route]:
        return await self._repo.list_routes(purpose)

    async def replace_routing(
        self, purpose: str, payload: RoutingPut, *, actor_user_id: UUID
    ) -> list[Route]:
        known = {m.id: m for m in await self._repo.list_models()}
        missing = [str(m) for m in payload.models if m not in known]
        if missing:
            raise CatalogNotFoundError(f"unknown model id(s): {', '.join(missing)}")
        wanted = "audio" if purpose == "audio_transcription" else "text"
        wrong = [str(m) for m in payload.models if known[m].capability != wanted]
        if wrong:  # SPRINT-12 A-6d (H6d): a model whose capability cannot serve the purpose
            raise CatalogInvalidPricingError(
                f"purpose {purpose!r} needs {wanted!r} models; mismatched: {', '.join(wrong)}"
            )
        routes = [
            Route(purpose=purpose, model_id=model_id, priority=index)  # type: ignore[arg-type]
            for index, model_id in enumerate(payload.models)
        ]
        stored = await self._repo.replace_routes(purpose, routes)
        self._invalidate()
        _audit("routing.replace", actor_user_id, purpose)
        return stored

    # -------------------------------------------------------------- probe --
    async def probe(
        self,
        payload: ProbeRequest,
        *,
        actor_user_id: UUID,
        tenant_id: UUID,
        guard_factory: GuardFactory,
        http_client: httpx.AsyncClient | None,
    ) -> ProbeOut:
        """Run ``payload.prompt`` through the named model behind a fresh ``BudgetGuard``.
        Provider failures ⇒ ``ok=false`` + the ``ErrorCategory`` (never a 5xx)."""
        model = await self._repo.get_model(payload.model_id)
        if model is None:
            raise CatalogNotFoundError("model not found")
        provider = await self._repo.get_provider(model.provider_id)
        if provider is None:  # pragma: no cover - FK CASCADE makes this unreachable on PG
            raise CatalogNotFoundError("provider not found")
        snapshot = await self._repo.snapshot()
        resolved = ResolvedModel(provider=provider, model=model, priority=0)
        inner = build_provider_client(
            resolved,
            credentials=CredentialResolver(self._encryption),
            http_client=http_client,
            allow_local=self._allow_local,
            resolver=self._resolver,
        )
        # A-5: the guard prices by the resolved key; A-6a: the facade applies max_tokens_cap.
        guarded = guard_factory(inner, inner.model, snapshot)
        req = CompletionRequest(
            purpose="admin_probe",
            system=PROBE_SYSTEM,
            user=payload.prompt,
            max_tokens=payload.max_tokens,
            temperature=0.0,
            brand_id=None,
            tenant_id=tenant_id,
            prompt_version="admin-probe-v1",
        )
        started = time.perf_counter()
        _audit("probe", actor_user_id, model.id)
        try:
            result = await guarded.complete(req)
        except AIError as exc:
            return ProbeOut(
                ok=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                prompt_tokens=None,
                completion_tokens=None,
                estimated_cost_usd=None,
                preview="",
                error_category=exc.category,
            )
        # A-5 (H5): one pricing source — the same ``CatalogPriceTable`` the guard used.
        cost = CatalogPriceTable(snapshot).estimate(
            inner.model, result.usage.prompt_tokens, result.usage.completion_tokens
        )
        return ProbeOut(
            ok=True,
            latency_ms=result.latency_ms,
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            estimated_cost_usd=cost,
            preview=result.text[:PREVIEW_CHARS],
            error_category=None,
        )


__all__ = ["CatalogAdminService", "CatalogRepository", "GuardFactory", "ModelPricing"]
