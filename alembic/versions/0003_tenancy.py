"""tenancy — tenants / brands / brand_access_grants, brand-scoped connections, page selection

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29

SPRINT-03 Part B (owner decision "option C simplified").

upgrade()
---------
1. Create ``tenants``, ``brands``, ``brand_access_grants``.
2. ``social_connections``: add nullable ``brand_id`` / ``connected_by_tenant_id``.
3. **Backfill with raw SQL** (no ORM in migrations) through a temp table
   ``_legacy_tenant_map(legacy_tenant_id, tenant_id, brand_id)``:
   * one tenant per DISTINCT legacy ``tenant_id`` — ``slug = lower(tenant_id)`` with every
     char outside ``[a-z0-9-]`` replaced by ``-`` and edge dashes trimmed; on collision or
     when shorter than 3 chars ⇒ append ``-`` + first 8 chars of ``md5(tenant_id)``;
   * one ``default`` brand ("Default brand") per created tenant;
   * ``UPDATE social_connections SET brand_id, connected_by_tenant_id`` via the map.
4. ``SET NOT NULL``, add FKs / indexes / ``uq_social_connections_brand_identity``; drop
   ``uq_social_connections_identity``, ``ix_social_connections_tenant_id``, ``tenant_id``.
5. ``social_connection_pages``: add ``is_selected`` (default false), mark legacy pages
   *with* a token as selected (don't break existing work), add the CHECK constraint.
6. Drop the temp table.

downgrade()
-----------
Re-adds ``tenant_id VARCHAR(64)`` populated with the owner tenant's ``slug``, then —
**before** re-creating ``uq_social_connections_identity (tenant_id, provider,
provider_user_id)`` — deletes duplicate rows sharing that triple (the same Meta account
linked to two brands of one owner), keeping the **newest** (``updated_at``, then
``connected_at``), to avoid a ``UniqueViolation``. Then drops the new columns /
constraints, ``is_selected`` + CHECK, and the three tables in reverse order.

**Lossy by design (accepted):** the downgrade loses the ``connected_by`` distinction
(agency vs owner), all access grants, the per-brand split of a shared Meta account and
the explicit page selection flags.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    # 1. new tables -----------------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_table(
        "brands",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("owner_tenant_id", "slug", name="uq_brands_owner_slug"),
    )
    op.create_index("ix_brands_owner_tenant_id", "brands", ["owner_tenant_id"])
    op.create_table(
        "brand_access_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "grantee_tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column(
            "granted_by_tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("granted_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('manager', 'viewer')", name="ck_brand_access_grants_role"),
    )
    op.create_index("ix_brand_access_grants_brand_id", "brand_access_grants", ["brand_id"])
    op.create_index(
        "ix_brand_access_grants_grantee_tenant_id", "brand_access_grants", ["grantee_tenant_id"]
    )
    op.create_index(
        "uq_brand_access_grants_active",
        "brand_access_grants",
        ["brand_id", "grantee_tenant_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # 2. nullable new columns ---------------------------------------------------------
    op.add_column(
        "social_connections",
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "social_connections",
        sa.Column("connected_by_tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # 3. backfill (raw SQL) -----------------------------------------------------------
    op.execute(
        """
        CREATE TEMP TABLE _legacy_tenant_map (
            legacy_tenant_id VARCHAR(64) PRIMARY KEY,
            tenant_id UUID NOT NULL,
            brand_id UUID NOT NULL,
            slug VARCHAR(64) NOT NULL
        )
        """
    )
    op.execute(
        """
        WITH legacy AS (
            SELECT DISTINCT tenant_id AS legacy_tenant_id FROM social_connections
        ),
        base AS (
            SELECT
                legacy_tenant_id,
                trim(both '-' from regexp_replace(lower(legacy_tenant_id), '[^a-z0-9-]', '-', 'g'))
                    AS raw_slug
            FROM legacy
        ),
        counted AS (
            SELECT legacy_tenant_id, raw_slug,
                   count(*) OVER (PARTITION BY raw_slug) AS dup_count
            FROM base
        ),
        resolved AS (
            SELECT
                legacy_tenant_id,
                CASE
                    WHEN dup_count > 1 OR length(raw_slug) < 3
                         OR raw_slug IN (SELECT slug FROM tenants)
                    THEN left(raw_slug || '-' || left(md5(legacy_tenant_id), 8), 64)
                    ELSE raw_slug
                END AS slug
            FROM counted
        )
        INSERT INTO _legacy_tenant_map (legacy_tenant_id, tenant_id, brand_id, slug)
        SELECT legacy_tenant_id, gen_random_uuid(), gen_random_uuid(), trim(both '-' from slug)
        FROM resolved
        """
    )
    op.execute(
        "INSERT INTO tenants (id, slug, name) "
        "SELECT tenant_id, slug, legacy_tenant_id FROM _legacy_tenant_map"
    )
    op.execute(
        "INSERT INTO brands (id, owner_tenant_id, slug, name) "
        "SELECT brand_id, tenant_id, 'default', 'Default brand' FROM _legacy_tenant_map"
    )
    op.execute(
        """
        UPDATE social_connections sc
        SET brand_id = m.brand_id, connected_by_tenant_id = m.tenant_id
        FROM _legacy_tenant_map m
        WHERE sc.tenant_id = m.legacy_tenant_id
        """
    )

    # 4. tighten + re-key -------------------------------------------------------------
    op.alter_column("social_connections", "brand_id", nullable=False)
    op.alter_column("social_connections", "connected_by_tenant_id", nullable=False)
    op.create_foreign_key(
        "fk_social_connections_brand_id",
        "social_connections",
        "brands",
        ["brand_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_social_connections_connected_by_tenant_id",
        "social_connections",
        "tenants",
        ["connected_by_tenant_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_social_connections_brand_id", "social_connections", ["brand_id"])
    op.create_unique_constraint(
        "uq_social_connections_brand_identity",
        "social_connections",
        ["brand_id", "provider", "provider_user_id"],
    )
    op.drop_constraint("uq_social_connections_identity", "social_connections", type_="unique")
    op.drop_index("ix_social_connections_tenant_id", table_name="social_connections")
    op.drop_column("social_connections", "tenant_id")

    # 5. page selection ---------------------------------------------------------------
    op.add_column(
        "social_connection_pages",
        sa.Column("is_selected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.execute(
        "UPDATE social_connection_pages SET is_selected = true "
        "WHERE encrypted_page_token IS NOT NULL"
    )
    op.create_check_constraint(
        "ck_social_connection_pages_selected_has_token",
        "social_connection_pages",
        "(is_selected = false) OR (encrypted_page_token IS NOT NULL)",
    )

    # 6. temp table -------------------------------------------------------------------
    op.execute("DROP TABLE IF EXISTS _legacy_tenant_map")


def downgrade() -> None:
    # Re-add the legacy free-text tenant id and backfill it with the owner tenant's slug.
    op.add_column("social_connections", sa.Column("tenant_id", sa.String(64), nullable=True))
    op.execute(
        """
        UPDATE social_connections sc
        SET tenant_id = t.slug
        FROM brands b
        JOIN tenants t ON t.id = b.owner_tenant_id
        WHERE sc.brand_id = b.id
        """
    )
    op.alter_column("social_connections", "tenant_id", nullable=False)

    # Owner note 3: the same Meta account linked to two brands of one owner collapses to
    # one (tenant_id, provider, provider_user_id) triple — keep only the newest row so the
    # legacy unique constraint can be re-created without a UniqueViolation.
    op.execute(
        """
        DELETE FROM social_connections sc
        USING (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY tenant_id, provider, provider_user_id
                       ORDER BY updated_at DESC, connected_at DESC, id DESC
                   ) AS rn
            FROM social_connections
        ) ranked
        WHERE sc.id = ranked.id AND ranked.rn > 1
        """
    )

    op.create_unique_constraint(
        "uq_social_connections_identity",
        "social_connections",
        ["tenant_id", "provider", "provider_user_id"],
    )
    op.create_index("ix_social_connections_tenant_id", "social_connections", ["tenant_id"])

    op.drop_constraint(
        "uq_social_connections_brand_identity", "social_connections", type_="unique"
    )
    op.drop_index("ix_social_connections_brand_id", table_name="social_connections")
    op.drop_constraint(
        "fk_social_connections_connected_by_tenant_id", "social_connections", type_="foreignkey"
    )
    op.drop_constraint("fk_social_connections_brand_id", "social_connections", type_="foreignkey")
    op.drop_column("social_connections", "connected_by_tenant_id")
    op.drop_column("social_connections", "brand_id")

    op.drop_constraint(
        "ck_social_connection_pages_selected_has_token", "social_connection_pages", type_="check"
    )
    op.drop_column("social_connection_pages", "is_selected")

    op.drop_index("uq_brand_access_grants_active", table_name="brand_access_grants")
    op.drop_index("ix_brand_access_grants_grantee_tenant_id", table_name="brand_access_grants")
    op.drop_index("ix_brand_access_grants_brand_id", table_name="brand_access_grants")
    op.drop_table("brand_access_grants")
    op.drop_index("ix_brands_owner_tenant_id", table_name="brands")
    op.drop_table("brands")
    op.drop_table("tenants")
