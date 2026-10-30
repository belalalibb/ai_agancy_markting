# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-02.1 رُوجع واعتُمد ودُمج (PR #5 ⇒ `main` @ `df01260`)؛ تقريره في [`sprints/SPRINT-02.1_REVIEW.md`](./sprints/SPRINT-02.1_REVIEW.md). هذا التوجيه يفتح **SPRINT-03**: بوابات CI تحرس كل دمج قادم، ونموذج **Tenant → Brand → Grants** الذي ستُبنى عليه كل الوحدات اللاحقة.

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-03-CI-GATES-AND-TENANCY` |
| **النوع** | بنية تحتية (CI) + نموذج بيانات تأسيسي (Tenancy) + هجرة آمنة |
| **الحالة** | `READY_FOR_EXECUTION` |
| **يبدأ من** | `main` بعد دمج **PR #6** (هذا التوجيه). أول أمر: `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` |
| **الفرع المستهدف** | `work/implementation` ⇒ **PR جديد #7** إلى `main` (PR #5 دُمج وأُغلق) |
| **الكوميتات** | **اثنان بالضبط**: Part A ثم Part B (§6). كل كوميت يُدفع فوراً |
| **الحجم المتوقع** | Part A ~200 سطر؛ Part B ~900–1200 سطر مع الاختبارات |
| **تاريخ الإصدار** | 2026-10-29 |

---

## 0. لماذا هذا السبرنت؟

1. **كل دمج حتى الآن اعتمد على تشغيل بشري/وكيل للاختبارات.** هذا لا يتوسع. من الآن، لا يُدمج شيء في `main` إلا بعد أن تُثبت CI: ruff + وحدة + تكامل على PostgreSQL حقيقية + لا أسرار + لا GPL/AGPL (D-054).
2. **`tenant_id` نص حر في `social_connections`** لا يكفي لمنتج حقيقي: المالك والوكالة يحتاجان وصولاً متزامناً لنفس العلامة التجارية، ووصول الوكالة يجب أن يكون قابلاً للسحب دون نقل ملكية. **قرار المالك المعتمد:** `tenants → brands → social_connections.brand_id` + جدول صغير `brand_access_grants`.
3. **خطر خلط الصفحات (تحذير المالك):** حساب Meta لمدير وكالة يُرجع عبر `/me/accounts` صفحات **عدة عملاء**. لو خُزّنت كلها تحت علامة واحدة لاختلطت بيانات عميل بعميل وخُزّنت توكنات صفحات لا تخص العلامة. الحل في §2-B6: **اختيار الصفحات صراحةً**، ولا يُخزَّن توكن صفحة غير مختارة.
4. ملاحظات مرحَّلة من مراجعة 02.1 (غير حاجبة لكنها تُغلق هنا): `/health/ready` عبر `engine.connect()` (A6)، `alembic.ini path_separator` (A7)، حارس دائم لـ `provider_user_name` (B7).

---

## 1. النطاق

### داخل النطاق
- **Part A — CI & Gates:** `.github/workflows/ci.yml`، `scripts/check_licenses.py`، A6، A7.
- **Part B — Tenancy:** وحدة جديدة `src/modules/tenancy/` (tenants, brands, brand_access_grants)، هجرة `0003` آمنة لـ `social_connections` إلى `brand_id`، إعادة تحديد نطاق مسارات Meta تحت `/brands/{brand_id}/…`، اختيار الصفحات، الحارس B7.

### خارج النطاق (ممنوع)
- أي وحدة مصادقة/JWT/جلسات (الـ`tenant_id` الفاعل يبقى معاملاً صريحاً مؤقتاً — انظر §2-B4).
- تغيير التشفير (D-058 يثبّت Fernet مرحلياً).
- أي اتصال بـ LiteLLM / Temporal / WhatsApp / نشر محتوى.
- واجهات أمامية.
- تعديل الملفات المحظورة: `ARCHITECTURE_LOCK.md`, `SESSION_RECOVERY.md`, `imaster_planner.md`, `README.md`, `AI_Marketing_Platform_Hub/**`, `sprints/**`. (**يُسمح** بتعديل `WORK_STATE.md` و§7 من هذا الملف فقط.)

---

## 2. المواصفات الفنية

### Part A — CI & Gates

#### A1. `.github/workflows/ci.yml`
```yaml
name: ci
on:
  push:
    branches: [main, work/implementation, genspark_ai_developer]
  pull_request:
    branches: [main]
permissions:
  contents: read
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true
jobs:
  lint:        # ruff check + ruff format --check (src tests alembic scripts)
  unit:        # pytest -m "not integration" --cov=src --cov-fail-under=95
  integration: # services: pgvector/pgvector:pg16 ⇒ pytest -m integration
  secrets:     # gitleaks/gitleaks-action@v2 (MIT) — fetch-depth: 0
  licenses:    # python scripts/check_licenses.py (في venv نظيف يُثبّت `pip install .` فقط)
```
- Python **3.12** (الحد الأدنى في `requires-python`)؛ `actions/setup-python@v5` مع `cache: pip`؛ `actions/checkout@v4`.
- وظيفة `integration`:
  ```yaml
  services:
    postgres:
      image: pgvector/pgvector:pg16
      env: { POSTGRES_USER: postgres, POSTGRES_PASSWORD: postgres, POSTGRES_DB: ai_growth_os_test }
      ports: ["5432:5432"]
      options: >-
        --health-cmd "pg_isready -U postgres" --health-interval 5s --health-timeout 5s --health-retries 10
  env:
    TEST_DATABASE_URL: postgresql+asyncpg://postgres:postgres@localhost:5432/ai_growth_os_test
  ```
  `CREATE EXTENSION vector` تتولاه هجرة `0001` بالفعل — لا خطوة يدوية.
- **كل وظيفة من الخمس يجب أن تُطبع كفحص مستقل** (ليست خطوات داخل وظيفة واحدة) ليتمكن المالك من جعلها Required في حماية الفرع (§8).
- لا `continue-on-error` في أي مكان. لا أسرار مطلوبة في الـworkflow (gitleaks على حساب شخصي لا يحتاج ترخيصاً).

#### A2. `scripts/check_licenses.py` — بوابة D-054
- يقرأ `importlib.metadata.distributions()` للبيئة الحالية (CI تشغّله داخل venv فيه `pip install .` **بلا** `[dev]`؛ محلياً يُشغَّل بنفس الطريقة).
- يستخرج الترخيص: `License-Expression` ⇒ وإلا `License` (إن كان ≤ 60 حرفاً) ⇒ وإلا مصنّفات `License ::`.
- **رفض (exit 1)** إن طابق أيٌّ: `(?<![L\w])GPL`, `AGPL`, `Affero`, `SSPL`, `BUSL`, `Commons Clause`, `EUPL`, أو كان `UNKNOWN`/فارغاً.
- **قبول** للقائمة البيضاء: MIT, MIT-0, BSD-2/3-Clause, Apache-2.0, PSF-2.0, ISC, MPL-2.0, Unlicense, Python-2.0, Zlib، وأي تعبير `AND`/`OR` كل أطرافه مقبولة.
- أي ترخيص خارج القائمتين ⇒ **رفض** مع رسالة واضحة (لا منطقة رمادية صامتة).
- ملف استثناءات اختياري `scripts/license_allowlist.txt` (`package==version # reason`) — **يُسلَّم فارغاً**؛ أي إضافة لاحقة تحتاج مراجعة المخطط.
- يطبع جدولاً `package | version | license | verdict` دائماً. حزمة المشروع نفسها (`ai-growth-os`, Proprietary) مستثناة بالاسم.
- **ملاحظة تحقق المخطط:** شجرة التشغيل الحالية (36 حزمة) كلها MIT/BSD/Apache/PSF/MPL-2.0 ⇒ البوابة تمر يوم تفعيلها. إن فشلت عندك فالمشكلة في السكربت لا في التبعيات.

#### A3. اختبارات السكربت
`tests/scripts/test_check_licenses.py`: يحقن قائمة توزيعات وهمية (لا يعتمد على بيئة التشغيل) ويثبت: GPL يُرفض، `Apache-2.0 OR BSD-3-Clause` يُقبل، `UNKNOWN` يُرفض، `LGPL-2.1` يُرفض (خارج القائمة البيضاء)، allowlist تُطبّق فقط على الزوج الدقيق `name==version`.

#### A4. `pyproject.toml`
- لا تبعيات جديدة لـ Part A (السكربت ذاتي، بلا `pip-licenses`).
- أضف `scripts` إلى مسارات ruff إن لم تكن مشمولة.

#### A5. `README.md` محظور — لا توثّق CI هناك. ضع التوثيق في رأس `ci.yml` كتعليقات.

#### A6. `/health/ready` بلا جلسة ORM (مرحَّل من مراجعة 02.1 §3)
```python
@app.get("/health/ready", ...)
async def health_ready() -> JSONResponse:
    try:
        async with database.get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError):
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "unreachable"})
    return JSONResponse(status_code=200, content={"status": "ready", "database": "ok"})
```
- لا `Depends(get_db_session)`، لا معاملة، لا `commit`.
- الاختبارات في `tests/test_main.py` تُعدَّل لتحقن محركاً وهمياً عبر `monkeypatch` على `database.get_engine` (أو `app.dependency_overrides` لتبعية `get_engine` جديدة — اختر واحداً والتزم به).
- اختبار جديد `test_health_ready_has_no_session_dependency`: يفحص `app.routes` ويثبت أن مسار `/health/ready` ليس في تبعياته `get_db_session`.
- اختبار تكامل `test_health_ready_live_200` على PG حقيقية.

#### A7. `alembic.ini`: أضف `path_separator = os` (يُسكت التحذير). لا تغيير آخر.

---

### Part B — Tenancy (قرار المالك: الخيار ج المبسّط)

#### B1. الوحدة الجديدة `src/modules/tenancy/`
بنفس بنية `social_connect`: `domain/models.py`, `domain/repository.py` (Protocol), `infrastructure/orm.py`, `infrastructure/postgres_repository.py`, `service.py`, `schemas.py`, `router.py`, `exceptions.py`. **لا استيراد من `social_connect` إلى `tenancy`** (الاتجاه الوحيد المسموح: `social_connect` ⇒ `tenancy.domain`).

#### B2. الجداول (هجرة `0003_tenancy.py`)

**`tenants`**
| العمود | النوع | قيود |
|---|---|---|
| `id` | UUID PK | `uuid4` |
| `slug` | VARCHAR(64) | **UNIQUE**؛ regex `^[a-z0-9](?:[a-z0-9-]{1,62}[a-z0-9])?$` (3–64) |
| `name` | VARCHAR(255) | NOT NULL |
| `created_at`, `updated_at` | TIMESTAMPTZ | server default `now()` |

**`brands`**
| العمود | النوع | قيود |
|---|---|---|
| `id` | UUID PK | |
| `owner_tenant_id` | UUID FK → `tenants.id` **ON DELETE RESTRICT** | NOT NULL، index |
| `slug` | VARCHAR(64) | نفس regex؛ **UNIQUE (`owner_tenant_id`, `slug`)** |
| `name` | VARCHAR(255) | NOT NULL |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

**`brand_access_grants`**
| العمود | النوع | قيود |
|---|---|---|
| `id` | UUID PK | |
| `brand_id` | UUID FK → `brands.id` **ON DELETE CASCADE** | NOT NULL، index |
| `grantee_tenant_id` | UUID FK → `tenants.id` **ON DELETE CASCADE** | NOT NULL، index |
| `role` | VARCHAR(16) | CHECK IN (`'manager'`, `'viewer'`) |
| `granted_by_tenant_id` | UUID FK → `tenants.id` ON DELETE RESTRICT | NOT NULL (= المالك وقت المنح) |
| `granted_at` | TIMESTAMPTZ | NOT NULL |
| `revoked_at` | TIMESTAMPTZ | NULL = ساري |
| فهرس | **فريد جزئي** `uq_brand_access_grants_active (brand_id, grantee_tenant_id) WHERE revoked_at IS NULL` | يسمح بإعادة المنح بعد السحب مع إبقاء سجل التدقيق |

**تعديل `social_connections`**
- إضافة `brand_id UUID FK → brands.id ON DELETE CASCADE NOT NULL` (index).
- إضافة `connected_by_tenant_id UUID FK → tenants.id ON DELETE RESTRICT NOT NULL` (مَن أجرى OAuth: المالك أو الوكالة).
- **حذف** `tenant_id` (النص الحر) وقيد `uq_social_connections_identity` وفهرس `ix_social_connections_tenant_id`.
- قيد فريد جديد `uq_social_connections_brand_identity (brand_id, provider, provider_user_id)` — **نفس حساب Meta يُربط بعلامتين مختلفتين = صفّان** (هذا هو حال مدير الوكالة).

**تعديل `social_connection_pages`**
- إضافة `is_selected BOOLEAN NOT NULL DEFAULT false`.
- قيد CHECK `ck_social_connection_pages_selected_has_token`: `(is_selected = false) OR (encrypted_page_token IS NOT NULL)` — صفحة مختارة بلا توكن حالة مستحيلة. **صفحة غير مختارة بتوكن** ممنوعة أيضاً لكنها تُفرض في الخدمة (B6) لأن الحالة الانتقالية داخل المعاملة تحتاجها.

#### B3. الهجرة الآمنة `0003` — خوارزمية `upgrade()` (بيانات موجودة لا تُفقد)
1. إنشاء الجداول الثلاثة.
2. `ALTER TABLE social_connections ADD COLUMN brand_id UUID NULL, ADD COLUMN connected_by_tenant_id UUID NULL`.
3. **Backfill بـ SQL خام** (لا ORM داخل الهجرات) عبر جدول وسيط مؤقت `_legacy_tenant_map(legacy_tenant_id, tenant_id, brand_id)`:
   - لكل `DISTINCT tenant_id` قديم: `INSERT INTO tenants (id, slug, name) VALUES (gen_random_uuid(), <slug>, <tenant_id>)` حيث `<slug>` = `lower(tenant_id)` بعد استبدال أي حرف خارج `[a-z0-9-]` بـ `-` وقصّ الشرطات الطرفية؛ إن تصادم slug مع آخر أو كان أقصر من 3 ⇒ ألحق `-` + أول 8 أحرف من `md5(tenant_id)`. (`gen_random_uuid()` مدمج في PG 13+.)
   - لكل مستأجر مُنشأ: علامة واحدة `INSERT INTO brands (owner_tenant_id, slug, name) VALUES (<id>, 'default', 'Default brand')`.
   - `UPDATE social_connections sc SET brand_id = m.brand_id, connected_by_tenant_id = m.tenant_id FROM _legacy_tenant_map m WHERE sc.tenant_id = m.legacy_tenant_id`.
4. `ALTER … SET NOT NULL` للعمودين، إضافة FK والفهارس والقيد الفريد الجديد، إسقاط القديم، إسقاط `tenant_id`.
5. `social_connection_pages`: إضافة `is_selected` بـ DEFAULT false ثم **`UPDATE … SET is_selected = true WHERE encrypted_page_token IS NOT NULL`** (الصفحات القديمة ذات التوكن تُعتبر مختارة — لا نكسر عملاً قائماً)، ثم إضافة CHECK.
6. إسقاط `_legacy_tenant_map`.

`downgrade()`: إعادة `tenant_id VARCHAR(64) NULL` ⇒ backfill `tenant_id = t.slug` عبر `brands.owner_tenant_id` ⇒ NOT NULL ⇒ إعادة القيد/الفهرس القديمين ⇒ إسقاط الأعمدة والقيود الجديدة ⇒ إسقاط `is_selected` والـCHECK ⇒ إسقاط الجداول الثلاثة بالترتيب العكسي. **(يُوثَّق في docstring: النزول يفقد تمييز `connected_by` والمنح — مقبول.)**

**اختبار إلزامي (I-B1):** صفّان قديمان بمستأجرين مختلفين + صفحة بتوكن وصفحة بلا توكن ⇒ `upgrade` ⇒ تحقق المستأجرَين والعلامتين والربط و`is_selected` ⇒ `downgrade` ⇒ `tenant_id` عاد بقيمة slug ⇒ `upgrade` مجدداً ينجح.

#### B4. هوية الفاعل (مؤقتة حتى وحدة المصادقة)
- تبعية واحدة مركزية في `src/modules/tenancy/dependencies.py`:
  ```python
  ActorTenantId = Annotated[UUID, Query(alias="tenant_id", description="TEMPORARY: acting tenant until auth module")]
  ```
  وللطلبات ذات الجسم: حقل `tenant_id: UUID` في الـschema. **هذا هو المكان الوحيد** الذي سيُستبدل لاحقاً بالمصادقة؛ أي قراءة أخرى لـ`tenant_id` من الطلب مخالفة.
- `tenant_id` الآن **UUID** (كان نصاً حراً). `tests/modules/social_connect/*` تُحدَّث تبعاً لذلك.

#### B5. حل الوصول (`BrandAccessResolver`)
- Protocol في `tenancy/domain/access.py`:
  ```python
  class BrandRole(StrEnum): OWNER="owner"; MANAGER="manager"; VIEWER="viewer"
  class BrandAccessResolver(Protocol):
      async def resolve(self, tenant_id: UUID, brand_id: UUID) -> BrandRole | None: ...
  ```
- التنفيذ: المالك ⇒ `OWNER`؛ منحة سارية (`revoked_at IS NULL`) ⇒ دورها؛ غير ذلك ⇒ `None`.
- **سياسة الاستجابة:** `None` ⇒ **404 `brand_not_found`** (لا 403 — منع تعداد العلامات). دور غير كافٍ ⇒ **403 `insufficient_role`**.
- مصفوفة الصلاحيات:

| العملية | OWNER | MANAGER | VIEWER |
|---|---|---|---|
| قراءة العلامة / قائمة الاتصالات / تفاصيل اتصال | ✅ | ✅ | ✅ |
| بدء اتصال Meta / اختيار الصفحات / فصل اتصال | ✅ | ✅ | ❌ |
| منح/سحب/عرض المنح | ✅ | ❌ | ❌ |

#### B6. اختيار الصفحات — الحل لخطر خلط الصفحات
- **في `callback`:** بعد `/me` و`/debug_token` و`/me/accounts`:
  - إن أُرجعت **صفحة واحدة بالضبط** ⇒ تُحفظ **مختارة** مع توكنها و IG (لا احتمال خلط).
  - إن أُرجعت **صفحتان فأكثر** ⇒ تُحفظ كلها **بيانات وصفية فقط**: `is_selected=false`, `encrypted_page_token=NULL`, حقول IG تُحفظ (ليست سراً)، `has_page_token=false`. **لا يُخزَّن أي توكن صفحة.**
  - إن أُرجعت صفر صفحات ⇒ الاتصال يُحفظ بلا صفحات (كما الآن).
- **مسار جديد:** `PUT /api/v1/brands/{brand_id}/social/meta/connections/{connection_id}/pages/selection`
  - الجسم: `{"tenant_id": UUID, "page_ids": [str, ...]}` (1–50 عنصراً، فريدة).
  - الدور المطلوب: OWNER/MANAGER.
  - الخوارزمية: فك توكن المستخدم ⇒ `get_user_pages` من جديد (التنفيذ الحالي بالترقيم المُسقَّف) ⇒ أي `page_id` مطلوب غير موجود في النتيجة ⇒ **422 `unknown_page_ids`** مع القائمة، **ولا تغيير** في القاعدة ⇒ وإلا داخل معاملة واحدة: الصفحات المطلوبة تُحدَّث بالتوكن المشفَّر + IG + `is_selected=true`؛ أي صفحة كانت مختارة ولم تعد في القائمة ⇒ `encrypted_page_token=NULL`, `is_selected=false`؛ صفحات جديدة ظهرت في Meta ولم تكن مخزنة تُضاف غير مختارة.
  - توكن مستخدم منتهٍ (190) ⇒ 401 بالخريطة الحالية + `status=reauth_required`.
  - يعيد `ConnectedAccountOut` المحدَّث.
- `PageAssetOut` يكتسب `is_selected: bool`. القائمة تُرجع **كل** الصفحات مع العلمين `is_selected`/`has_page_token` (واجهة الاختيار تحتاجها).
- **ثابت يُفرض في الخدمة واختبار:** لا يوجد أبداً صف `is_selected=false AND encrypted_page_token IS NOT NULL` بعد انتهاء أي عملية.

#### B7. حارس `provider_user_name` (شرط المالك)
- يُحفظ من `/me.name` (بيانات عرض).
- **لا يُسجَّل:** اختبار `test_identity_and_secrets_never_logged` يشغّل التدفق الكامل (start ⇒ callback ⇒ selection ⇒ disconnect) مع `caplog` على مستوى `DEBUG` لجذر `src` ويثبت أن `provider_user_name` و`provider_user_id` والتوكنات (خام ومشفَّر) **لا تظهر** في أي سجل.
- **يُحذف عند `disconnect`:** اختبار تكامل يثبت بعد `DELETE` أن `SELECT count(*) FROM social_connections WHERE provider_user_name = :name` = 0 و`social_connection_pages` للاتصال = 0.
- **يُحذف عند سحب المنحة (B8).**

#### B8. سحب المنحة يمسح اتصالات الوكالة
- `DELETE /brands/{brand_id}/grants/{grant_id}`: يضع `revoked_at = now()` **و** في نفس المعاملة يحذف (حذفاً فعلياً) كل `social_connections` حيث `brand_id = :brand AND connected_by_tenant_id = :grantee` (مع صفحاتها بالـCASCADE). المبرر: التوكن ملك حساب Meta الشخصي لموظف الوكالة؛ لا يبقى بحوزة العلامة بعد انتهاء التفويض (Zero-Secret).
- اتصالات المالك نفسه واتصالات نفس الوكالة في علامات أخرى **لا تُمس** (اختبار صريح).

#### B9. المسارات (كلها تحت `settings.api_v1_prefix`)

| الطريقة | المسار | الجسم/المعاملات | الدور | الاستجابة |
|---|---|---|---|---|
| POST | `/tenants` | `{slug, name}` | — | 201 `TenantOut` / 409 `tenant_slug_taken` |
| GET | `/tenants/{tenant_id}` | `?tenant_id=` يجب أن يساوي المسار وإلا 404 | نفس المستأجر | 200 |
| POST | `/brands` | `{tenant_id, slug, name}` | المالك = `tenant_id` | 201 `BrandOut` / 409 `brand_slug_taken` / 404 مستأجر غير موجود |
| GET | `/brands` | `?tenant_id=` | — | 200 `list[BrandWithRoleOut]` (المملوكة + الممنوحة السارية، مع `role`) |
| GET | `/brands/{brand_id}` | `?tenant_id=` | أي دور | 200 `BrandWithRoleOut` / 404 |
| POST | `/brands/{brand_id}/grants` | `{tenant_id, grantee_tenant_id, role}` | OWNER | 201 `GrantOut` / 404 / 403 / 422 `grantee_is_owner` / 409 `grant_already_active` |
| GET | `/brands/{brand_id}/grants` | `?tenant_id=` | OWNER | 200 قائمة (السارية والمسحوبة مع `revoked_at`) |
| DELETE | `/brands/{brand_id}/grants/{grant_id}` | `?tenant_id=` | OWNER | 204 (B8) / 404 |
| POST | `/brands/{brand_id}/social/meta/connections/start` | `{tenant_id}` | OWNER/MANAGER | 200 كما الآن؛ الـ`state` يحمل `{t, b, n, iat}` |
| GET | `/social/meta/callback` | `code, state` | يُستنتج من `state` | **غير مرتبط بعلامة في المسار** (عنوان الرجوع المسجَّل لدى Meta ثابت)؛ قبل أي نداء Graph: `resolve(t, b)` ∈ {OWNER, MANAGER} وإلا 404/403 **ولا نداء Graph ولا صف** |
| GET | `/brands/{brand_id}/social/meta/connections` | `?tenant_id=` | أي دور | 200 |
| GET | `/brands/{brand_id}/social/meta/connections/{id}` | `?tenant_id=` | أي دور | 200 / 404 |
| PUT | `/brands/{brand_id}/social/meta/connections/{id}/pages/selection` | B6 | OWNER/MANAGER | 200 / 422 / 403 |
| DELETE | `/brands/{brand_id}/social/meta/connections/{id}` | `?tenant_id=` | OWNER/MANAGER | 204 / 404 / 403 |

- المسارات القديمة `/social/meta/connections*` **تُحذف** (404)؛ `/social/meta/callback` يبقى في موضعه.
- `OAuthStateSigner`: `issue(tenant_id: UUID, brand_id: UUID)`؛ `verify()` يُرجع `(tenant_id, brand_id)`؛ أي payload بلا `b` أو بقيم غير UUID ⇒ `InvalidOAuthStateError`. اختبارات `tests/core/test_oauth_state.py` تُحدَّث (الاختبار الحتمي F1 يبقى بمنطقه).

#### B10. الاستثناءات والأخطاء
- `tenancy/exceptions.py`: `TenantNotFoundError`, `TenantSlugTakenError`, `BrandNotFoundError` (يُستخدم أيضاً لحجب الوجود), `BrandSlugTakenError`, `InsufficientRoleError`, `GranteeIsOwnerError`, `GrantAlreadyActiveError`, `GrantNotFoundError`.
- `social_connect/exceptions.py`: `UnknownPageIdsError(page_ids: list[str])` ⇒ 422 بجسم `{"error":"unknown_page_ids","page_ids":[...]}`.
- خريطة HTTP موحدة في `router.py` لكل وحدة بنفس أسلوب `_to_http` الحالي.

#### B11. الحارس Zero-Secret
- حارسا الاختبار الحاليان (`"token" not in name` مع استثناء `has_page_token`) يُوسَّعان ليشملا **كل** schemas الوحدتين و`/openapi.json` الكامل. لا استثناءات جديدة.

#### B12. `docker-compose.yml` و`.env.template`
- لا تغيير مطلوب؛ إن احتجت متغيراً جديداً فسجّله في `.env.template` **بقيمة فارغة** فقط.

---

## 3. الاختبارات الإلزامية

### Part A
| # | الاختبار | المكان |
|---|---|---|
| A-T1 | `test_health_ready_200_with_fake_engine` / `_503_on_sqlalchemy_error` / `_503_on_os_error` (محرك وهمي، لا جلسة) | `tests/test_main.py` |
| A-T2 | `test_health_ready_has_no_session_dependency` | `tests/test_main.py` |
| A-T3 | `test_health_ready_live_200` على PG حقيقية | `tests/integration/` |
| A-T4 | 5 اختبارات `check_licenses` (§A3) | `tests/scripts/test_check_licenses.py` |
| A-T5 | **CI خضراء على PR #7** — رابط التشغيل يُلصق في §7 (الوظائف الخمس ظاهرة كفحوص منفصلة) | GitHub |

### Part B — وحدة (fakes، بلا I/O)
| # | الاختبار |
|---|---|
| B-U1 | slug: يقبل `acme-co`، يرفض `Acme`, `-acme`, `ab`, 65 حرفاً, `a_b`؛ تكرار ⇒ 409 |
| B-U2 | إنشاء علامة؛ تكرار `(owner, slug)` ⇒ 409؛ نفس slug لمالكين مختلفين ⇒ مقبول |
| B-U3 | `resolve`: مالك ⇒ OWNER؛ منحة manager ⇒ MANAGER؛ منحة viewer ⇒ VIEWER؛ منحة مسحوبة ⇒ None؛ غريب ⇒ None |
| B-U4 | منح: فاعل غير مالك ⇒ **404** (لا 403)؛ `grantee == owner` ⇒ 422؛ منحة سارية مكررة ⇒ 409؛ سحب ثم إعادة منح ⇒ 201 |
| B-U5 | B8: سحب منحة وكالة يحذف اتصالاتها في **هذه** العلامة فقط؛ اتصالات المالك واتصالات الوكالة في علامة أخرى باقية |
| B-U6 | VIEWER: list/get ⇒ 200؛ start/selection/disconnect ⇒ 403 |
| B-U7 | start يضمّن `b` في `state`؛ callback بـ`state` لمستأجر فقد الوصول (منحة سُحبت بين start وcallback) ⇒ 404 **و** `me_route.call_count == 0` **و** لا صف |
| B-U8 | callback بصفحتين ⇒ كلتاهما `is_selected=false`, `has_page_token=false`, IG محفوظة؛ بصفحة واحدة ⇒ مختارة بتوكن |
| B-U9 | selection: اختيار 1 من 3 ⇒ الواحدة بتوكن ومختارة، الاثنتان بلا توكن؛ إلغاء اختيار سابق ⇒ التوكن يُمحى؛ `page_id` مجهول ⇒ 422 ولا تغيير؛ 190 أثناء إعادة الجلب ⇒ 401 + `reauth_required` |
| B-U10 | نفس `provider_user_id` في علامتين ⇒ صفّان مستقلان؛ selection في علامة لا تؤثر على الأخرى |
| B-U11 | B7: `caplog(DEBUG)` عبر التدفق الكامل خالٍ من الاسم/المعرّف/التوكنات |
| B-U12 | ثابت B6: بعد كل سيناريو أعلاه لا صفحة `is_selected=false` بتوكن (يُفحص عبر fake repo) |
| B-U13 | حارس Zero-Secret الموسّع على `/openapi.json` كاملاً |
| B-U14 | `OAuthStateSigner`: payload بلا `b` ⇒ مرفوض؛ `b` ليس UUID ⇒ مرفوض؛ الحتمية F1 باقية |

### Part B — تكامل (PostgreSQL حقيقية)
| # | الاختبار |
|---|---|
| I-B1 | هجرة `0003` بصفوف قديمة (B3) ⇒ upgrade ⇒ تحقق ⇒ downgrade ⇒ تحقق ⇒ upgrade |
| I-B2 | `uq_social_connections_brand_identity`: نفس المستخدم في نفس العلامة ⇒ `ON CONFLICT` تحديث (السلوك الحالي)؛ في علامتين ⇒ صفّان |
| I-B3 | حذف علامة ⇒ CASCADE يحذف اتصالاتها وصفحاتها ومنحها؛ حذف مستأجر يملك علامة ⇒ يفشل (RESTRICT) |
| I-B4 | الفهرس الجزئي للمنح: منحتان ساريتان ⇒ خطأ تكامل؛ سحب ثم منح ⇒ ينجح |
| I-B5 | B7: بعد `disconnect` الاسم غير موجود في أي صف؛ الصفحات 0 |
| I-B6 | B8 عبر API على PG: سحب المنحة ⇒ اتصالات الوكالة 0، اتصالات المالك باقية |
| I-B7 | CHECK `is_selected ⇒ token`: إدراج مباشر لصف مختار بلا توكن ⇒ `IntegrityError` |
| I-B8 | التدفق الكامل عبر API على PG مع respx: tenant ⇒ brand ⇒ grant(manager) ⇒ start (بالوكالة) ⇒ callback (3 صفحات) ⇒ selection (1) ⇒ list ⇒ disconnect |

---

## 4. معايير القبول

- [ ] **CI خضراء** على PR #7 بخمسة فحوص منفصلة (`lint`, `unit`, `integration`, `secrets`, `licenses`).
- [ ] `pytest -m "not integration"` و`pytest -m integration` والحزمة كاملة: **0 فشل**؛ تغطية **≥ 95%** (`--cov-fail-under=95`).
- [ ] `ruff check` و`ruff format --check` نظيفان على `src tests alembic scripts`.
- [ ] `grep -rn "Depends(get_db_session)" src/main.py` ⇒ لا نتائج.
- [ ] `grep -rn "tenant_id: str" src` ⇒ لا نتائج (UUID في كل مكان).
- [ ] `alembic upgrade head` على قاعدة فيها صفوف قديمة ينجح؛ `downgrade 0002` ثم `upgrade head` ينجح.
- [ ] `/openapi.json`: لا مسار `/social/meta/connections` خارج `/brands/{brand_id}/`؛ `/social/meta/callback` موجود؛ لا حقل باسم يحوي `token` إلا `has_page_token`.
- [ ] اختبارا B7 (U11, I-B5) موجودان بالاسم وناجحان.
- [ ] `python scripts/check_licenses.py` في venv نظيف (`pip install .`) ⇒ exit 0 وجدول مطبوع.
- [ ] كوميتان بالضبط على `work/implementation` فوق `main` (+ كوميت توثيق اختياري)؛ لا ملف محظور مُسّ؛ فحص الأسرار نظيف.
- [ ] §7 أدناه مملوء و`WORK_STATE.md` §1/§3/§5/§6 محدَّثة.

---

## 5. أوامر التشغيل

```bash
# ابدأ
git checkout work/implementation && git fetch origin && git merge --ff-only origin/main
pip install -e ".[dev]"

# PostgreSQL محلية (إن لم يتوفر Docker — نفس طريقة 02.1)
export TEST_DATABASE_URL=postgresql+asyncpg://aigos@localhost:5433/ai_growth_os_test

# حلقة العمل
ruff check src tests alembic scripts && ruff format --check src tests alembic scripts
pytest -m "not integration" --cov=src --cov-fail-under=95
pytest -m integration
pytest --cov=src --cov-report=term-missing

# بوابة التراخيص كما تُشغَّل في CI
python -m venv /tmp/lic && /tmp/lic/bin/pip install -q . && /tmp/lic/bin/python scripts/check_licenses.py

# فحوص القبول
grep -rn "Depends(get_db_session)" src/main.py
grep -rn "tenant_id: str" src
git diff --name-only origin/main...HEAD | grep -E "^(ARCHITECTURE_LOCK|SESSION_RECOVERY|imaster_planner|README)\.md$|^AI_Marketing_Platform_Hub/|^sprints/" && echo "FORBIDDEN TOUCHED" || echo "forbidden OK"
```

---

## 6. التزامات المنفذ عند الإنهاء

1. **كوميت Part A** برسالة: `ci: SPRINT-03 Part A — GitHub Actions gates (lint/unit/integration-pg16/secrets/licenses), readiness via engine.connect, alembic path_separator` ⇒ **ادفع فوراً** ⇒ افتح **PR #7** `work/implementation → main` بعنوان `SPRINT-03: CI gates + Tenancy (tenants/brands/grants)` ⇒ تأكد أن CI تعمل على PR #7 قبل بدء Part B (إن فشلت لسبب في الـworkflow أصلحه داخل كوميت Part A بـ`--amend` + `push -f` **فقط قبل** بدء Part B).
2. **كوميت Part B** برسالة: `feat(tenancy): SPRINT-03 Part B — tenants/brands/brand_access_grants, migration 0003, brand-scoped Meta connections, explicit page selection` ⇒ **ادفع فوراً**.
3. املأ **§7** أدناه (كوميت ثالث للتوثيق مسموح: `docs(executor): SPRINT-03 handoff`).
4. حدّث `WORK_STATE.md`: §1 ⇒ `SPRINT_03_DELIVERED_AWAITING_REVIEW`؛ §3 الحالة؛ §5 الخطوة التالية؛ §6 سطر جديد في الأعلى.
5. **لا تدمج PR #7 بنفسك.** المخطط يراجع ويعتمد.
6. أي سؤال يعيق التنفيذ: اكتبه في §7 «أسئلة»، نفّذ ما لا يعتمد عليه، **ولا تخمّن في قرارات البيانات** (الهجرة خاصة).

---

## 7. تقرير المنفذ (يملؤه المنفذ)

- **حالة التنفيذ:** `[x] مكتمل` / `[ ] جزئي — السبب:` —
- **Commit SHA Part A:** `a985d55` — **Part B:** `b712df0` — **docs:** (كوميت `docs(executor): SPRINT-03 handoff` فوق `b712df0`؛ SHA في `WORK_STATE.md` §6)
- **PR #7:** https://github.com/belalalibb/ai_agancy_markting/pull/7 — **رابط تشغيل CI الأخضر (Part B `b712df0`):** push https://github.com/belalalibb/ai_agancy_markting/actions/runs/40979056739 · pull_request https://github.com/belalalibb/ai_agancy_markting/actions/runs/40979061696 — الفحوص الخمسة `lint` / `unit` / `integration` / `secrets` / `licenses` ظاهرة منفصلة وخضراء. (Part A `a985d55` كان أخضر أيضاً: runs/40973042472 و runs/40973047744.)
- **`pytest -m "not integration"` / `-m integration` / الكل:** **298 ناجحاً** (1 skipped = حزمة التكامل بلا `TEST_DATABASE_URL`) / **17 ناجحاً** على PG 17.11 حقيقية / **315 ناجحاً، 0 فشل**.
- **التغطية:** **99.68%** (`--cov-fail-under=95`؛ الملفان دون 100%: `social_connect/exceptions.py` 98%، `tenancy/service.py` 98%).
- **ruff:** `ruff check src tests alembic scripts` ⇒ All checks passed · `ruff format --check` ⇒ 63 ملفاً مهيأة.
- **`check_licenses.py` في venv نظيف:** exit **0** / **37 حزمة** / خارج القائمة البيضاء: **لا شيء** (`scripts/license_allowlist.txt` فارغ). ملاحظة: في بيئة الساندبوكس المشتركة (غير النظيفة) يرفض السكربت حزماً غير تابعة للمشروع — سلوك صحيح، لذلك البوابة تعمل في CI على venv نظيف فقط.
- **I-B1 (هجرة بصفوف قديمة):** **ناجح** — `tests/integration/test_postgres_persistence.py::test_alembic_0003_migrates_legacy_rows_and_roundtrips`: صفّان قديمان (`'Tenant A!'`, `'tenant-b'`) + صفحة بتوكن وصفحة NULL ⇒ `upgrade 0003` ⇒ مستأجران بـslug `tenant-a` (slugified من `Tenant A!`، الاسم الأصلي محفوظ كـ`name`) و`tenant-b`، علامة `default` لكل منهما، `connected_by_tenant_id = owner`، `tenant_id` مُزال، `is_selected = {p-tok: true, p-null: false}` ⇒ إدراج علامة ثانية لنفس المالك بنفس حساب Meta (أحدث `updated_at`) ⇒ `downgrade 0002` ⇒ `tenant_id` عاد بقيمة slug، **التكرار حُذف مع إبقاء الأحدث (`gAAAAA-newest`) — ملاحظة المالك 3**، `uq_social_connections_identity` أُعيد، جداول Tenancy اختفت ⇒ `upgrade head` ينجح مجدداً (مستأجران).
- **إصدار PostgreSQL محلياً:** PostgreSQL **17.11** (Debian) + pgvector، مثبّتة عبر apt في الساندبوكس (لا Docker). CI تستخدم `pgvector/pgvector:pg16` كما في التوجيه.
- **ملاحظات المالك الثلاث (مطبَّقة):**
  1. **ci.yml `secrets`:** gitleaks يحصل على `env: GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}` والوظيفة تحمل `permissions: contents: read, pull-requests: read` (`.github/workflows/ci.yml`).
  2. **B6 إعادة الربط:** `MetaConnectService.complete_connection` يبحث عن اتصال قائم بنفس `(brand_id, provider, provider_user_id)` عبر `find_by_identity`؛ إن وُجد ⇒ `keep_selected` = صفحاته المختارة سابقاً ⇒ `_apply_selection_policy`: صفحة واحدة ⇒ مختارة بتوكن؛ ≥2 ⇒ المختارة سابقاً تبقى مختارة **ويُحدَّث توكنها المشفَّر** من Graph، وغير المختارة ⇒ `is_selected=false` و`encrypted_page_token=NULL`. اختبارات: `test_reconnect_preserves_selection_and_refreshes_tokens` (router)، `test_single_page_is_auto_selected_with_token` (service)، وI-B8 على PG. الثابت `NOT(is_selected=false AND token NOT NULL)` مفروض في الدومين (`violates_selection_invariant`) وفي DB (`ck_social_connection_pages_selected_has_token`، I-B7).
  3. **downgrade 0003:** قبل إعادة `uq_social_connections_identity` يُنفَّذ `DELETE … USING (row_number() OVER (PARTITION BY tenant_id, provider, provider_user_id ORDER BY updated_at DESC, connected_at DESC, id DESC)) WHERE rn > 1` — مُختبَر في I-B1.
- **اختبارات حُذفت/أُعيدت تسميتها (بالاسم والسبب):**
  - `tests/integration/test_postgres_persistence.py`: `test_repo_upsert_same_provider_user_updates_not_duplicates` ⇒ **`test_repo_upsert_same_user_same_brand_updates_two_brands_two_rows`** (أصبح I-B2)؛ `test_repo_tenant_isolation` ⇒ **`test_repo_brand_isolation`**؛ `test_repo_set_status` ⇒ **`test_repo_set_status_and_replace_pages`** (يغطي `replace_pages` الجديدة)؛ `test_alembic_0002_upgrade_downgrade_roundtrip_with_null_rows` **حُذف** — كان يُدرج صفوفاً بـ`tenant_id` النصي ويفترض `0002` رأساً؛ تغطيته (NULL ⇒ `''` عند النزول إلى 0001) ما زالت مضمونة ضمن `test_alembic_upgrade_head_and_downgrade_base` (نزول إلى base) وبصفوف حقيقية في I-B1 (صفحة NULL تمر بـ0003 صعوداً ونزولاً).
  - `tests/modules/social_connect/test_router.py` أُعيدت كتابته بالكامل لنطاق العلامة؛ أسماء اختبارات SPRINT-02 المحفوظة (خرائط الأخطاء/الأسرار) بقيت، وأُضيفت B-U6…B-U13 بأسمائها (`test_identity_and_secrets_never_logged` = B-U11، `test_openapi_has_no_token_named_fields_except_has_page_token` = B-U13).
  - `tests/modules/social_connect/test_schemas.py::test_connection_start_request_validation` عُدّل: `tenant_id` UUID (B4) بدل نص حر.
- **انحرافات عن التوجيه (بالسطر والسبب):**
  1. **Part A — `scripts/verify_hub.py` أُعيد تنسيقه بـ`ruff format` فقط** (لا تغيير منطقي): التوجيه يفرض `ruff format --check … scripts` فكان لا بد من تهيئته ليمرّ الفحص.
  2. **Part A — `tests/modules/social_connect/test_router.py` SECRET_PATTERNS:** النمط `EAA` أصبح `(?-i:EAA)` لأن `IGNORECASE` كان يطابق `eaa` داخل UUIDs عشوائياً (اختبار متذبذب في CI).
  3. **سير الكوميتات:** بسبب إعادة تهيئة الساندبوكس المتكررة (×7 خلال السبرنت) دُفعت كوميتات WIP مؤقتة موسومة `[squash]` إلى `work/implementation` ثم **سُحقت كلها** في كوميت Part B الوحيد `b712df0` بالرسالة المفروضة (`git reset --soft a985d55` + `push -f`). النتيجة النهائية: كوميتان فوق `main` كما يطلب §6.
  4. **`connected_at` عند إعادة الربط:** يُحفظ تاريخ الربط الأول (ON CONFLICT لا يحدّث `connected_at`) — التوجيه لم يحدده؛ اخترت الأقل فقداناً للمعلومة.
  5. **`PostgresBrandAccessGrantRepository.delete_connections_of_grantee` يستخدم SQL خام** (`DELETE FROM social_connections WHERE brand_id=… AND connected_by_tenant_id=…`) بدل ORM، احتراماً لاتجاه الاستيراد الوحيد المسموح (`social_connect ⇒ tenancy.domain`؛ لا استيراد عكسي).
  6. **إضافات واجهة غير مذكورة نصاً:** `ConnectedAccountRepository.replace_pages(brand_id, account_id, pages)` و`find_by_identity(...)` (لازمتان لـB6 وملاحظة 2)؛ `MetaConnectService.verify_state(state)` مكشوفة كي يتحقق الـrouter من الحالة **قبل** التفويض وقبل أي نداء Graph؛ `UnknownPageIdsError` ⇒ 422 `{"error":"unknown_page_ids","page_ids":[…]}`؛ router `social_connect` يعيد استخدام `tenancy.router.to_http` لتوحيد شكل أخطاء 403/404.
  7. **اختبارات وحدة للمستودعات Postgres بجلسة Stub** (`tests/fakes.py::StubSession`، `test_postgres_repository.py` في الوحدتين): أُضيفت لأن وحدة `tenancy` رفعت الكود غير المغطى في `-m "not integration"` إلى 93% (<95). تختبر تركيب العبارات والتحويل صف⇄دومين، بينما التكامل يختبر السلوك الفعلي على PG.
  8. **`tests/conftest.py` و`tests/integration/conftest.py`:** عالم Tenancy ثابت (`OWNER_ID/AGENCY_ID/STRANGER_ID/BRAND_ID/BRAND2_ID`) + `seed_tenancy()` خام لاختبارات التكامل؛ `TABLES` أصبحت الجداول الخمسة.
- **أسئلة:** (1) هل تُدمَج اختبارات الـStub (انحراف 7) في المراجعة أم يُفضَّل رفع `cov-fail-under` بالاعتماد على التكامل فقط؟ (2) قرار SPRINT-04 عن تفويض `MANAGER ⇒ VIEWER` (§8/3) — التنفيذ الحالي يرفض (GRANT_ROLES = owner فقط). (3) بعد المراجعة: حذف/تدوير التوكن المستخدم للدفع.

---

## 8. ما ينتظر المالك (ليس للمنفذ)

1. **حماية الفرع `main`** (Settings ⇒ Branches ⇒ Add rule): Require a pull request + **Require status checks**: `lint`, `unit`, `integration`, `secrets`, `licenses` + Require branches to be up to date. يُفعَّل **بعد** أول تشغيل أخضر لـ CI على PR #7 (الفحوص لا تظهر في القائمة قبل أن تعمل مرة).
2. **تفعيل GitHub Actions** على المستودع إن كان معطّلاً (Settings ⇒ Actions ⇒ Allow all actions).
3. **قرار مؤجل لـ SPRINT-04 (لا يحجب 03):** هل يُسمح لمستأجر `MANAGER` بمنح `VIEWER` لغيره (تفويض فرعي)؟ التوصية: **لا** حتى تُبنى وحدة المصادقة.
4. بعد اعتماد SPRINT-03: ينقل المخطط `provider_user_name` + `D-058` + نموذج Tenancy إلى `AI_Marketing_Platform_Hub/DATA_AND_INTEGRATIONS.md` و`SECURITY_BASELINE.md` (ملفات محظورة على المنفذ).
