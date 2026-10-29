# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **ملف التوجيه المباشر بين المخطط والمنفذ.** هذا الملف هو **المصدر الوحيد للمهمة الحالية**. التوجيه السابق (SPRINT-01) أُغلق ومراجعته مؤرشفة في [`sprints/SPRINT-01_REVIEW.md`](./sprints/SPRINT-01_REVIEW.md).

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-02-PERSISTENCE-AND-HARDENING` |
| **المراجعة** | Revision 1 |
| **الحالة** | `READY_FOR_EXECUTION` |
| **يبدأ من** | `main` بعد دمج PR #2 (أي كود SPRINT-01 @ `118c0bb` أو أحدث) |
| **الفرع المستهدف للكود** | `work/implementation` |
| **المخطط المسؤول** | AI Master Planner |
| **المنفذ المستهدف** | AI Executor Agent |
| **تاريخ الإصدار** | 2026-10-29 |
| **بوابات الجودة** | G1 + G2 (اختبارات معزولة) **+ G2.5** (اختبار تكامل حقيقي مع PostgreSQL في Docker) |

---

## 0. نقطة البداية المُتحقَّق منها (Verified Starting Point)

> المخطط شغّل الكود التالي بنفسه: **123 اختباراً ناجحاً، تغطية 99.62%، ruff نظيف، `/docs` يعمل.**

| الموجود على `work/implementation` | الحالة |
|---|---|
| `src/core/config.py` — `Settings` بـ pydantic-settings | ✅ يُعاد استخدامه مع تعديلات (قسم 5-A) |
| `src/core/security.py` — `TokenEncryptionService` (MultiFernet + rotate) | ✅ يُعاد استخدامه كما هو |
| `src/core/database.py` — محرك SQLAlchemy **متزامن** كسول | ⚠️ يُستبدل بمحرك async (قسم 5-B) |
| `src/modules/social_connect/meta_client.py` — `MetaGraphClient` | ✅ يُعاد استخدامه مع إصلاح C4 |
| `src/modules/social_connect/exceptions.py` — `map_graph_error` | ✅ يُعاد استخدامه مع إصلاح ترتيب 401/403 |
| `src/modules/social_connect/schemas.py` | ⚠️ تُضاف نماذج عامة بدون توكنات |
| `src/modules/social_connect/service.py` — `MetaConnectService` | ⚠️ يُوسَّع ليحفظ عبر Repository |
| `src/modules/social_connect/router.py` — 3 مسارات | ⚠️ يُعاد تشكيله (قسم 5-D) |
| `tests/**` — 123 اختباراً | ✅ **يجب أن تبقى خضراء** أو تُحدَّث بوعي مع ذكر السبب |

**قاعدة ذهبية:** لا تعِد كتابة ما يعمل. عدّل بالحد الأدنى، وأضف فوقه.

---

## 1. الهدف التنفيذي (Objective)

تحويل وحدة `social_connect` من «تدفق يُرجع النتيجة للمتصفح» إلى **وحدة ربط حقيقية متعددة المستأجرين** تحفظ الحسابات المربوطة وتوكناتها مشفرة في **PostgreSQL 16**، وتُغلق الثغرات الأربع الحرجة المكتشفة في مراجعة SPRINT-01.

### ينقسم السبرنت إلى جزأين متتابعين (كوميت منفصل لكل جزء):

**Part A — Hardening (إصلاحات حرجة، صغيرة، تُنجز أولاً):**
- C1: Graph API `v19.0` → `v26.0`.
- C2: التحقق الخادمي من `state` (HMAC موقّع).
- C3: تقليص الصلاحيات الافتراضية إلى القراءة فقط.
- C4: إلغاء إعادة المحاولة على Rate Limit.
- تصحيح ترتيب 401/403 (قرار المخطط في المراجعة §5).

**Part B — Persistence (جوهر السبرنت):**
- Docker Compose لـ PostgreSQL 16 + pgvector.
- SQLAlchemy 2 **async** + Alembic.
- جدولان: `social_connections` و `social_connection_pages`.
- `ConnectedAccountRepository` (Protocol) + تنفيذ PostgreSQL.
- `GET /callback` يُكمل الربط ويحفظ.
- مسارات قراءة الحسابات المربوطة **بدون أي توكن في الاستجابة**.
- اختبارات تكامل حقيقية ضد PostgreSQL (G2.5).

### لماذا الآن؟
- بدون تخزين، كل ربط يضيع عند إغلاق المتصفح — المنتج غير قابل للاستخدام.
- D-032 يفرض PostgreSQL؛ تأجيله أكثر يعني بناء محركات لاحقة (E4, E10) على رمال.
- الثغرات C1–C4 تمس الأمان والتوافق مع Meta مباشرة؛ تركها حتى لو يوماً إضافياً يزيد تكلفة الإصلاح.

### النتيجة العملية عند الاكتمال
`docker compose up -d` ⇒ `alembic upgrade head` ⇒ `uvicorn` ⇒ ربط حساب Meta من المتصفح ⇒ الحساب والصفحات محفوظة في PostgreSQL مشفرة ⇒ `GET /connections` يعرضها بدون توكنات ⇒ إعادة تشغيل الخادم لا تفقد شيئاً.

---

## 2. النطاق (Scope)

### ✅ داخل النطاق
كل ما في القسم 1. بالإضافة إلى `.env.template` و `docker-compose.yml` و `alembic/`.

### 🚫 خارج النطاق (ممنوع)
- **النشر** على أي منصة. الصلاحيات تبقى قراءة فقط.
- **مصادقة مستخدمي المنصة** (JWT/RBAC/Keycloak). `tenant_id` يبقى معاملاً صريحاً مؤقتاً.
- **تجديد التوكنات التلقائي / مزامنة دورية / Temporal** — سبرنت لاحق.
- **Redis** — state الموقّع بـ HMAC لا يحتاج تخزيناً.
- **pgvector فعلياً** — فقط الإضافة مفعّلة في Docker للمستقبل؛ لا جداول vector الآن.
- Insights، منشورات، تعليقات، أي منصة غير Meta، الواجهة الأمامية، LiteLLM.
- **لا تحذف اختبارات SPRINT-01**؛ عدّلها فقط إذا تغيّر سلوك عن قصد (وثّق ذلك).

---

## 3. الحدود المسموحة والمحظورة في الملفات

### مسموح
```
pyproject.toml, requirements.txt, requirements-dev.txt
.env.template
docker-compose.yml
alembic.ini
alembic/**
src/**
tests/**
scripts/**                 ← إضافة سكربتات مساعدة فقط
CURRENT_DIRECTIVE.md       ← قسم 11 فقط (تقرير المنفذ)
WORK_STATE.md              ← أقسام 3 و 5 و 6 فقط
```

### محظور
- `AI_Marketing_Platform_Hub/**`, `ARCHITECTURE_LOCK.md`, `imaster_planner.md`, `SESSION_RECOVERY.md`, `README.md`, `sprints/**`.
- أي سر حقيقي في الكود/الاختبارات/الكوميتات/`docker-compose.yml` (كلمة مرور Postgres في Compose تُقرأ من `.env` أو تكون `postgres` التطويرية الواضحة مع تعليق).
- مكتبات AGPL/GPL (D-054).
- SQLite بأي شكل حتى في الاختبارات (D-032). الاختبارات التي تحتاج قاعدة بيانات تستخدم PostgreSQL حقيقية في Docker.
- الكوميت المباشر على `main`.
- إرجاع أي حقل يحوي توكناً (خاماً أو مشفراً أو مُقنَّعاً) من أي مسار API.

---

## 4. القرارات المعمارية الواجب احترامها

| القرار | كيف يظهر في هذا السبرنت |
|---|---|
| **D-013** Modular Monolith | Repository Protocol في `domain/`، تنفيذ PostgreSQL في `infrastructure/`؛ الخدمة لا تعرف SQLAlchemy. |
| **D-032** PostgreSQL 16+ مع pgvector | Docker Compose يستخدم صورة `pgvector/pgvector:pg16` (ترخيص PostgreSQL License — مسموح). `CREATE EXTENSION IF NOT EXISTS vector` في أول migration. **ممنوع SQLite.** |
| **D-054** لا AGPL | التبعيات الجديدة في 5-F كلها MIT/Apache/BSD/PostgreSQL. |
| **Zero-Secret** | التوكنات تُشفَّر قبل `INSERT`، لا تُسجَّل، لا تُرجَع. حتى `"***"` يُلغى: الحقول ببساطة **غير موجودة** في نماذج الاستجابة. |
| **D-055 / Evidence** | `token_expires_at = NULL` يعني «غير معروف» وليس «لا ينتهي». |
| **عزل المستأجرين (F1/D-014)** | كل استعلام مُقيَّد بـ `tenant_id`. قيد فريد `(tenant_id, provider, provider_user_id)`. |

---

## 5. المواصفات الفنية التفصيلية

### 5-A. Part A — Hardening (الإصلاحات الحرجة)

#### A1 — إصدار Graph API (C1)
- `config.py`: `meta_graph_api_version: str = "v26.0"`.
- أضف validator يرفض أي إصدار `< v21.0` برسالة واضحة (`v21.0` أقدم إصدار ما زال حياً حتى 2027-01-21).
- `.env.template`: `META_GRAPH_API_VERSION=v26.0`.
- اختبار: `test_rejects_expired_graph_version` (`v19.0` ⇒ `ValidationError`) و `test_default_graph_version_is_v26`.

#### A2 — الصلاحيات الافتراضية (C3)
- `config.py`: `meta_oauth_scopes = "pages_show_list,pages_read_engagement,instagram_basic"`.
- `.env.template` نفس القيمة مع تعليق: `# Publishing scopes are added in a later sprint after App Review`.
- حدّث الاختبار القائم الذي يفحص الصلاحيات إن وُجد.

#### A3 — إلغاء إعادة المحاولة على Rate Limit (C4)
- `meta_client.py::_request_absolute`: `retryable = isinstance(error, MetaTransientError)` فقط. `MetaRateLimitError` يُرفع فوراً.
- أبقِ `retry_after_seconds` في الاستثناء ليُمرَّر كترويسة `Retry-After` في طبقة الـ API.
- حدّث `test_rate_limit_retries_then_succeeds` ⇒ يصبح `test_rate_limit_raises_immediately_without_retry` مع `assert route.call_count == 1`. أبقِ `test_backoff_honours_retry_after` إن كان يختبر الـ Transient فقط، وإلا عدّله.

#### A4 — ترتيب 401/403 (قرار المراجعة §5)
- `exceptions.py::map_graph_error`: افحص `_PERMISSION_CODES` **قبل** فرع `OAuthException`. النتيجة: `code 10/200-299` ⇒ `MetaPermissionError` (403) حتى لو `type == "OAuthException"`؛ `code 190` ⇒ 401.
- أضف إلى `detail` في `router.py::_to_http` حقل `required_action`:
  - `MetaTokenExpiredError`/`MetaOAuthError` ⇒ `"reconnect"`
  - `MetaPermissionError` ⇒ `"reauthorize_with_additional_scopes"`
  - `MetaRateLimitError` ⇒ `"retry_later"`
  - غير ذلك ⇒ `null`
- اختبار: `test_permission_error_with_oauth_type_maps_to_403`.

#### A5 — `state` موقّع (C2)
ملف جديد `src/core/security/oauth_state.py` (أو داخل `security.py` إن فضّلت ملفاً واحداً — لكن لا تخلطه مع Fernet):
```python
class OAuthStateSigner:
    """Stateless CSRF protection: state = base64url(payload) + "." + base64url(hmac_sha256(payload))."""
    def __init__(self, secret: str, ttl_seconds: int = 600) -> None: ...
    def issue(self, tenant_id: str) -> str                      # payload = {"t": tenant_id, "n": nonce16, "iat": unix_ts}
    def verify(self, state: str) -> str                         # returns tenant_id; raises InvalidOAuthStateError
```
- السر: `Settings.oauth_state_secret: SecretStr` (جديد؛ في `.env.template` مع أمر توليد `secrets.token_urlsafe(32)`).
- `verify` يرفض: توقيع غير مطابق (قارن بـ `hmac.compare_digest`)، `iat` أقدم من TTL، `iat` في المستقبل بأكثر من 60 ثانية، payload مشوّه.
- **ملاحظة Replay:** بدون تخزين لا يمكن منع إعادة استخدام state داخل نافذة TTL. هذا **مقبول مؤقتاً** لأن Meta تُبطل `code` بعد أول استخدام (فإعادة الـ state مع نفس الـ code تفشل عند Meta). وثّق ذلك بتعليق `# TEMPORARY: replay within TTL is bounded by Meta's single-use code; add nonce store (Redis) when auth module lands`.
- اختبارات: roundtrip، تعديل حرف ⇒ رفض، انتهاء TTL ⇒ رفض (استخدم `time` قابل للحقن)، سر مختلف ⇒ رفض.

### 5-B. Part B — طبقة قاعدة البيانات

#### B1 — Docker Compose (`docker-compose.yml` في الجذر)
```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-aigos}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-aigos_dev_only}   # dev only — override in .env
      POSTGRES_DB: ${POSTGRES_DB:-ai_growth_os}
    ports: ["${POSTGRES_PORT:-5432}:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-aigos} -d ${POSTGRES_DB:-ai_growth_os}"]
      interval: 5s
      timeout: 3s
      retries: 10
volumes:
  pgdata:
```
- لا Redis ولا Temporal الآن.

#### B2 — محرك async (`src/core/database.py` — يُستبدل)
- `sqlalchemy[asyncio]>=2.0` + `asyncpg>=0.29`.
- `DATABASE_URL=postgresql+asyncpg://aigos:aigos_dev_only@localhost:5432/ai_growth_os`.
- `create_async_engine(settings.database_url, pool_pre_ping=True, echo=settings.database_echo)` كسول (لا اتصال عند الاستيراد).
- `async_sessionmaker(expire_on_commit=False)`.
- `get_db_session()` كـ async generator dependency: `commit` عند النجاح، `rollback` عند الاستثناء.
- أبقِ `Base(DeclarativeBase)`.
- احذف المحرك المتزامن ودالته. حدّث `tests/core/test_database.py` تبعاً.

#### B3 — Alembic
- `alembic init alembic` مع `env.py` **async** (قالب `async` الرسمي من Alembic).
- `alembic.ini`: `sqlalchemy.url` فارغ؛ `env.py` يقرأ `DATABASE_URL` من `Settings`.
- Migration 0001 `initial_social_connections`:
  - `CREATE EXTENSION IF NOT EXISTS vector;` (تحضير لـ D-032؛ لا جداول vector الآن).
  - الجدولان أدناه.
- اختبار `test_alembic_upgrade_head_and_downgrade_base` ضمن اختبارات التكامل.

#### B4 — المخطط (`src/modules/social_connect/infrastructure/orm.py`)

**`social_connections`**
| العمود | النوع | القيود |
|---|---|---|
| `id` | `UUID` | PK, default `uuid4` |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, index |
| `provider` | `VARCHAR(16)` | NOT NULL (`"meta"`) |
| `provider_user_id` | `VARCHAR(64)` | NOT NULL |
| `provider_user_name` | `VARCHAR(255)` | NULL |
| `encrypted_user_token` | `TEXT` | NOT NULL |
| `token_expires_at` | `TIMESTAMPTZ` | NULL (= غير معروف) |
| `granted_scopes` | `JSONB` | NOT NULL default `[]` |
| `status` | `VARCHAR(32)` | NOT NULL (`active` / `reauth_required` / `revoked`) |
| `connected_at` | `TIMESTAMPTZ` | NOT NULL |
| `last_synced_at` | `TIMESTAMPTZ` | NULL |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL, onupdate |
| **UNIQUE** | `(tenant_id, provider, provider_user_id)` | يمنع التكرار ويدعم إعادة الربط |

**`social_connection_pages`**
| العمود | النوع | القيود |
|---|---|---|
| `id` | `UUID` | PK |
| `connection_id` | `UUID` | FK → `social_connections.id` ON DELETE CASCADE, index |
| `page_id` | `VARCHAR(64)` | NOT NULL |
| `page_name` | `VARCHAR(255)` | NOT NULL |
| `category` | `VARCHAR(255)` | NULL |
| `tasks` | `JSONB` | NOT NULL default `[]` |
| `encrypted_page_token` | `TEXT` | NOT NULL |
| `ig_user_id` | `VARCHAR(64)` | NULL |
| `ig_username` | `VARCHAR(255)` | NULL |
| `ig_name` | `VARCHAR(255)` | NULL |
| `ig_profile_picture_url` | `TEXT` | NULL |
| **UNIQUE** | `(connection_id, page_id)` | |

#### B5 — نماذج المجال وعقد التخزين (`src/modules/social_connect/domain/`)
- `models.py`: `Provider`, `AccountStatus` (StrEnum)، `InstagramAsset`, `PageAsset`, `ConnectedAccount` (Pydantic، كما في مواصفة Rev 2 §6.6 — التوكنات مشفرة فقط).
- `repository.py`:
```python
class ConnectedAccountRepository(Protocol):
    async def upsert(self, account: ConnectedAccount) -> ConnectedAccount: ...   # على (tenant_id, provider, provider_user_id)؛ يستبدل الصفحات كاملة
    async def get(self, tenant_id: str, account_id: UUID) -> ConnectedAccount | None: ...
    async def list_by_tenant(self, tenant_id: str) -> list[ConnectedAccount]: ...
    async def set_status(self, tenant_id: str, account_id: UUID, status: AccountStatus) -> bool: ...
    async def delete(self, tenant_id: str, account_id: UUID) -> bool: ...
```
- `infrastructure/postgres_repository.py`: `PostgresConnectedAccountRepository(session: AsyncSession)` يحقق العقد. `upsert` عبر `INSERT … ON CONFLICT DO UPDATE` على القيد الفريد، ثم حذف الصفحات القديمة وإدراج الجديدة في نفس المعاملة.
- **ممنوع** وجود `InMemory` repository في `src/` — الاختبارات المعزولة تستخدم `AsyncMock`/Fake بسيط داخل `tests/` فقط.

### 5-C. الخدمة (`service.py` — يُوسَّع)
```python
class MetaConnectService:
    def __init__(self, client, encryption, repository, state_signer, settings) -> None: ...
    def start_connection(self, tenant_id: str) -> MetaAuthorizeUrlResponse        # يصدر state موقّع
    async def complete_connection(self, state: str, code: str) -> ConnectedAccount # التدفق الكامل + upsert
    async def list_connections(self, tenant_id: str) -> list[ConnectedAccount]
    async def get_connection(self, tenant_id: str, account_id: UUID) -> ConnectedAccount | None
    async def disconnect(self, tenant_id: str, account_id: UUID) -> bool           # حذف من قاعدة البيانات فقط (لا استدعاء Meta الآن)
```
`complete_connection` بالترتيب:
1. `tenant_id = state_signer.verify(state)`.
2. `exchange_code_for_token(code, settings.meta_oauth_redirect_uri)`.
3. `debug_token(long_lived)` ⇒ `provider_user_id = user_id`، `granted_scopes = scopes`، `token_expires_at = expires_at` (إن `0` أو غائب ⇒ `None`). إن فشل `debug_token` بخطأ غير 190 ⇒ سجّل تحذيراً (بدون التوكن) وتابع بـ `None` و `[]`.
4. `get_user_pages(long_lived)` — وسّع `PAGE_FIELDS` ليشمل `instagram_business_account{id,username,name,profile_picture_url}`.
5. شفّر توكن المستخدم وكل توكن صفحة.
6. `repository.upsert(...)`.
7. أرجع `ConnectedAccount`.

### 5-D. مسارات الـ API (`router.py` — يُعاد تشكيله)

| Method | Path | الوصف | الاستجابة |
|---|---|---|---|
| `POST` | `/api/v1/social/meta/connections/start` | Body `{"tenant_id"}` | `200` `{"authorization_url","state"}` |
| `GET` | `/api/v1/social/meta/callback?code&state` | عودة Meta؛ يُكمل ويحفظ | `201` `ConnectedAccountPublic` |
| `GET` | `/api/v1/social/meta/connections?tenant_id` | القائمة | `200` `list[ConnectedAccountPublic]` |
| `GET` | `/api/v1/social/meta/connections/{id}?tenant_id` | تفاصيل | `200` / `404` |
| `DELETE` | `/api/v1/social/meta/connections/{id}?tenant_id` | فصل | `204` / `404` |

- **احذف** `GET /authorize-url` و `POST /token` و `POST /connect` (يُستبدلان بـ `start` و `callback`). حدّث اختباراتها.
- `callback` مع `?error=access_denied&error_description=…` ⇒ `400` `{"error": {"type": "user_denied", "message": …}}`.
- `callback` مع state غير صالح ⇒ `400` `{"error": {"type": "invalid_oauth_state"}}`.
- `ConnectedAccountPublic` و `PageAssetPublic` في `api/schemas.py` (أو `schemas.py` القائم): **لا حقل `encrypted_*` ولا `*_token` إطلاقاً**. يُبنى بـ `model_validate(account, from_attributes=True)` مع استبعاد صريح.
- الحقن: `get_db_session` → `PostgresConnectedAccountRepository`؛ `httpx.AsyncClient` واحد في `app.state` يُنشأ ويُغلق في `lifespan`.
- `tenant_id` معامل صريح `# TEMPORARY until auth module`.

### 5-E. التكوين (`config.py` — إضافات)
```python
database_url: str = "postgresql+asyncpg://aigos:aigos_dev_only@localhost:5432/ai_growth_os"
oauth_state_secret: SecretStr            # إلزامي
oauth_state_ttl_seconds: int = 600
```

### 5-F. التبعيات الجديدة (كلها مُراجعة الترخيص)
| الحزمة | الحد الأدنى | الترخيص | الغرض |
|---|---|---|---|
| `sqlalchemy[asyncio]` | `>=2.0.30` | MIT | ORM async |
| `asyncpg` | `>=0.29` | Apache-2.0 | سائق PostgreSQL async |
| `alembic` | `>=1.13` | MIT | الهجرات |
| **dev:** `pytest-postgresql` | — | **لا تضفه** (يتطلب تثبيت Postgres محلياً)؛ استخدم Docker Compose مباشرة. |
| **dev:** `testcontainers[postgres]` | `>=4.0` | Apache-2.0 | **اختياري** — إن لم يتوفر Docker socket في بيئتك، تخطَّه واستخدم `DATABASE_URL` من البيئة مع `pytest.mark.integration` و `skipif`. |

**ممنوع:** `psycopg2`, `databases`, `tortoise-orm`, `sqlmodel` (لا حاجة).

---

## 6. الاختبارات الإلزامية

### 6.1 معزولة (G1 + G2) — تعمل بدون Docker
| # | الاختبار | ما يثبته |
|---|---|---|
| U1 | `test_default_graph_version_is_v26` / `test_rejects_expired_graph_version` | A1 |
| U2 | `test_default_scopes_are_read_only` | A2 — لا `manage`/`publish` في الافتراضي |
| U3 | `test_rate_limit_raises_immediately_without_retry` | A3 — `call_count == 1` |
| U4 | `test_transient_5xx_still_retries` | A3 — لم ينكسر سلوك Transient |
| U5 | `test_permission_error_with_oauth_type_maps_to_403` | A4 |
| U6 | `test_required_action_in_error_detail` | A4 — `reconnect` / `reauthorize_with_additional_scopes` / `retry_later` |
| U7 | `test_oauth_state_roundtrip` | A5 |
| U8 | `test_oauth_state_tampered_rejected` | A5 |
| U9 | `test_oauth_state_expired_rejected` | A5 — `time` محقون |
| U10 | `test_oauth_state_wrong_secret_rejected` | A5 |
| U11 | `test_complete_connection_happy_path_with_fake_repo` | 5-C — التدفق كاملاً بـ Fake repository في `tests/` |
| U12 | `test_complete_connection_uses_debug_token_for_identity_and_expiry` | 5-C خطوة 3 |
| U13 | `test_complete_connection_debug_token_failure_degrades_gracefully` | 5-C خطوة 3 — `None`/`[]` |
| U14 | `test_complete_connection_invalid_state_400` | 5-D |
| U15 | `test_callback_user_denied_400` | 5-D |
| U16 | `test_public_schemas_have_no_token_fields` | 5-D — فحص `model_fields` لـ `ConnectedAccountPublic` و `PageAssetPublic`: لا اسم يحوي `token` أو `encrypted` |
| U17 | `test_router_responses_never_contain_tokens` | 5-D — فحص JSON كل المسارات: لا `token`, لا `encrypted`, لا `***`, لا `EAA` |
| U18 | `test_removed_routes_return_404` | `/authorize-url`, `/token`, `/connect` ⇒ 404/405 |

### 6.2 تكامل (G2.5) — تتطلب PostgreSQL حقيقية
- ملف `tests/integration/conftest.py`: يقرأ `TEST_DATABASE_URL` من البيئة؛ إن غاب ⇒ `pytest.skip("integration DB not configured")` على مستوى الحزمة. يُشغّل `alembic upgrade head` مرة واحدة ثم يُنظّف الجداول بين الاختبارات (`TRUNCATE … CASCADE`).
- وسم `@pytest.mark.integration` مُسجَّل في `pyproject.toml`.

| # | الاختبار | ما يثبته |
|---|---|---|
| I1 | `test_alembic_upgrade_head_and_downgrade_base` | B3 — صعود ونزول نظيفان؛ `vector` extension موجودة بعد upgrade |
| I2 | `test_repo_upsert_inserts_connection_and_pages` | B5 |
| I3 | `test_repo_upsert_same_provider_user_updates_not_duplicates` | القيد الفريد؛ الصفحات تُستبدل |
| I4 | `test_repo_tenant_isolation` | المستأجر A لا يرى/يحذف حسابات B |
| I5 | `test_repo_delete_cascades_pages` | FK CASCADE |
| I6 | `test_repo_set_status` | |
| I7 | `test_stored_tokens_are_ciphertext_and_decryptable` | ما في الجدول ≠ الأصل، وفكه = الأصل |
| I8 | `test_full_callback_flow_persists_via_api` | `TestClient` + `respx` لـ Meta + PostgreSQL حقيقية: `start` → `callback` → `GET /connections` يعيد الحساب؛ ثم إعادة إنشاء التطبيق (محاكاة إعادة تشغيل) و `GET` يعيده أيضاً |

### 6.3 الحد الأدنى
- **≥ 18 اختباراً معزولاً جديداً** + **≥ 8 اختبارات تكامل**.
- اختبارات SPRINT-01 الـ 123: تبقى خضراء، أو تُعدَّل بوعي (كل تعديل مذكور في قسم 11 مع سببه). **الحذف ممنوع إلا للمسارات الثلاثة المحذوفة.**
- التغطية على `src/` ≥ 90% عند تشغيل المعزولة + التكامل معاً، و ≥ 85% للمعزولة وحدها.

---

## 7. معايير القبول (Acceptance Criteria)

### Part A
- [ ] `v26.0` افتراضي؛ `v19.0` مرفوض.
- [ ] الصلاحيات الافتراضية قراءة فقط.
- [ ] Rate Limit لا يُعاد؛ Transient يُعاد مرة.
- [ ] `code 200 + OAuthException` ⇒ 403 مع `required_action`.
- [ ] `state` موقّع ويُتحقق منه؛ 4 اختبارات A5 خضراء.
- [ ] **كوميت منفصل** بعنوان `fix(social-connect): SPRINT-02 Part A hardening (C1–C4, 401/403 order)`.

### Part B
- [ ] `docker compose up -d` ⇒ Postgres صحي خلال 30 ثانية.
- [ ] `alembic upgrade head` ينجح على قاعدة فارغة؛ `alembic downgrade base` ينجح.
- [ ] `pytest -m "not integration"` ⇒ Zero Failures بدون Docker.
- [ ] `TEST_DATABASE_URL=… pytest -m integration` ⇒ Zero Failures (≥ 8).
- [ ] `pytest` (الكل) ⇒ Zero Failures، تغطية ≥ 90%.
- [ ] `ruff check` و `ruff format --check` نظيفان.
- [ ] `/docs` يعرض المسارات الخمسة الجديدة فقط + `/health`؛ القديمة غير موجودة.
- [ ] `grep -rn "InMemory" src` ⇒ لا نتائج.
- [ ] `grep -rniE "\"\\*\\*\\*\"" src` ⇒ لا نتائج (أسلوب التقنيع أُلغي لصالح الحذف).
- [ ] `grep -rnE "EAA[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}" src tests alembic docker-compose.yml .env.template` ⇒ لا نتائج.
- [ ] `.env.template` يحوي كل المتغيرات الجديدة (`DATABASE_URL`, `OAUTH_STATE_SECRET`, `OAUTH_STATE_TTL_SECONDS`, `POSTGRES_*`).
- [ ] لم يُلمَس أي ملف محظور.

---

## 8. أوامر التشغيل والتحقق

```bash
# 1) البنية التحتية
docker compose up -d && docker compose ps            # انتظر healthy

# 2) البيئة
pip install -e ".[dev]"
cp .env.template .env                                 # املأ: TOKEN_ENCRYPTION_KEY, OAUTH_STATE_SECRET, META_*
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # TOKEN_ENCRYPTION_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"                                 # OAUTH_STATE_SECRET

# 3) الهجرات
alembic upgrade head

# 4) الجودة
ruff check src tests alembic && ruff format --check src tests alembic
pytest -m "not integration" -q
TEST_DATABASE_URL=postgresql+asyncpg://aigos:aigos_dev_only@localhost:5432/ai_growth_os pytest -m integration -q
pytest -q --cov=src --cov-report=term-missing

# 5) التشغيل
uvicorn src.main:app --reload --port 8000             # http://127.0.0.1:8000/docs
```

### اختبار يدوي للمالك (G3)
نفس خطوات SPRINT-01 (تطبيق Meta في وضع Development + Standard Access) لكن الآن:
1. `POST /api/v1/social/meta/connections/start` بـ `{"tenant_id":"demo"}` ⇒ افتح `authorization_url`.
2. بعد الموافقة يعود المتصفح إلى `/callback` ويعرض الحساب **بدون أي توكن**.
3. `GET /api/v1/social/meta/connections?tenant_id=demo` ⇒ يظهر الحساب.
4. **أوقف الخادم وأعد تشغيله** ⇒ كرر الخطوة 3 ⇒ الحساب ما زال موجوداً.
5. `psql` ⇒ `SELECT encrypted_user_token FROM social_connections;` ⇒ نص مشفر يبدأ بـ `gAAAA` وليس `EAA`.
6. `GET …?tenant_id=other` ⇒ قائمة فارغة.

---

## 9. شروط التوقف الإلزامية (Stop & Ask)

توقف، سجّل في قسم 11، ادفع الكوميت دون إكمال، إذا:
- Docker غير متاح في بيئتك **ولا** يمكنك الوصول إلى PostgreSQL بأي طريقة ⇒ أنجز Part A كاملاً + كود Part B مع الاختبارات المعزولة، وعلّم اختبارات التكامل `skip`، واذكر ذلك صراحة. **لا تستبدل PostgreSQL بـ SQLite.**
- احتجت تبعية غير مدرجة في 5-F.
- تطلّب مطلبٌ تعديلَ ملف محظور أو تعارض مع `ARCHITECTURE_LOCK.md`.
- اكتشفت أن إصلاح C4 يكسر أكثر من 5 اختبارات قائمة بطريقة تستدعي إعادة تصميم (أبلغ قبل الحذف الجماعي).

**لا توسّع النطاق** (لا نشر، لا مزامنة، لا Redis، لا مصادقة).

---

## 10. التزامات المنفذ عند الإنهاء (Handoff)

1. **قبل أي شيء:** تأكد أن `main` يحوي كود SPRINT-01 (`git log origin/main --oneline | grep 118c0bb`). إن لم يكن مدموجاً بعد، **توقف** وأبلغ المالك بدمج PR #2 أولاً.
2. `git checkout work/implementation && git pull`.
3. كوميتان على الأقل: `fix(...)` لـ Part A ثم `feat(social-connect): SPRINT-02 Part B — PostgreSQL persistence, Alembic, callback flow`.
4. **افتح Pull Request** من `work/implementation` إلى `main` بعنوان `SPRINT-02: Persistence (PostgreSQL/Alembic) + Meta connector hardening`، يذكر: عدد الاختبارات المعزولة/التكامل، التغطية، الاختبارات المُعدَّلة من SPRINT-01 وسببها، الأسئلة المفتوحة.
5. املأ **قسم 11**.
6. حدّث `WORK_STATE.md`: قسم 3 (⇒ `AWAITING_PLANNER_REVIEW`)، قسم 5، وسطر في قسم 6 بالكوميت والنتائج.
7. `git push origin work/implementation`. **لا تدمج في `main`.**

---

## 11. تقرير المنفذ عند الانتهاء (يملؤه الوكيل المنفذ)

- **حالة التنفيذ:** `[ ] Part A مكتمل` `[ ] Part B مكتمل` `[ ] متوقف بانتظار قرار`
- **Commit SHA — Part A:** `...`
- **Commit SHA — Part B:** `...`
- **رابط الـ Pull Request:** `...`
- **`pytest -m "not integration"`:** `... passed / ... failed`
- **`pytest -m integration`:** `... passed / ... failed / skipped (السبب)`
- **التغطية الكلية على `src/`:** `...%`
- **`ruff check` / `ruff format --check`:** `...`
- **اختبارات SPRINT-01 المُعدَّلة أو المحذوفة وسبب كل منها:** `...`
- **الملفات الموسومة `# TEMPORARY` وعددها:** `...`
- **تبعيات خارج القائمة ومبرراتها:** `لا يوجد` / `...`
- **انحرافات عن التوجيه ومبرراتها:** `لا يوجد` / `...`
- **ما يحتاج المالك لاختباره يدوياً:** `...`
- **أسئلة مفتوحة للمخطط:** `...`

---

## 12. المراجع (تاريخ الاطلاع: 2026-10-29)

| المرجع | الرابط | الاستخدام |
|---|---|---|
| Graph API Versions / Changelog | https://developers.facebook.com/docs/graph-api/changelog/ | v26.0 حالي؛ v21.0 أقدم حيّ (حتى 2027-01-21)؛ v19.0 منتهٍ |
| Debug Token | https://developers.facebook.com/docs/facebook-login/guides/access-tokens/debugging-and-error-handling | `expires_at`, `scopes`, `user_id`, `is_valid` |
| Rate Limiting | https://developers.facebook.com/docs/graph-api/overview/rate-limiting | «عند الحظر توقف عن الطلب؛ الاستمرار يُطيل الحظر» ⇒ C4 |
| Error Handling | https://developers.facebook.com/docs/graph-api/guides/error-handling | الأكواد 190/10/200–299/4/17/32/613 |
| SQLAlchemy 2 asyncio | https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html | `create_async_engine`, `async_sessionmaker` |
| Alembic async template | https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic | `alembic init -t async` |
| pgvector Docker image | https://hub.docker.com/r/pgvector/pgvector | `pgvector/pgvector:pg16` |
| asyncpg | https://github.com/MagicStack/asyncpg | Apache-2.0 |
| OAuth 2.0 CSRF (`state`) — RFC 6749 §10.12 | https://datatracker.ietf.org/doc/html/rfc6749#section-10.12 | أساس A5 |
