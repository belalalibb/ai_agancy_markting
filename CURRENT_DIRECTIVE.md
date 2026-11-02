# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-11 رُوجع مستقلاً على PostgreSQL 17.11 UTF8 و**قُبل ودُمج** (`main` @ `8fbbe8c`؛ التقرير [`sprints/SPRINT-11_REVIEW.md`](./sprints/SPRINT-11_REVIEW.md) §3 يثبت E1–E5 + F1/F2 بمِجسّ حي). هذا التوجيه = **SPRINT-11.1**: **Part A** يغلق النتائج السبع المثبتة (خصوصية/مالية/حدود) ثم **Part B/C** يسلّم **كتالوج المزودين مع تحكّم الإدارة الكامل (D-077)**: هجرة `0015`، `/api/v1/admin/ai/*`، probe، تسعير رموز/صوت، مفتاح قتل، عقد إزالة (D-076).

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-11.1-PROVIDER-CATALOG` |
| **النوع** | Part A: إغلاق E1 (consent في `content_plan`) · E2/E2c (`check_counter` + حدود trial للعلامة غير المخزَّنة) · E3/F2 (نقل `PATCH /learning-consent` إلى `tenancy` + إزالة استيراد البنية التحتية من `whatsapp/webhook.py`) · E4 (رموز الحجز) · E5 (رفض الإحالة الذاتية للمستأجر) · F1 (دقة بوابة التغطية) ⇒ Part B: `ai_providers`/`ai_models`/`ai_routing` + هجرة `0015` + `ProviderCatalog` + `CatalogPriceTable` + `CatalogGatewayResolver` + مفتاح القتل ⇒ Part C: `/api/v1/admin/ai/*` (CRUD + routing + probe) + `REMOVABILITY.md` + اختبار العزل |
| **الحالة** | `ISSUED` — جاهز للتنفيذ فور دمج هذا التوجيه في `main` (المخطط يدمجه بنفسه — D-063) |
| **يبدأ من** | `main` @ **SHA دمج هذا التوجيه** (يسجَّل في `WORK_STATE.md` §6). أول أمر: `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` |
| **الفرع المستهدف** | `work/implementation` ⇒ PR إلى `main` بعنوان `SPRINT-11.1: E1–E5 closure + provider catalog (D-077) + admin AI control` |
| **الكوميتات** | **ثلاثة** متسلسلة، **كلٌّ منها يمرّ كاملاً على PG 17 UTF8**: **A** (الإغلاقات السبعة، بلا هجرة) ⇒ **B** (الكتالوج + هجرة 0015 + الحلّ الديناميكي + مفتاح القتل، **بلا مسارات جديدة**) ⇒ **C** (مسارات الإدارة + probe + removability). كوميت رابع اختياري `docs(sprint-11.1): executor report` يملأ §6 فقط. |
| **تبعيات جديدة** | **لا شيء**. لا مكتبة جديدة في `pyproject.toml`. التشفير عبر `src/core/security.py::TokenEncryptionService` (MultiFernet) الموجود. |
| **الحجم المتوقع** | A ≈ 200 سطر `src/` + 350 اختبار · B ≈ 800 + 700 · C ≈ 700 + 800 ⇒ **≤ ~1,800 سطر `src/`** (سقف G6). **صمّام:** إن تجاوز B+C معاً 1,600 سطر `src/` فعلياً، أنجز C بلا `routing` (مسارات المزودين/النماذج/probe فقط) وسجّل في §6. |
| **المسارات المحمية بعد السبرنت** | **`PROTECTED_ROUTE_COUNT == 77`** = 66 + **11** (انظر §2-C-1) · العامة تبقى **6** (لا مسار عام جديد) · `PATCH /brands/{id}/learning-consent` **ينتقل** من `entitlements` إلى `tenancy` بلا تغيير في العدد |
| **الهجرات** | `0015_ai_provider_catalog.py` (Part B) — ثلاثة جداول جديدة فقط؛ لا تعديل على جداول قائمة؛ `downgrade` فعلي مُختبَر `head ⇒ 0014 ⇒ head` |
| **تاريخ الإصدار** | 2026-11-02 |

---

## 0. لماذا؟ (السياق الذي يجب أن يفهمه المنفذ قبل أي سطر)

1. **E1 خصوصية مُثبَتة على PG:** `PATCH /learning-consent {none}` ثم `POST /brain/content-plan` ⇒ **3 صفوف `learning_signals`** (`advisor.py:445–448` حلقة غير مشروطة). D-080 يقول «`none` ⇒ لا إشارة جديدة» — بلا استثناء للمسار.
2. **E2c مالي مُثبَت:** علامة بلا صف `brand_entitlements` ترى `daily_tokens=20,000` في `GET /entitlement` بينما `PgEntitlementLimitsReader.check()` تُرجع `False` (`service.py:226`) فتُحكم بالإعداد **200,000**. العرض يجب أن يساوي الفرض. **قرار المخطط:** العلامة غير المخزَّنة **تُحكم بحدود `trial`** (20k/10/50)؛ الإعداد `ai_daily_token_budget_per_brand` يبقى **سقفاً احتياطياً** عند مفتاح القتل فقط.
3. **E4 مالي:** الحجز (`budget_guard.py:254–270`) يكتب `estimated_cost_usd` بلا `prompt_tokens`/`completion_tokens` ⇒ `combined_usage_since` يحتسب **0** للصف `pending`/`ambiguous`. سقف المنصة بالدولار صحيح (يجمع `estimated_cost_usd`)؛ الخلل في رموز العلامة فقط.
4. **E5 مالي:** `POST /brands {referred_by_brand_id: علامة من نفس المستأجر}` ⇒ **201** ⇒ إحالة ذاتية تُنتج `referral_credit` 10% (D-078).
5. **E3/F2 حدود:** `PATCH /learning-consent` يعيش في `entitlements/router.py:107` (وحدة قابلة للإزالة) بينما العمود ملك `tenancy`؛ و`whatsapp/webhook.py:57` يستورد `tenancy.infrastructure.postgres_repository` مباشرة. D-016/D-076: المستهلك يملك العقد و`tenancy/dependencies.py` يملك التركيب.
6. **F1 حوكمة:** `--cov-fail-under=95` بدقة 0 يقبل 94.57% (pytest-cov 7.1 يقرّب). بوابة `unit` في CI طبعت `FAIL … 94.57%` ونجحت. نصلح الدقة **ونرفع** تغطية الوحدات ≥ 95.00 فعلياً.
7. **D-077 لماذا الآن:** اليوم إضافة نموذج = تعديل `.env` + إعادة تشغيل + `AI_PRICE_TABLE_JSON` يدوي. المالك يريد إضافة/تعطيل مزوّد أو نموذج أو سعر أو مسار بديل **في وقت التشغيل** دون نشر، ولكل غرض (`content_generation`, `whatsapp_suggest_reply`, `brand_diagnosis`, `content_plan`, مستقبلاً `audio_transcription`) مسار أساسي + بدائل مرتّبة. **المفاتيح لا تُعاد أبداً** عبر أي مسار (G4).
8. **لا فئات أخطاء جديدة:** `ErrorCategory` ثابتة على 12 اسماً. أخطاء الكتالوج استثناءات طبقة `BudgetGuard`/`ProviderCatalog` بأسماء HTTP حرفية (§2-B-6).
9. **قابلية الإزالة (D-076):** `FEATURE_PROVIDER_CATALOG_ENABLED=false` ⇒ السلوك الحالي حرفياً (`.env` `AI_*` + `AI_PRICE_TABLE_JSON`) دون لمس الجداول.

---

## 1. النطاق

### 1-أ. داخل النطاق
- Part A: الإغلاقات السبعة (E1, E2+E2c, E3+F2, E4, E5, F1).
- Part B: هجرة `0015` + `ai_gateway/catalog/` (نموذج المجال، المستودع PG، `ProviderCatalog`، `CatalogPriceTable`، `CatalogGatewayResolver`) + تكامله مع `build_gateway`/`build_price_table`/`BudgetGuard` خلف مفتاح القتل.
- Part C: `/api/v1/admin/ai/*` (11 مساراً) + probe + `ai_gateway/catalog/REMOVABILITY.md` + `tests/modules/ai_gateway/catalog/test_isolation.py`.

### 1-ب. خارج النطاق (مؤجَّل صراحةً)
- **لا** تنفيذ فعلي لـ`audio_transcription` (الغرض يُسجَّل في الكتالوج والتسعير فقط؛ لا مزوّد صوت).
- **لا** واجهة ويب للإدارة؛ **لا** `users.is_platform_admin` في DB (يبقى `PLATFORM_ADMIN_USER_IDS`).
- **لا** تغيير في `public_fetch.py` (SSRF) — `AI_ALLOW_LOCAL_PROVIDER_HOSTS` يخص نقاط نهاية المزودين فقط.
- **لا** أسقف واتساب D-082، **لا** احتفاظ D-081 (SPRINT-12/13).

---

## 2. المواصفات

### Part A — الإغلاقات (كوميت `fix(sprint-11.1): close E1–E5 + F1/F2 (PG17-proven)`)

**A-1 (E1) — `brand_brain/advisor.py` `content_plan()`:** استبدل الحلقة `:445–448` بـ:
```python
if await may_write_signal(self._consent, brand_id):
    for rec, _ in items:
        await self._repo.add_learning_signal(...)
```
الاختبار: `tests/integration/…::test_a1_content_plan_consent_none_writes_zero_signals_on_pg` (نفس سيناريو مِجسّ المخطط: consent `none` ⇒ 3 عناصر ⇒ `learning_signals` = 0 و`brand_recommendations` = 3 و`content_items` = 3) + وحدة في `tests/modules/brand_brain/`.

**A-2 (E2 + E2c) — `entitlements`:**
- `PgEntitlementLimitsReader.check()` (`service.py:219–247`): عند `not stored` **لا** تُرجع `False`؛ بل تحكم بـ`BrandEntitlement.trial(...).limits.daily_tokens` (20,000). تُرجع `False` **فقط** عند مفتاح القتل. حدّث docstring C-1 و`test_plan_replaces_setting.py` (الحالات: مخزَّن نشط ⇒ حدود الباقة؛ غير مخزَّن ⇒ trial 20k؛ مفتاح القتل ⇒ الإعداد 200k؛ مخزَّن منتهٍ `is_active=False` ⇒ trial).
- **`check_counter` تُستدعى فعلاً:** (أ) `content/service.py` قبل **الموافقة** (`approve`) على عنصر: `monthly_posts`؛ (ب) `whatsapp/webhook.py` عند **إنشاء محادثة جديدة** (أول رسالة واردة لمحادثة غير موجودة): `monthly_wa_conversations` — **لا يُرجع 429 من webhook موقَّع أبداً** (دلالة Meta retry): عند التجاوز تُخزَّن الرسالة وتُعلَّم المحادثة `quota_exceeded=true` في الحمولة/السجل ويُمنع `suggest-reply`/الإرسال الصادر لها بـ**402 `entitlement_quota_exceeded`** `{counter, limit, used}`. (ج) `whatsapp/router.py` suggest-reply + send: نفس الفحص.
- الاعتماد عبر بروتوكول يملكه المستهلك (`content/entitlements_port.py::CounterGuard` و`whatsapp/entitlements_port.py::CounterGuard`؛ `None` ⇒ لا فحص) وتركيبه في `entitlements/dependencies.py` فقط (grep §4-3 رقم 4 يبقى 0).
- اختبار PG: 10 موافقات تنجح، الـ11 ⇒ 402؛ 50 محادثة جديدة ثم الـ51 ⇒ webhook 200 + `suggest-reply` 402.

**A-3 (E3 + F2) — نقل الموافقة إلى `tenancy`:**
- انقل `PATCH /brands/{brand_id}/learning-consent` من `entitlements/router.py:107–125` إلى `tenancy/router.py` (نفس الـschema `LearningConsentPatch`، نفس `GRANT_ROLES`، نفس الاستجابة `BrandOut`). احذف المسار من `entitlements`؛ حدّث `entitlements/REMOVABILITY.md:11` و`:36–39` (الموافقة لم تعد تعتمد على الوحدة).
- `tenancy/dependencies.py`: أضف `build_brand_consent_reader(session) -> PostgresBrandConsentReader`؛ `whatsapp/webhook.py:57` و`:378–382` يستخدمانه بدل الاستيراد المباشر للبنية التحتية.
- `PROTECTED_ROUTE_COUNT` يبقى 66 بعد A.

**A-4 (E4) — `budget_guard.py` الحجز:** `CostEntry(... prompt_tokens=prompt_estimate, completion_tokens=req.max_tokens, ...)` في `:257–270`. `_settle_ok` يستبدلهما بالفعلي (كما الآن)؛ `_settle_error` كما الآن؛ `_refund` يصفّرهما (`outcome=refunded`). `settle_stale` يبقي القيم (المحجوزة تُحتسب في `ambiguous`). اختبار PG: صف `pending` ⇒ `combined_usage_since` = `prompt_estimate + max_tokens` > 0؛ بعد `settle_stale` تبقى > 0؛ بعد `_settle_ok(150)` = 150. حدّث `test_b1_content_plan_counts_once` إن لزم (الاحتساب النهائي يبقى 150).

**A-5 (E5) — `tenancy/service.py:70–95`:** بعد التحقق من وجود المُحيل: إن كان `referrer.owner_tenant_id == owner_tenant_id` ⇒ `SelfReferralError` ⇒ **422 `referral_self_tenant`** `{"error":"referral_self_tenant","message":"a brand cannot be referred by a brand of the same tenant"}`. اختبار PG يعيد مِجسّ المخطط (`POST /brands` بـ`BRAND_ID` ⇒ 422 ولا صف).

**A-6 (F1) — `.github/workflows/ci.yml:71`:** `--cov-fail-under=95 --cov-precision=2`؛ **و** ارفع تغطية `pytest -m "not integration"` إلى **≥ 95.00%** فعلياً بإضافة اختبارات وحدة للأسطر المفقودة (`ai_gateway/dependencies.py:99–105`, `tenancy/dependencies.py:73`, `whatsapp/client.py:86–87`, `publishing/channels/registry.py:53–54`, `brand_brain/infrastructure/internal_metrics.py:35–123`, …) — **لا** `# pragma: no cover` جديد.

### Part B — الكتالوج (كوميت `feat(ai-gateway): provider catalog + migration 0015 + dynamic routing (D-077)`)

**B-1 هجرة `alembic/versions/0015_ai_provider_catalog.py`** (ثلاثة جداول؛ كل القيود **مسمّاة**):
- `ai_providers`: `id UUID PK` · `slug VARCHAR(32) UNIQUE` (`^[a-z0-9_-]+$`) · `kind VARCHAR(24)` CHECK `IN ('openai_compatible','fake')` · `base_url TEXT NOT NULL` · `credential_cipher TEXT NULL` (نص Fernet) · `credential_ref VARCHAR(64) NULL` (اسم متغير بيئة) · CHECK `ck_ai_providers_one_credential`: `NOT (credential_cipher IS NOT NULL AND credential_ref IS NOT NULL)` · `enabled BOOL NOT NULL DEFAULT true` · `timeout_seconds NUMERIC(5,1) NOT NULL DEFAULT 30` · `created_at`, `updated_at TIMESTAMPTZ`.
- `ai_models`: `id UUID PK` · `provider_id FK ai_providers ON DELETE CASCADE` · `model_name VARCHAR(64)` · UNIQUE `(provider_id, model_name)` · `capability VARCHAR(16)` CHECK `IN ('text','audio')` · `prompt_per_1k NUMERIC(12,6) NULL` · `completion_per_1k NUMERIC(12,6) NULL` · `cost_per_minute_usd NUMERIC(12,6) NULL` · `max_audio_seconds INT NULL` CHECK `<= 120` · CHECK `ck_ai_models_price_shape`: `(capability='text' AND cost_per_minute_usd IS NULL AND max_audio_seconds IS NULL) OR (capability='audio' AND prompt_per_1k IS NULL AND completion_per_1k IS NULL)` · CHECK كل سعر `>= 0` · `enabled BOOL DEFAULT true` · `max_tokens_cap INT NULL` · timestamps.
- `ai_routing`: `id UUID PK` · `purpose VARCHAR(32)` CHECK `IN ('content_generation','whatsapp_suggest_reply','brand_diagnosis','content_plan','audio_transcription')` · `model_id FK ai_models ON DELETE CASCADE` · `priority SMALLINT NOT NULL` (0 = أساسي) · UNIQUE `(purpose, priority)` · UNIQUE `(purpose, model_id)` · `enabled BOOL DEFAULT true` · timestamps.
- `downgrade`: إسقاط الثلاثة بالترتيب العكسي. اختبار `test_b1_migration_0015_round_trip_on_pg` (`head ⇒ 0014 ⇒ head`؛ الجداول الثلاثة و11 قيداً مسمّى تختفي وتعود).

**B-2 المجال `ai_gateway/catalog/domain.py`:** `Provider`, `Model`, `Route` (Pydantic frozen) + `Purpose = Literal[...]` + `ModelPricing` (`token(prompt, completion) | audio(per_minute, max_seconds)`). **`Provider` لا يحمل المفتاح الخام** — يحمل `credential_cipher | credential_ref` ويُفكّ عبر `CredentialResolver(encryption, env)` داخل `CatalogGatewayResolver` فقط، لحظة البناء، ولا يُسجَّل في أي log.

**B-3 المستودع `ai_gateway/catalog/infrastructure/pg_repository.py`:** `PgCatalogRepository` (CRUD + `list_routes(purpose)` مرتّبة بالأولوية + `snapshot()` يعيد الثلاثة في استعلام أو ثلاثة). **ذاكرة مؤقتة** في العملية بعمر `AI_CATALOG_CACHE_SECONDS=30` (قابل للتصفير من مسارات الإدارة عبر `invalidate()`).

**B-4 `ProviderCatalog` (`ai_gateway/catalog/service.py`):**
- `resolve(purpose) -> list[ResolvedModel]` = المسارات المفعَّلة ⇒ نماذج مفعَّلة ⇒ مزودون مفعَّلون، بالأولوية؛ فارغة ⇒ `NoRouteError` ⇒ **503 `ai_route_unavailable`**.
- **تسعير:** `CatalogPriceTable(catalog)` ينفّذ نفس واجهة `PriceTable.estimate/estimate_unit` من `ai_models`؛ `Decimal("0")` صالح؛ `None` (سعر غير مُدخَل) ⇒ `UnpricedModelError` كما اليوم (503 `ai_model_unpriced`) في وضع `platform` الصارم. للصوت: `estimate_audio(model, seconds)`؛ `seconds > max_audio_seconds` ⇒ `UnpricedModelError`.
- **بدائل مرتّبة (fallback):** `CatalogGatewayResolver.complete(req)` يجرّب المسار 0؛ عند `AIError` فئتها في `{provider_unavailable, model_unavailable, rate_limited, timeout, retryable_server_error}` ينتقل للتالي **بحجز جديد وتسوية/استرداد السابق** عبر `BudgetGuard` (كل محاولة صفّ `ai_cost_log` مستقل)؛ أي فئة أخرى ⇒ لا بديل. حد أقصى **3** محاولات. `credential_mode=user_key` (`X-AI-Key`) ⇒ **لا** fallback (مفتاح المستخدم لمزوّد واحد).
- **`AI_ALLOW_LOCAL_PROVIDER_HOSTS`** (`list[str]`, افتراضي فارغ): `base_url` لمزوّد يُقبل فقط إن كان مضيفه عاماً (`_is_global_address` **مستورد** من `brand_brain/public_fetch.py`؟ **لا** — انسخ الدالة الصغيرة إلى `core/netguard.py` واستخدمها في المكانين دون تغيير سلوك `public_fetch`) **أو** مذكوراً حرفياً في القائمة (مثل `localhost:11434`, `ollama:11434`). الرفض ⇒ 422 `provider_host_not_allowed`.

**B-5 التكامل خلف مفتاح القتل (`ai_gateway/dependencies.py`):**
- `FEATURE_PROVIDER_CATALOG_ENABLED: bool = False` (افتراضي **معطَّل** في هذا السبرنت؛ المالك يفعّله بعد الـseed).
- `get_ai_gateway` و`build_price_table`: إن كان المفتاح مفعَّلاً ⇒ `CatalogGatewayResolver(purpose)` + `CatalogPriceTable`؛ وإلا السلوك الحالي **حرفياً** (`build_gateway(settings)` + `PriceTable.from_json`). `purpose` يأتي من `BudgetContext.purpose` الموجود.
- **لا** تغيير في `BudgetGuard.complete` سوى قبول `price_table` بأي تنفيذ للبروتوكول `PriceSource` (استخرجه من `PriceTable`).

**B-6 أخطاء HTTP الحرفية (لا `ErrorCategory` جديد):** `ai_route_unavailable` 503 · `ai_model_unpriced` 503 (موجود) · `provider_host_not_allowed` 422 · `catalog_conflict` 409 (slug/model/priority مكرر) · `catalog_not_found` 404 · `catalog_disabled` 503 (مسارات الإدارة عندما المفتاح معطَّل **تعمل** للكتابة والقراءة — فقط **التشغيل** يتجاهل الكتالوج؛ لذا لا 503 هنا — احذف هذا الاسم إن لم يلزم).

### Part C — تحكّم الإدارة (كوميت `feat(admin-ai): /admin/ai/* CRUD + routing + probe + removability (D-077)`)

**C-1 المسارات (كلها `PlatformAdmin`، كلها تكتب `entitlement_events`? **لا** — جدول تدقيق خاص غير مطلوب: سجّل بـ`logger.info("admin_ai …")` بحقول `actor_user_id, action, target_id` **بلا أسرار**، وأضف صف `ai_cost_log` فقط للـprobe):**

| # | Method | Path | ملاحظة |
|---|---|---|---|
| 1 | GET | `/admin/ai/providers` | قائمة؛ `has_credential: bool` فقط |
| 2 | POST | `/admin/ai/providers` | `{slug, kind, base_url, api_key?: SecretStr, credential_ref?, timeout_seconds?}` ⇒ يشفّر `api_key` فوراً ويُسقطه؛ 201 |
| 3 | PATCH | `/admin/ai/providers/{id}` | `enabled/base_url/timeout/api_key/credential_ref` (api_key جديد يستبدل القديم؛ `null` صريح يمسحه) |
| 4 | DELETE | `/admin/ai/providers/{id}` | 204؛ CASCADE على النماذج والمسارات |
| 5 | GET | `/admin/ai/models` | `?provider_id=` اختياري |
| 6 | POST | `/admin/ai/models` | `{provider_id, model_name, capability, pricing{…}, max_tokens_cap?}` |
| 7 | PATCH | `/admin/ai/models/{id}` | `enabled/pricing/max_tokens_cap` |
| 8 | DELETE | `/admin/ai/models/{id}` | 204 |
| 9 | GET | `/admin/ai/routing` | `?purpose=` ⇒ مرتّبة |
| 10 | PUT | `/admin/ai/routing/{purpose}` | `{"models": [model_id, …]}` يستبدل القائمة كاملة ذرّياً (الترتيب = الأولوية)؛ فارغة = لا مسار |
| 11 | POST | `/admin/ai/probe` | انظر C-2 |

**الاستجابات لا تحتوي أبداً** `api_key`, `credential_cipher`, `credential_ref` **القيمة** (يُعاد `credential_ref` الاسم فقط لأنه اسم متغير لا سر). وسّع حارس Zero-Secret (`tests/modules/social_connect/test_router.py:865`) ليشمل `openapi.json` الجديد **وقراءة فعلية** لكل مسار GET بعد إنشاء مزوّد بمفتاح: `"sk-"` و النص الخام **لا يظهران** في أي استجابة ولا في `caplog`.

**C-2 probe `POST /admin/ai/probe`:** `{model_id, prompt: str (≤ 500 حرف), max_tokens: int (≤ 64)}` ⇒ ينفّذ عبر `BudgetGuard` بـ`BudgetContext(purpose="admin_probe", brand_id=None, platform_pays=True, credential_mode="platform")` ⇒ صف `ai_cost_log` بـ`brand_id NULL`, `purpose='admin_probe'` (وسّع قيد `purpose` إن وُجد). **حدود:** `max_tokens ≤ 64`؛ محدِّد `RateLimiter` دلو `admin:ai-probe` **5/دقيقة لكل `user_id`**؛ يحترم سقف المنصة `$25`. الاستجابة: `{latency_ms, prompt_tokens, completion_tokens, estimated_cost_usd, preview: str (≤ 200 حرف), error_category: ErrorCategory|null, ok: bool}` — عند فشل المزوّد يُرجع **200** بـ`ok=false` و`error_category` من الـ12 (لا 5xx — الـprobe أداة تشخيص). `PlatformAdmin` فقط.

**C-3 `ai_gateway/catalog/REMOVABILITY.md`** (≤ 70 سطراً): ما يُحذف (`catalog/`, هجرة 0015 `downgrade`, مسارات `/admin/ai/*`, الإعدادات `FEATURE_PROVIDER_CATALOG_ENABLED`/`AI_ALLOW_LOCAL_PROVIDER_HOSTS`/`AI_CATALOG_CACHE_SECONDS`)، ما يبقى (`ai_cost_log` صفوف `admin_probe` ⇒ `brand_id NULL` سليمة)، مفتاح القتل، واختبار العزل `tests/modules/ai_gateway/catalog/test_isolation.py`: تحميل التطبيق مع `FEATURE_PROVIDER_CATALOG_ENABLED=false` وإزالة `catalog` من `sys.modules` ⇒ كل مسارات غير الإدارة تعمل؛ `grep -rn "ai_gateway.catalog" src --include=*.py` ⇒ فقط `ai_gateway/dependencies.py`, `ai_gateway/catalog/*`, `main.py`.

**C-4 `.env.template`:** `FEATURE_PROVIDER_CATALOG_ENABLED=false`, `AI_ALLOW_LOCAL_PROVIDER_HOSTS=`, `AI_CATALOG_CACHE_SECONDS=30` مع تعليقات عربية قصيرة.

---

## 3. معايير القبول (المخطط يتحقق منها حرفياً على PG 17 UTF8 ببيئة مُعاد بناؤها)

| # | المعيار | كيف يتحقق المخطط |
|---|---|---|
| 1 | **ثلاثة كوميتات A ⇒ B ⇒ C**، كلٌّ يمرّ وحده: `ruff check src tests alembic scripts && ruff format --check …` + `pytest -q --cov=src --cov-fail-under=95 --cov-precision=2` (كامل) + `pytest -m "not integration" --cov-fail-under=95 --cov-precision=2` بلا DB **≥ 95.00%** + `alembic upgrade head` | `git checkout <sha>` لكل كوميت |
| 2 | `PROTECTED_ROUTE_COUNT == 77` بعد C (66 بعد A و B)؛ العامة 6 | `tests/test_auth_guard.py` + `openapi.json` |
| 3 | **E1:** consent `none` ⇒ `content-plan` 3 عناصر ⇒ `learning_signals` **0**، `brand_recommendations` 3 | مِجسّ المخطط على PG |
| 4 | **E2c:** علامة بلا صف ⇒ `GET /entitlement` 20,000 **و** الطلب الـN الذي يتجاوز 20,000 رمزاً ⇒ 402/429 حسب العقد الحالي (`entitlement_exceeded`) لا 200 | PG + `FIN_PRICES` |
| 5 | **E2:** الموافقة الـ11 في الشهر ⇒ 402 `entitlement_quota_exceeded`؛ المحادثة الـ51 ⇒ webhook **200** + `suggest-reply` 402 | PG |
| 6 | **E3:** `PATCH /learning-consent` في `tenancy/router.py`؛ `grep -rn "learning-consent" src/modules/entitlements` ⇒ 0؛ `grep -rn "tenancy.infrastructure" src/modules --include=*.py` خارج `tenancy/` ⇒ **فقط** `brand_brain/infrastructure/postgres_repository.py:40` | grep |
| 7 | **E4:** صف `pending` ⇒ `combined_usage_since` = `prompt_estimate + max_tokens`؛ بعد `settle_stale` يبقى؛ بعد نجاح 150 ⇒ 150 | PG |
| 8 | **E5:** إحالة من نفس المستأجر ⇒ **422 `referral_self_tenant`** ولا صف | PG |
| 9 | **0015:** `head ⇒ 0014 ⇒ head` على قاعدة نظيفة؛ 11 قيداً مسمّى | alembic |
| 10 | **Zero-secret:** إنشاء مزوّد بـ`api_key="sk-PLANNER-PROBE-…"` ⇒ لا يظهر النص في أي استجابة GET/PATCH/probe ولا في `caplog` ولا في `openapi.json`؛ `SELECT credential_cipher` يبدأ بـ`gAAAA` (Fernet) | PG + caplog |
| 11 | **التسعير:** نموذج بسعر `0/0` ⇒ النداء ينجح و`estimated_cost_usd=0`؛ نموذج بسعر `NULL` ⇒ 503 `ai_model_unpriced` **قبل** أي نداء للمزوّد (respx 0 طلب)؛ صوت `max_audio_seconds=121` ⇒ 422 عند الإنشاء | PG + respx |
| 12 | **Routing/fallback:** مساران؛ الأول يرجع 503 ⇒ الثاني يُستدعى؛ صفّا `ai_cost_log` (الأول `refunded`/`error`، الثاني `ok`)؛ الأول يرجع 400 `bad_request` ⇒ **لا** بديل؛ `X-AI-Key` ⇒ لا بديل | respx + PG |
| 13 | **SSRF للمزودين:** `base_url=http://127.0.0.1:11434` ⇒ 422 `provider_host_not_allowed`؛ مع `AI_ALLOW_LOCAL_PROVIDER_HOSTS=127.0.0.1:11434` ⇒ 201؛ `public_fetch.py` **بلا تغيير** (`git diff` فارغ) | PG + grep |
| 14 | **probe:** `max_tokens=65` ⇒ 422؛ السادس خلال دقيقة ⇒ 429؛ النجاح ⇒ صف `ai_cost_log` `purpose='admin_probe'`, `brand_id NULL`, `outcome='ok'`؛ فشل المزوّد ⇒ 200 `ok=false` + `error_category` من الـ12 + صف `error` | PG + respx |
| 15 | **مفتاح القتل:** `FEATURE_PROVIDER_CATALOG_ENABLED=false` مع كتالوج ممتلئ ⇒ النداء يستخدم `AI_PROVIDER`/`AI_PRICE_TABLE_JSON` حرفياً (respx يرى `base_url` القديم)؛ مسارات `/admin/ai/*` تبقى تعمل | respx |
| 16 | **PlatformAdmin:** كل الـ11 ⇒ 403 `platform_admin_required` لمستخدم عادي و401 بلا توكن؛ قائمة فارغة ⇒ 403 للجميع | TestClient |
| 17 | **الحدود:** `grep -rn "ai_gateway.catalog" src --include=*.py` خارج `ai_gateway/` ⇒ فقط `main.py`؛ `grep -rn "src.modules.entitlements" src --include=*.py` خارج القائمة البيضاء ⇒ 0؛ `ErrorCategory` 12 بلا تغيير؛ `pyproject.toml` بلا تغيير | grep |
| 18 | **G6:** `git diff --numstat <main> <C> -- src` ≤ ~1,800 سطر مُضاف | numstat |
| 19 | **Removability:** `REMOVABILITY.md` + `test_isolation.py` يمرّ | pytest |
| 20 | **لا تراجع:** كل اختبارات SPRINT-11 (B1–B6, C1–C7, FIN) تمرّ بلا تعديل في التوقعات إلا A-4 (150 يبقى 150) و A-2 (trial) | pytest |

---

## 4. أوامر

```bash
# 0) البداية (من main @ SHA دمج هذا التوجيه)
git checkout work/implementation && git fetch origin && git merge --ff-only origin/main

# 1) بيئة PG 17 UTF8 (كما في SPRINT-11)
initdb -D /var/lib/pg17 -U postgres --auth=trust -E UTF8 --locale=C.UTF-8
pg_ctl -D /var/lib/pg17 -l /var/lib/pg17/log.txt -o "-p 5432 -c listen_addresses=127.0.0.1" start
psql -h 127.0.0.1 -U postgres -c "CREATE DATABASE ai_growth_os_test ENCODING 'UTF8' TEMPLATE template0 LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8'"
psql -h 127.0.0.1 -U postgres -d ai_growth_os_test -c "CREATE EXTENSION vector"
pip install -e ".[dev]"

# 2) بعد كل Part
ruff check src tests alembic scripts && ruff format --check src tests alembic scripts
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_growth_os_test pytest -q --cov=src --cov-fail-under=95 --cov-precision=2
env -u TEST_DATABASE_URL pytest -m "not integration" -q --cov=src --cov-fail-under=95 --cov-precision=2   # بوابة CI بلا DB — يجب ≥ 95.00
alembic -x database_url=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_growth_os_test upgrade head

# 3) فحوص grep الملزمة (يجب أن تُرجع 0 نتائج)
grep -rn "learning-consent" src/modules/entitlements
grep -rn "tenancy.infrastructure" src/modules --include=*.py | grep -v "src/modules/tenancy/\|brand_brain/infrastructure/postgres_repository.py"
grep -rn "src.modules.entitlements" src --include=*.py | grep -v "src/modules/entitlements/\|src/main.py\|ai_gateway/dependencies.py"
grep -rn "ai_gateway.catalog" src --include=*.py | grep -v "src/modules/ai_gateway/\|src/main.py"
grep -rn "get_secret_value()" src/modules/ai_gateway/catalog | grep -v "credential"   # المفتاح يُفكّ في CredentialResolver فقط
git diff origin/main -- src/modules/ai_gateway/contract.py src/modules/brand_brain/public_fetch.py pyproject.toml | wc -l   # 0

# 4) الدفع بعد كل كوميت (قاعدة الساندبوكس)
git push origin work/implementation
```

---

## 5. التزامات المنفذ

1. **لا دمج** إلى `main` (D-063). PR واحد من `work/implementation`.
2. **لا** `ErrorCategory` جديد؛ **لا** تبعية جديدة؛ **لا** `# pragma: no cover` جديد؛ **لا** لمس `public_fetch.py`.
3. المفاتيح: تُشفَّر عند الاستلام، تُفكّ في `CredentialResolver` لحظة بناء العميل فقط، لا تُسجَّل، لا تُعاد. أي `SecretStr` في schema الإدخال فقط.
4. **ادفع بعد كل كوميت** وحدّث §6 في كوميت رابع منفصل `docs(sprint-11.1): executor report`.
5. إن اكتشفت تعارضاً بين هذا التوجيه والكود الفعلي (اسم دالة/سطر)، **الكود الفعلي هو المرجع**: نفّذ المقصود وسجّل الفرق في §6 «انحرافات».
6. **صمّام الحجم:** إن تجاوز B+C معاً 1,600 سطر `src/` فعلياً، سلّم C بلا `PUT /routing` و fallback (الكتالوج يعمل بمسار واحد لكل غرض = النموذج الأول المفعَّل) وسجّل ذلك؛ **لا تتوقف** قبل C.

---

## 6. تقرير المنفذ (يملؤه المنفذ)

| البند | القيمة |
|---|---|
| SHA الكوميتات | A: `67dceb4` · B: `4c396c8` · C: `2d26c28` · docs: هذا الكوميت · الأساس `main` @ `7e7542e` (= دمج PR #41؛ شجرة `src/tests/alembic` مطابقة لـ`2047e47`) |
| نتائج `pytest` (العدد/التغطية) لكل كوميت — كامل **و** بلا DB | **C (الشجرة النهائية) على PG 17 UTF8:** تكامل **121/0** + وحدات **1,346/0** ⇒ **1,467 اختباراً، 0 فشل، 98.18%** (الملفات الجديدة كلها ≥ 95.92%؛ `internal_metrics.py` 39% ⇒ 100%، `netguard.py` 100%). **بوابة CI بلا DB** (`env -u TEST_DATABASE_URL … --cov-fail-under=95 --cov-precision=2`): 1,346 passed، **≥ 95.00%** (CI `unit` هو الحكم النهائي). **A و B:** ruff نظيف على شجرة كل كوميت؛ اختباراتهما مجموعة فرعية حرفية من C (كل ملف اختبار في A/B موجود بلا تعديل في C إلا إصلاح assertion واحد موثَّق في الانحراف B-3) — الخط الأساس لكل كوميت على PG يتحقق منه المخطط وفق §3-1. |
| `PROTECTED_ROUTE_COUNT` | **66** بعد A و B (لا مسار جديد) · **77** بعد C (= 66 + 11 `/admin/ai/*`؛ `tests/test_auth_guard.py` + `tests/modules/content/test_router.py`) · العامة **6** بلا تغيير |
| هجرة 0015 ذهاباً وإياباً | ✅ `test_b1_migration_0015_round_trip_on_pg`: `head ⇒ 0014 ⇒ head` على قاعدة نظيفة؛ الجداول الثلاثة + **12** قيداً مسمّى (11 في §2-B-1 + `uq_ai_routing_purpose_model`) تختفي وتعود؛ FKs CASCADE (`ai_models.provider_id`, `ai_routing.model_id`)؛ CHECKs `ck_ai_providers_kind` / `ck_ai_models_max_audio_seconds` / `ck_ai_models_price_shape` ترفض الصفوف السيئة |
| نتائج grep الملزمة (§4-3) | 1 `learning-consent` في `entitlements/` ⇒ **0** · 2 `tenancy.infrastructure` خارج `tenancy/` و`brand_brain/infrastructure/postgres_repository.py` ⇒ **0** · 3 `src.modules.entitlements` خارج القائمة البيضاء ⇒ **0** · 4 `ai_gateway.catalog` خارج `ai_gateway/` و`main.py` ⇒ **0** · 5 `get_secret_value()` في `catalog/` بلا كلمة `credential` ⇒ **0** · 6 `git diff origin/main -- contract.py public_fetch.py pyproject.toml` ⇒ **0** سطر · `ErrorCategory` = **12** |
| أسطر `src/` المضافة لكل Part | **A** +361 (−69) · **B** +1,160 (−24) · **C** +871 (−6؛ منها 44 سطر `REMOVABILITY.md`) ⇒ **المجموع +2,386 خام / 1,909 كوداً فعلياً** (غير فارغ وغير تعليق). **B+C = 2,031 خام / 1,632 كوداً** مقابل صمّام §5-6 «1,600 سطر `src/` فعلياً»: بالقياس الخام يتجاوز بـ431 سطراً، وبالقياس الفعلي (كود) بـ**32 سطراً فقط**. **قرار المنفذ:** سلّمت C **كاملاً** (مع `PUT /routing` و fallback) ولم أُفعّل العلاج المنصوص (حذف routing/fallback) لأن (1) التجاوز الفعلي 2% وأغلبه docstrings مطلوبة بموجب §5-3/G4، (2) حذف routing/fallback يوفّر ≈ 40 سطراً فقط فلا يُدخل B+C تحت السقف، (3) المعايير #12 و#14 و#17 تتطلبهما صراحة. مسجَّل لقرار المالك (انظر «ما يحتاج قرار المالك» 1). |
| انحرافات عن §2 (مع السبب) | **A-1** `check_counter` تُستدعى عبر بروتوكول `CounterGuard` يملكه المستهلك (`content/entitlements_port.py`, `whatsapp/entitlements_port.py`) ويُربط في `src/main.py` بسطرين (`counter_guard_dependency`) — لأن D-016 يمنع المستهلك من استيراد `entitlements`، و`main.py` هو جذر التركيب الوحيد المسموح؛ grep #3 يبقى 0. **A-2** webhook واتساب عند تجاوز الحصة يخزّن الرسالة ويعلّم المحادثة بـ`quota_exceeded` في الحمولة (لا عمود جديد — بلا هجرة في A كما نصّ §2-A). **A-3** `--cov-precision=2` في `ci.yml` **و** `pyproject.toml` معاً. **B-1** أغراض الكتالوج تُطابَق على `gateway.Purpose` الحيّ عبر `PURPOSE_ALIASES` (`content_draft ⇒ content_generation`, `reply_suggestion ⇒ whatsapp_suggest_reply`, `competitor_gap`/`tone_profile ⇒ brand_diagnosis`) — الكود الفعلي يستخدم هذه الأسماء لا أسماء §2-B-1 (§5-5). **B-2** `catalog_disabled` **محذوف** كما سمح §2-B-6. **B-3** `NoRouteError`/`CredentialUnavailableError` ترثان `BudgetGuardError` كي تمرّ عبر `budget_error_to_http` الموجود (503) دون لمس مستهلكي الـgateway؛ اختبار 0015 يقبل `confdeltype` كـ`bytes` (asyncpg) كما في اختبارات 0013/0014. **B-4** `brand_id=None` مسموح في `CompletionRequest`/`BudgetGuard` لغرض `admin_probe` فقط (العمود nullable منذ 0013 — بلا هجرة). **B-5** `get_catalog_reader` يُرجع `None` عندما المفتاح معطَّل **قبل** أي استيراد للكتالوج — لازم لاختبار العزل C-3. **C-1** خطآن إضافيان حرفيان **`catalog_invalid_pricing` 422** (`PATCH /models/{id}` بشكل تسعير لا يطابق `capability`) و**`catalog_unknown_purpose` 422** — بلا `ErrorCategory` جديد. **C-2** `400 bad_request` من المزوّد الأساسي ⇒ **لا** fallback **و** الحجز يُسترَدّ (صف محذوف) وفق دلالة SPRINT-11 B-1، لا صف `error` كما افترض #12 حرفياً؛ حالة 503 ⇒ صفّان `error`+`ok` كما نصّ. **C-3** `HttpOpenAICompatibleProvider` تعيد محاولة 503 مرة بنفسها قبل انتقال الكتالوج للبديل ⇒ الأساسي قد يُستدعى مرتين (مُثبَت في `test_c12_…_on_pg`). **C-4** حارس Zero-Secret يسمح بـ`max_tokens_cap` (`ModelOut`) و`max_tokens` (`ProbeRequest`) — أعداد صحيحة سمّاها التوجيه. **C-5** `probe` تمرّ عبر `BudgetGuard` بـ`limits_reader=None` (لا علامة ⇒ لا حدود باقة) مع سقف المنصة `$25`. |
| ما يحتاج قرار المالك | **(1) صمّام §5-6:** قبول C كاملاً (+32 سطراً فعلياً فوق 1,600) **أو** إعادته كـSPRINT-11.1.1 بحذف `PUT /routing` + fallback. **(2)** تفعيل `FEATURE_PROVIDER_CATALOG_ENABLED=true` فقط بعد seed (مزوّد + نموذج مسعَّر + مسار لكل غرض) — وإلا 503 `ai_route_unavailable` بالتصميم. **(3)** `PLATFORM_ADMIN_USER_IDS` قبل أول استخدام لـ`/admin/ai/*`. **(4)** تدوير التوكن (ظهر في المحادثة **≥ 36 مرة**). |

---

## 7. ما ينتظر المالك

1. **تدوير التوكن** (`ghp_…` ظهر في المحادثة **≥ 15 مرة**) + حماية فرع `main`.
2. تحديد `PLATFORM_ADMIN_USER_IDS` قبل أول استخدام لـ`/admin/ai/*` أو `/admin/entitlements/*`.
3. بعد قبول SPRINT-11.1: تفعيل `FEATURE_PROVIDER_CATALOG_ENABLED=true` **بعد** إدخال مزوّد + نموذج مسعَّر + مسار لكل غرض عبر `/admin/ai/*` (وإلا 503 `ai_route_unavailable` بالتصميم).
4. ثم SPRINT-12 (L1 + Pulse-SQL + أسقف واتساب D-082) ⇒ SPRINT-13 (Day-2 Ops + احتفاظ D-081).
