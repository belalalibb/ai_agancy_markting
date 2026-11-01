"""Closed dictionary of evidence metrics (D-074).

``brand_evidence.metric`` is ``VARCHAR(64)`` on the database side; the *allowed* names live
here so a typo can never become a new metric. Every metric states its unit and the
``evidence_kind`` it is normally produced with (a collector may still downgrade a metric to
``estimated`` when its source is weaker — never upgrade it).
"""

from __future__ import annotations

from typing import Final, Literal

EvidenceKind = Literal["measured", "inferred", "estimated"]
SourceKind = Literal[
    "graph_api", "cloud_api", "internal_db", "public_web", "owner_input", "model_inference"
]
SubjectKind = Literal["brand", "competitor"]

# D-074 — which sources may back which grade (mirrored by ``ck_brand_evidence_kind_source``).
ALLOWED_SOURCES: Final[dict[str, frozenset[str]]] = {
    "measured": frozenset({"graph_api", "cloud_api", "internal_db"}),
    "estimated": frozenset({"public_web", "owner_input", "graph_api"}),
    "inferred": frozenset({"model_inference"}),
}

# metric name ⇒ (unit, default grade)
METRICS: Final[dict[str, tuple[str, EvidenceKind]]] = {
    # Audience / reach
    "followers_count": ("count", "estimated"),
    "fan_count": ("count", "estimated"),
    # Publishing cadence
    "posting_frequency_7d": ("posts/7d", "measured"),
    "top_content_type": ("label", "inferred"),
    # Service quality (WhatsApp)
    "avg_reply_latency_s": ("seconds", "measured"),
    "wa_conversations_7d": ("count", "measured"),
    "wa_approval_rate_30d": ("ratio", "measured"),
    # Content governance
    "content_approval_rate_30d": ("ratio", "measured"),
    # Voice
    "tone_match_score": ("ratio", "inferred"),
    # Competitor public page (B-2)
    "public_page_summary": ("text", "estimated"),
    "page_category": ("label", "estimated"),
    "website_host": ("host", "estimated"),
}

# The five metrics ``EvidenceCollector.collect_internal`` measures from our own tables.
INTERNAL_METRICS: Final[tuple[str, ...]] = (
    "wa_conversations_7d",
    "wa_approval_rate_30d",
    "content_approval_rate_30d",
    "posting_frequency_7d",
    "avg_reply_latency_s",
)


def is_known_metric(name: str) -> bool:
    return name in METRICS


def source_allowed(evidence_kind: str, source_kind: str) -> bool:
    return source_kind in ALLOWED_SOURCES.get(evidence_kind, frozenset())
