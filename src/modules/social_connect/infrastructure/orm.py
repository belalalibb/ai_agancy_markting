"""SQLAlchemy ORM tables for ``social_connect`` (PostgreSQL dialect, SPRINT-02 §5-B4).

``social_connections``        — one row per (tenant, provider, provider user).
``social_connection_pages``   — Facebook Pages (+ linked IG account) per connection.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import Base


class SocialConnectionRow(Base):
    __tablename__ = "social_connections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "provider", "provider_user_id", name="uq_social_connections_identity"
        ),
        Index("ix_social_connections_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted_user_token: Mapped[str] = mapped_column(Text, nullable=False)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    granted_scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    connected_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    pages: Mapped[list[SocialConnectionPageRow]] = relationship(
        back_populates="connection",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="SocialConnectionPageRow.page_id",
    )


class SocialConnectionPageRow(Base):
    __tablename__ = "social_connection_pages"
    __table_args__ = (
        UniqueConstraint("connection_id", "page_id", name="uq_social_connection_pages_page"),
        Index("ix_social_connection_pages_connection_id", "connection_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("social_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_id: Mapped[str] = mapped_column(String(64), nullable=False)
    page_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tasks: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # NULL = the user has no token for this Page (no Page role) — SPRINT-02.1 F4.
    encrypted_page_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    ig_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ig_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ig_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ig_profile_picture_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    connection: Mapped[SocialConnectionRow] = relationship(back_populates="pages")
