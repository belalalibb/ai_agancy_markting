# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-10 (M7 Brand Brain + N1–N5) رُوجع مستقلاً على PostgreSQL 17 و**قُبل ودُمج** (`main` @ `b633540`؛ التقرير في [`sprints/SPRINT-10_REVIEW.md`](./sprints/SPRINT-10_REVIEW.md) §4 يثبت B1–B6). خطة ما بعد M7 اعتمدها المالك (Q1–Q17 ⇒ D-075–D-082). هذا التوجيه = **SPRINT-11**: إغلاق B1–B6 بإثبات على PG 17، ثم الطبقة المالية الصحيحة (D-075)، ثم طبقة الباقات/التفعيل (D-073) مع عقد القابلية الآمنة للإزالة (D-076). **كتالوج المزودين وتحكم الإدارة الكامل (D-077) = SPRINT-11.1** يصدر فور قبول هذا السبرنت (قرار تقسيم الحجم — انظر §1-ب).

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-11-FINANCE-AND-ENTITLEMENTS` |
| **النوع** | Part A: إغلاق B2–B6 (SSRF/robots/صفحات الخطأ · كشف الخاص في `/profile/revisions` · سباق `reply_template` + جسم فارغ · إسقاط التشخيص على نوع مجهول · سقف `phone_number_id` قبل HMAC) + متغيرات `AI_*` في `.env.template` ⇒ Part B: المالية (B1 + D-075: نقل ملكية `ai_cost_log` إلى `ai_gateway`، هجرة 0013، `BudgetGuard.reserve/settle/refund`، fail-closed، سقف المنصة، حذف `UNION ALL`) ⇒ Part C: `entitlements` (D-073/D-078) + `platform_admin` + `learning_consent` (D-080) + إحالة CTWA (`wa_messages.referral`) + `referred_by_brand_id` + هجرة 0014 + عقد Safe Removability (D-076) |
| **الحالة** | `ISSUED` — جاهز للتنفيذ فور دمج هذا التوجيه في `main` (المخطط يدمجه بنفسه — D-063) |
| **يبدأ من** | `main` @ **SHA دمج هذا التوجيه** (يسجَّل في `WORK_STATE.md` §6). أول أمر: `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` |
| **الفرع المستهدف** | `work/implementation` ⇒ PR إلى `main` بعنوان `SPRINT-11: B1–B6 closure + finance (D-075) + entitlements (D-073) + removability (D-076)` |
| **الكوميتات** | **ثلاثة** متسلسلة، **كلٌّ منها يمرّ كاملاً على PG 17 UTF8** (الاختبارات + `ruff` + `alembic upgrade head`/`downgrade`): **A** (B2–B6 + `.env.template`، بلا هجرة) ⇒ **B** (المالية + هجرة 0013) ⇒ **C** (entitlements + هجرة 0014 + consent + referral + removability). كوميت رابع اختياري `docs(sprint-11): executor report` يملأ §6 فقط. |
| **تبعيات جديدة** | **لا شيء**. لا مكتبة جديدة في `pyproject.toml`. `httpx` يبقى عبر `core/http.py` ومواضع `TID251` الحالية فقط. |
| **الحجم المتوقع** | A ≈ 350 سطر كود + 600 اختبار · B ≈ 650 + 900 · C ≈ 900 + 1,000 ⇒ **≈ 1,900 سطر `src/`** (تحت سقف G6 البالغ ~2,000؛ لذلك خرج الكتالوج إلى SPRINT-11.1) |
| **المسارات المحمية بعد السبرنت** | **`PROTECTED_ROUTE_COUNT == 66`** (62 + 4 جديدة في Part C — انظر §2-C-5) · العامة تبقى 6 |
| **الهجرات** | `0013_ai_cost_log_ownership_and_reservations.py` (Part B) · `0014_entitlements_consent_referral.py` (Part C) · كلتاهما بـ`downgrade` فعلي مُختبَر |
| **تاريخ الإصدار** | 2026-11-01 |

---

## 0. لماذا؟ (السياق الذي يجب أن يفهمه المنفذ قبل أي سطر)

1. **B1 مُثبَت ×8:** نداء `content-plan` واحد بـ150 رمزاً يُحتسب 1,200 لأن `pg_budget.py::ai_token_usage_sources` يجمع `UNION ALL` من `content_revisions` + `wa_reply_drafts` + `ai_cost_log` **و** `CostLoggingGateway` تكتب نسخة ثانية. القرار (D-075): `ai_cost_log` **المصدر الوحيد**، ويُحذف `UNION ALL`. الصف يُكتب **قبل** النداء (`pending`) لا بعده — لأن الكتابة بعد النداء تضيع عند انقطاع الاتصال فيُستهلك المزوّد بلا سجل.
2. **ملكية خاطئة:** `ai_gateway/cost_logging.py` يستورد `CostEntry`/`CostOutcome`/`CostLogWriter` من `brand_brain` ⇒ البوابة المشتركة تعتمد على وحدة أعمال (مخالفة D-016). تنتقل الملكية إلى `ai_gateway`.
3. **Fail-closed:** في وضع `platform` (مفتاح المنصة) أي نموذج بلا سعر معروف (`PriceTable.estimate ⇒ None`) **يُرفض قبل النداء**. **`Decimal("0")` سعر صالح** (Groq المجاني، faster-whisper المحلي، `fake`) — المحظور فقط `None` (قرار المالك Q4). هذا يمنع فاتورة مفاجئة من نموذج أُضيف دون تسعير.
4. **B6 خطر إنتاجي الآن:** طلب غير موقَّع بـ10,000 معرّف ⇒ 10,000 استعلام قبل أي HMAC. يُصلح **أولاً** في Part A.
5. **B2-أ SSRF:** `127.0.0.1.nip.io` يُجلب فعلاً. يُصلح بـ`getaddrinfo` + `ip_address.is_global` **قبل** أي اتصال.
6. **لا فئات أخطاء جديدة:** `ErrorCategory` في `contract.py` ثابت على 12 اسماً (`auth_expired, invalid_credential, rate_limited, quota_exceeded, model_unavailable, provider_unavailable, unsupported_capability, bad_request, content_rejected, timeout, retryable_server_error, non_retryable_error`) مع `RETRYABLE_DEFAULT` وتأكيد سلامته. الميزانية/التسعير **استثناءات طبقة `BudgetGuard`** لا فئات مزوّد.
7. **عقد الإزالة (D-076):** كل وحدة جديدة (`entitlements` هنا) تشحن `REMOVABILITY.md` + اختبار عزل + مفتاح قتل — ليتمكن المالك من إلغاء أي قدرة دون إعادة بناء.

---

## 1. النطاق

### 1-أ. داخل النطاق
- **Part A:** `brand_brain/public_fetch.py`، `brand_brain/schemas.py` (+`router.py` موضع الاستدعاء)، `brand_brain/service.py` و`advisor.py` (B4/B5)، `whatsapp/webhook.py` + `whatsapp/domain/repository.py` + `whatsapp/infrastructure/postgres_repository.py` (B6)، `.env.template`.
- **Part B:** `ai_gateway/` (ملفات جديدة `cost_log.py` [النماذج + البروتوكول]، `infrastructure/pg_cost_log.py`، `budget_guard.py`؛ تعديل `cost_logging.py`، `budget.py`، `dependencies.py`، `infrastructure/pg_budget.py`)، `brand_brain/domain/models.py` + `domain/repository.py` + `infrastructure/orm.py` + `infrastructure/postgres_repository.py` (إزالة ما انتقل)، `brand_brain/router.py`/`advisor.py`، `content/service.py`/`router.py`/`dependencies.py`، `whatsapp/suggestions.py` + مواضع تركيبه، `core/config.py`، `alembic/versions/0013_*.py`، `publishing/scheduler.py` أو وحدة مجدول عامة (مهمة `settle_stale_reservations`).
- **Part C:** وحدة جديدة `src/modules/entitlements/` كاملة، `tenancy/infrastructure/orm.py` + `domain/models.py` + `router.py` (عمودان + مسار consent)، `whatsapp/webhook.py` + `infrastructure/orm.py` (`referral`)، `auth/dependencies.py` (`PlatformAdmin`)، `core/config.py`، `src/main.py` (تركيب)، `alembic/versions/0014_*.py`، `pyproject.toml` (`per-file-ignores` موثَّق فقط إن لزم — انظر §2-C-8)، `tests/test_auth_guard.py` و`tests/modules/content/test_router.py` (العدد ⇒ 66).

### 1-ب. خارج النطاق (صراحةً — لا تُلمس)
- **كتالوج المزودين** `ai_providers/ai_models/ai_routing`، مسارات `/api/v1/admin/ai/*`، `POST /admin/ai/probe`، `AI_ALLOW_LOCAL_PROVIDER_HOSTS`، نقل `platform_admin` إلى قاعدة البيانات ⇒ **SPRINT-11.1** (يصدره المخطط فور قبول SPRINT-11). السبب: مجموع A+B+C+الكتالوج ≈ 3,100 سطر `src/` > سقف G6.
- أسقف واتساب 300/يوم ومفتاح القتل لكل رقم (D-082) ⇒ SPRINT-12. مهام التنظيف/الاحتفاظ (D-081) ⇒ SPRINT-13. `tenant_members`، `media_jobs`، بوابة الدفع، Benchmark Lab ⇒ V1.1.
- أي واجهة أمامية، أي تبعية جديدة، أي تغيير في `ErrorCategory`، أي بث جماعي واتساب.

---

## 2. المواصفات

> **قاعدة عامة لكل Part:** لا تغيير سلوكي خارج البنود المذكورة. أسماء الحقول/الأخطاء أدناه **حرفية**. كل بند له اختبار يذكر رقمه في اسم الدالة (`test_b6_…`, `test_b1_…`, `test_c3_…`).

### 2-A. Part A — إغلاق B2–B6 + بيئة AI (كوميت واحد، بلا هجرة)

**A-1 (B6) — سقف المعرّفات + استعلام واحد قبل HMAC** — `whatsapp/webhook.py`:
- ثابت `MAX_PHONE_NUMBER_IDS = 8` بجوار `MAX_BODY_BYTES`. ترتيب `POST receive_webhook` **الإلزامي**: (1) سقف الجسم 1 MiB ⇒ 413؛ (2) شكل الرأس `^sha256=[0-9a-f]{64}$` ⇒ 401؛ (3) `extract_phone_number_ids`؛ إن كان `len(ids) == 0` أو `> MAX_PHONE_NUMBER_IDS` ⇒ **400 `webhook_rejected`** (جسم ثابت `{"error":"webhook_rejected"}`، بلا تفاصيل — قبل أي استعلام)؛ (4) **استعلام واحد** `repo.get_accounts_by_phone_number_ids(ids) -> list[WhatsAppAccount]` (جديد في البروتوكول والتنفيذ: `WHERE phone_number_id = ANY(:ids) AND is_active`)؛ (5) فك التشفير + HMAC لكل حساب كما هو اليوم.
- يُحذف الاستدعاء الحلقي لـ`get_account_by_phone_number_id` من المسار (يبقى في البروتوكول إن استُخدم في مكان آخر).
- **اختبار PG إلزامي** `test_b6_single_query_before_hmac`: بنمط العدّاد الموجود (`event.listen(engine.sync_engine, "before_cursor_execute", …)` — `tests/integration/test_postgres_persistence.py:2301`) يثبت أن طلباً غير موقَّع بـ8 معرّفات ينفّذ **≤ 1** استعلام قبل الرفض 401، وأن 9 معرّفات ⇒ 400 بـ**0** استعلام، و950 KiB بـ10,000 معرّف ⇒ 400 بـ0 استعلام.

**A-2 (B2-أ) — SSRF: حلّ DNS وفحص `is_global` قبل أي اتصال** — `brand_brain/public_fetch.py`:
- أضف إلى `RefusalReason`: `"private_address"` و`"unavailable"`.
- قبل أي طلب (الصفحة **و** robots): `infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)`؛ إن فشل ⇒ `PublicFetchRefused("host_not_allowed", host=host)`؛ لكل عنوان `ipaddress.ip_address(sockaddr[0])`: إن كان **أيّ** عنوان ليس `is_global` (يشمل loopback/private/link-local/reserved/multicast/unspecified، وIPv6 ULA `fd00::/8`) ⇒ `PublicFetchRefused("private_address", host=host)`. لا استثناءات ولا إعداد يعطّله في هذا السبرنت.
- `getaddrinfo` يُحقن (`resolver: Callable | None` في مُنشئ `PublicFetcher`) ليُحاكى في الاختبارات بلا شبكة.
- **اختبارات:** `127.0.0.1.nip.io`، `169.254.169.254.nip.io`، `10.0.0.1.nip.io`، `fd00--1.sslip.io`، مضيف يحلّ إلى عنوان عام + عنوان خاص (يُرفض)، IPv6 عام (يمرّ). **كل حالة رفض تثبت `respx.calls.call_count == 0`** (لا طلب شبكي البتة).
- التحويلات (3xx) تبقى مرفوضة كما هي (`follow_redirects=False`).

**A-3 (B2-ب) — robots.txt متدفق** — `_robots_for`: استخدم `client.stream("GET", …)` و`aiter_bytes()` مع إيقاف القراءة عند `ROBOTS_MAX_BYTES` (64 KiB) ثم `response.aclose()`؛ **اختبار:** جسم 4.7 MiB ⇒ لا تُقرأ سوى ≤ 64 KiB (عدّاد على المولّد) ولا يُرفع `too_large`. حالة robots ≠ 200 ⇒ تُعامل كـ«لا robots» كما هو اليوم **إلا** 401/403 ⇒ `robots_disallow` (محافظة).

**A-4 (B2-ج) — صفحات الخطأ ليست أدلة** — `_get_page`: أي `status_code >= 400` ⇒ `PublicFetchRefused("unavailable", host=host)`. في `service.py::CompetitorRefresher.refresh` تُسجَّل الحالة في `brand_competitors.notes`/حقل الحالة الموجود كـ`unavailable:<status>` **ولا يُنشأ صف `brand_evidence`**. **اختبار:** 503 ⇒ صفر أدلة جديدة + الاستجابة 200 بحالة `unavailable`.

**A-5 (B3) — إخفاء الخاص في المراجعات** — `schemas.py::RevisionPublic.from_domain(cls, rev, *, private: bool)`: إن لم يكن `private` يُحذف من النسخة `snapshot["tone"]["banned_phrases"]` و`snapshot["reply_templates"]` (مفاتيح تُحذف لا تُفرَّغ، ليطابق `ProfilePublic` حيث `None` ⇒ مخفي). `router.py` `GET …/profile/revisions`: `private=role in PRIVATE_ROLES`. **اختبار:** VIEWER لا يرى المفتاحين، OWNER يراهما، وحجم الاستجابة للـVIEWER لا يحتوي النص `banned_phrases`.

**A-6 (B4) — سباق `reply_template` وجسم فارغ**:
- `service.py` موضع `accept()` لنوع `reply_template` على علامة بلا ملف: التقط `IntegrityError` حول `insert_profile` ⇒ `ProfileConflictError` جديد في `exceptions.py` ⇒ **409 `profile_conflict`** (رسالة: «الملف أُنشئ للتو — أعد المحاولة»). **اختبار PG** `test_b4_concurrent_accept_is_409_not_500`: قبولان متزامنان (`asyncio.gather`) ⇒ `sorted(status) == [200, 409]`.
- `advisor.py`: توصية `reply_template` بجسم فارغ/مسافات أو `len > MAX_REPLY_TEMPLATE_LEN` **تُسقَط** عند التحليل مع `logger.warning("advisor: dropped reply_template (empty)")` ولا تصل لقاعدة البيانات.

**A-7 (B5) — نوع توصية مجهول لا يُسقط التشخيص** — `advisor.py` حول السطر 135: التحليل على مستوى **كل توصية**: `json.JSONDecodeError` للجسم كله ⇒ 502 `ai_invalid_output` (كما هو)؛ لكن `ValidationError` لتوصية واحدة (مثل `kind="content_plan_item"`) ⇒ تُسقَط تلك التوصية مع WARNING ويستمر الباقي؛ إن سقطت كلها ⇒ 200 بقائمة فارغة و`dropped: int` في الاستجابة. **اختبار:** مخرجات بـ3 توصيات إحداها مجهولة ⇒ 200 باثنتين و`dropped == 1`.

**A-8 — `.env.template`**: أضف كتلة `# --- AI gateway (D-075) ---` بالمتغيرات **وقيمها الافتراضية المطابقة لـ`core/config.py`**: `AI_PROVIDER=fake`، `AI_PROVIDER_BASE_URL=https://api.openai.com/v1`، `AI_PROVIDER_API_KEY=`، `AI_DEFAULT_MODEL=gpt-4o-mini`، `AI_HTTP_TIMEOUT_SECONDS=30`، `AI_DAILY_TOKEN_BUDGET_PER_BRAND=200000`، `AI_PRICE_TABLE_JSON={"gpt-4o-mini":{"prompt_per_1k":"0.00015","completion_per_1k":"0.0006"}}` مع تعليق: «**في وضع `platform` يجب أن يحتوي الجدول على `AI_DEFAULT_MODEL` وإلا رُفض كل نداء بـ503 `ai_model_unpriced` (fail-closed، D-075)**». متغيرات Part B/C (`PLATFORM_DAILY_USD_CAP`, `FEATURE_ENTITLEMENTS_ENFORCED`, `PLATFORM_ADMIN_USER_IDS`) تُضاف في كوميتاتها. اختبار بسيط `test_env_template_lists_all_ai_settings` يقارن أسماء حقول `Settings` التي تبدأ بـ`ai_`/`platform_`/`feature_` بما في القالب.

### 2-B. Part B — المالية (B1 + D-075) + هجرة 0013 (كوميت واحد نظيف)

**B-1 — نقل الملكية إلى `ai_gateway`** (بلا تغيير سلوكي في هذه الخطوة):
- جديد `src/modules/ai_gateway/cost_log.py`: `CostOutcome(StrEnum)` = `PENDING="pending"`, `OK="ok"`, `ERROR="error"`, `AMBIGUOUS="ambiguous"`؛ `CostEntry` (نفس حقول `brand_brain/domain/models.py:173` + `brand_id: UUID | None`)؛ `CostLogWriter(Protocol)` بـ`reserve(entry) -> UUID`، `settle(entry_id, *, prompt_tokens, completion_tokens, estimated_cost_usd, latency_ms, outcome, error_category)`، `refund(entry_id)`، `platform_usd_since(since) -> Decimal` (مجموع `estimated_cost_usd` حيث `credential_mode='platform'` و`outcome IN ('pending','ok','ambiguous')`)، `brand_tokens_since(brand_id, since) -> int`، `settle_stale(older_than) -> int`.
- جديد `ai_gateway/infrastructure/pg_cost_log.py`: `AICostLogRow` (ينتقل من `brand_brain/infrastructure/orm.py:186`) + `PgCostLogWriter` (ينتقل من `brand_brain/infrastructure/postgres_repository.py:452` ويُوسَّع). **يُحذف** الأصلان من `brand_brain` وتُحدَّث كل الاستيرادات. `grep -rn "CostEntry\|CostOutcome\|CostLogWriter\|AICostLogRow" src/modules/brand_brain` ⇒ **لا نتائج إلا استيرادات من `ai_gateway`**.
- `ai_gateway/infrastructure/pg_budget.py`: **يُحذف** `ai_token_usage_sources` و`UNION ALL` واستيرادات `ContentItemRow/ContentRevisionRow/ReplyDraftRow`؛ `PgBudgetUsageReader.combined_usage_since` يقرأ **`ai_cost_log` فقط** (`SUM(prompt_tokens + completion_tokens)` حيث `brand_id` و`created_at >= since` و`outcome IN ('pending','ok','ambiguous')`؛ `error` لا يُحتسب). `oldest_generation_since` من الجدول نفسه. **اختبار PG** `test_b1_content_plan_counts_once`: نداء 150 رمزاً ⇒ الاستهلاك المقروء **150** بالضبط (لا 1,200).

**B-2 — هجرة `0013_ai_cost_log_ownership_and_reservations.py`**:
- `ALTER COLUMN outcome TYPE VARCHAR(16)` (كان `String(8)`).
- إسقاط `fk_ai_cost_log_brand_id` وإعادة إنشائه بـ`ondelete="SET NULL"` + `ALTER COLUMN brand_id DROP NOT NULL` (قرار D-081: السجل المالي يبقى 7 سنوات مجهَّلاً). **اختبار PG:** حذف علامة ⇒ صفوف `ai_cost_log` تبقى و`brand_id IS NULL`.
- أعمدة جديدة: `settled_at TIMESTAMPTZ NULL`، `request_id VARCHAR(64) NULL` (معرّف الطلب للربط بالسجلات).
- فهرس `ix_ai_cost_log_mode_created (credential_mode, created_at)` لسقف المنصة، وفهرس جزئي `ix_ai_cost_log_pending (created_at) WHERE outcome = 'pending'` لمهمة التسوية.
- `downgrade` يعكس كل ذلك (يضع `brand_id` NOT NULL بعد حذف الصفوف اليتيمة صراحةً مع تعليق تحذيري). `alembic upgrade head && alembic downgrade 0012 && alembic upgrade head` يمرّ على PG 17.

**B-3 — `BudgetGuard` (جديد `ai_gateway/budget_guard.py`)** — يلفّ أي `Gateway` ويحلّ محلّ `CostLoggingGateway` في كل المستهلكين:
```python
class BudgetGuard:
    def __init__(self, inner: Gateway, *, writer: CostLogWriter, price_table: PriceTable,
                 daily_budget: DailyBudget, platform_daily_usd_cap: Decimal,
                 limits_reader: EntitlementLimitsReader | None = None,  # Part C يملؤه
                 clock: Callable[[], datetime] = utcnow) -> None: ...
    async def generate_text(self, request: GenerateTextRequest, *, ctx: BudgetContext) -> GenerateTextResponse: ...
```
`BudgetContext(brand_id, purpose, credential_mode, prompt_version, request_id)`. الترتيب داخل `generate_text` **حرفي**:
1. **تسعير مسبق:** `price = price_table.estimate(request.model, 1000, 1000)`؛ إن كان `None` و`credential_mode == "platform"` ⇒ `raise UnpricedModelError(model)` (**قبل أي كتابة أو نداء**). `Decimal("0")` يمرّ. في `user_key` ⇒ يمرّ بـ`unpriced=True`.
2. **رموز العلامة:** `verdict = await daily_budget.check(brand_id, usage_reader=…)`؛ غير مسموح ⇒ `raise BrandBudgetExceededError(verdict)`.
3. **سقف المنصة (platform فقط):** `used = await writer.platform_usd_since(now - 24h)`؛ إن `used >= platform_daily_usd_cap` ⇒ `raise PlatformBudgetExhaustedError(resets_in_seconds)`.
4. **حدود الباقة (إن وُجد `limits_reader`):** `await limits_reader.check(brand_id, purpose)` ⇒ قد يرفع `EntitlementLimitError` (يُعرَّف البروتوكول هنا في `ai_gateway/budget.py`، التنفيذ في Part C).
5. **احجز:** `entry_id = await writer.reserve(CostEntry(outcome=PENDING, prompt_tokens=None, completion_tokens=None, estimated_cost_usd=None, …))` عبر **`independent_session()`** (D-072) — يُثبَّت حتى لو فشل الطلب لاحقاً.
6. **النداء:** `try: resp = await inner.generate_text(request)` ⇒ `settle(entry_id, …, outcome=OK, estimated_cost_usd=price_table.estimate(model, resp.prompt_tokens, resp.completion_tokens))`؛ `except AIGatewayError as e` ⇒ إن كانت الفئة تدل على أن النداء **لم يصل** للمزوّد (`invalid_credential`, `auth_expired`, `unsupported_capability`, `bad_request` قبل الإرسال) ⇒ `refund(entry_id)`؛ وإلا (`timeout`, `retryable_server_error`, `provider_unavailable`, …) ⇒ `settle(outcome=ERROR, error_category=e.category)` مع الرموز إن توفرت وإلا تقدير تحفظي = `request` prompt tokens المقدَّرة + 0.
7. **فشل الكاتب نفسه:** في وضع `platform` **يُرفع** (`CostLogUnavailableError` ⇒ 503) — لا يُبتلع. في `user_key` يُسجَّل ERROR ويستمر النداء (المستخدم يدفع لمزوّده).

الاستثناءات (كلها في `ai_gateway/budget_guard.py`، ترث `BudgetGuardError`): `UnpricedModelError`, `BrandBudgetExceededError`, `PlatformBudgetExhaustedError`, `EntitlementLimitError`, `CostLogUnavailableError`. **لا تُضاف إلى `ErrorCategory`.** التحويل في مستوى الـrouter/الخدمة لكل مستهلك (موحَّد عبر دالة `budget_error_to_http` في `ai_gateway/http_errors.py`): `BrandBudgetExceededError`/`EntitlementLimitError` ⇒ **429 `generation_budget_exceeded`** (الاسم الموجود؛ يُلحق `limit_kind: brand_daily_tokens | plan_<name>` في التفاصيل) + `Retry-After`؛ `PlatformBudgetExhaustedError` ⇒ **503 `platform_budget_exhausted`** + `Retry-After`؛ `UnpricedModelError` ⇒ **503 `ai_model_unpriced`**؛ `CostLogUnavailableError` ⇒ **503 `cost_log_unavailable`**.

**B-4 — التركيب:**
- `core/config.py`: `platform_daily_usd_cap: Decimal = Decimal("25")` (ge 0)، `feature_budget_guard_strict: bool = True` (مفتاح قتل لفحص الـ`None` فقط؛ تعطيله يسجّل WARNING عند الإقلاع — D-076). أضفهما إلى `.env.template`.
- `ai_gateway/dependencies.py`: `build_price_table(settings) -> PriceTable`: `PriceTable.from_json(settings.ai_price_table_json)` **مع إضافة** `fake-1 ⇒ (0, 0)` تلقائياً عندما `ai_provider == "fake"` (ليبقى CI يعمل بلا جدول). `get_budget_guard(...)` تبعية واحدة يستهلكها الجميع.
- **كل المستهلكين** (`brand_brain/router.py:196–215` و`:249`، `brand_brain/advisor.py`، `content/service.py:137/:593`، `whatsapp/suggestions.py:83/:89`) يستبدلون `DailyBudget.check` المباشر و`CostLoggingGateway` بـ`BudgetGuard`. `grep -rn "DailyBudget(" src/modules/{content,whatsapp,brand_brain}` ⇒ **0 نتائج** (يبقى داخل `ai_gateway` فقط). `CostLoggingGateway` تُحذف أو تصبح غلافاً رقيقاً فوق `BudgetGuard` — لا مسارَي كتابة.
- **مهمة `settle_stale_reservations`:** تُضاف إلى حلقة المجدول الموجودة (`scheduler_loop` في `main.py:110` / `publishing/scheduler.py`) كخطوة ثانية كل دورة: `writer.settle_stale(older_than = now − 2 × ai_http_timeout_seconds)` ⇒ `pending` ⇒ `ambiguous` مع `estimated_cost_usd` تحفظي = `price × (prompt_estimate + max_tokens)` إن توفر السعر. **اختبار PG:** صف `pending` عمره 90 ث مع مهلة 30 ⇒ يصبح `ambiguous` ويُحتسب في سقف المنصة.

**B-5 — اختبارات Part B الإلزامية (PG 17):** `test_b1_content_plan_counts_once`؛ `test_b1_reserve_row_exists_before_provider_call` (مزوّد وهمي يفحص الجدول أثناء النداء)؛ `test_b1_timeout_settles_error_not_refund`؛ `test_b1_invalid_credential_refunds`؛ `test_b1_unpriced_model_platform_503_no_call_no_row` (respx: 0 نداء، جدول: 0 صفوف)؛ `test_b1_zero_price_is_valid` (`Decimal("0")` ⇒ 200 وصف `ok` بتكلفة 0)؛ `test_b1_user_key_unpriced_passes_null_cost`؛ `test_b1_platform_cap_503_with_retry_after`؛ `test_b1_user_key_not_counted_in_platform_cap`؛ `test_b1_fake_provider_ci_path` (`AI_PROVIDER=fake` بلا `AI_PRICE_TABLE_JSON` ⇒ يعمل)؛ `test_b1_writer_failure_platform_503_user_key_continues`؛ `test_b1_brand_delete_sets_null`؛ `test_b1_settle_stale`؛ اختبارات التماثل القديمة (`test_a4_i1/i2`) تُحدَّث لتقرأ من المصدر الوحيد وتبقى خضراء.

### 2-C. Part C — `entitlements` (D-073/D-078) + `platform_admin` + `learning_consent` (D-080) + إحالة CTWA + هجرة 0014 + Safe Removability (D-076)

**C-1 — وحدة جديدة `src/modules/entitlements/`** (نفس تخطيط الوحدات الحالية: `domain/{models,repository}.py`, `infrastructure/{orm,postgres_repository}.py`, `service.py`, `router.py`, `dependencies.py`, `exceptions.py`, **`REMOVABILITY.md`**):
- `domain/models.py`: `Plan(StrEnum) = trial|starter|growth|agency`؛ `PlanLimits(frozen)`: `daily_tokens: int`, `monthly_posts: int`, `monthly_wa_conversations: int`, `max_brands: int`, `seats: int`؛ **الافتراضات الثابتة في `DEFAULT_LIMITS: dict[Plan, PlanLimits]`** (قابلة للتجاوز لكل علامة): `trial(20_000, 10, 50, 1, 1)`, `starter(60_000, 40, 300, 1, 1)`, `growth(200_000, 150, 1_000, 3, 3)`, `agency(600_000, 600, 3_000, 10, 10)`؛ `BrandEntitlement(brand_id, plan, limits, active_until: datetime | None, is_active, referred_by_brand_id: UUID | None, updated_at)`؛ `EntitlementEvent(id, brand_id, kind ∈ {activated, extended, downgraded, suspended, referral_credit, limits_overridden}, actor_user_id, payload: dict, created_at)`.
- **اللغة:** حدود الرموز اليومية في الباقة **تحلّ محلّ** `ai_daily_token_budget_per_brand` عند وجود entitlement نشط (الإعداد يبقى افتراضاً للعلامات بلا سجل = `trial`).
- `EntitlementLimitsReader` (البروتوكول في `ai_gateway/budget.py` — Part B) تنفّذه `entitlements/service.py::PgEntitlementLimitsReader.check(brand_id, purpose)`: علامة بلا سجل ⇒ `trial`؛ `is_active=False` أو `active_until < now` ⇒ `EntitlementLimitError(limit_kind="plan_inactive")` (**429 `generation_budget_exceeded`** بتفاصيل `limit_kind` — لا رمز خطأ جديد)؛ تجاوز `daily_tokens` ⇒ `limit_kind="plan_daily_tokens"`. فحوص `monthly_posts` (عند الموافقة/الجدولة في `content`) و`monthly_wa_conversations` (عند إنشاء محادثة جديدة في `whatsapp`) عبر `check_counter(brand_id, counter: Literal["monthly_posts","monthly_wa_conversations"])` — الاستهلاك يُقرأ من الجداول الموجودة **عبر بروتوكول قارئ تعرّفه الوحدة المستهلِكة** (لا استيراد ORM عبر الحدود — D-016/D-076).
- **مفتاح القتل:** `feature_entitlements_enforced: bool = True` في `core/config.py`؛ `False` ⇒ القارئ يُرجع «مسموح» دائماً ويُسجَّل WARNING واحد عند الإقلاع.

**C-2 — هجرة `0014_entitlements_consent_referral.py`:**
- `brand_entitlements(brand_id PK FK brands CASCADE, plan VARCHAR(16), limits JSONB, active_until TIMESTAMPTZ NULL, is_active BOOL NOT NULL DEFAULT false, updated_at)`.
- `entitlement_events(id UUID PK, brand_id FK brands **SET NULL** nullable (D-081), kind VARCHAR(24), actor_user_id UUID NULL FK users SET NULL, payload JSONB, created_at)` + فهرس `(brand_id, created_at)`.
- `brands.referred_by_brand_id UUID NULL FK brands(id) ON DELETE SET NULL` + `CHECK (referred_by_brand_id <> id)`.
- `brands.learning_consent VARCHAR(16) NOT NULL DEFAULT 'signals_only'` + `CHECK IN ('signals_only','none','full')`.
- `wa_messages.referral JSONB NULL` (كائن `referral` من Meta CTWA: `source_url, source_id, source_type, headline, body, ctwa_clid` — يُخزَّن بعد قصّ النصوص إلى 512 حرفاً).
- `downgrade` كامل. `upgrade head → downgrade 0013 → upgrade head` يمرّ.

**C-3 — دور `platform_admin` (نسخة الإعدادات؛ ينتقل إلى قاعدة البيانات في SPRINT-11.1):**
- `core/config.py`: `platform_admin_user_ids: list[UUID] = []` (من `PLATFORM_ADMIN_USER_IDS` مفصولاً بفواصل). فارغ ⇒ **كل مسارات `/admin/*` تُرجع 403 `platform_admin_required`** (آمن افتراضاً).
- `auth/dependencies.py`: `PlatformAdmin = Annotated[AuthenticatedActor, Depends(require_platform_admin)]` تبني على `CurrentUserFresh` وتفحص `actor.user_id in settings.platform_admin_user_ids` ⇒ وإلا 403 `platform_admin_required`. لا تُستخدم `email` (غير موجودة في `AuthenticatedActor`).
- **تدقيق:** كل نداء إداري ناجح يكتب `entitlement_events` بـ`actor_user_id` — لا استثناء.

**C-4 — خدمة التفعيل والإحالة** (`entitlements/service.py`):
- `activate(brand_id, plan, months: int (1..12), actor_user_id, note)`: upsert، `active_until = max(now, active_until) + months×30d`، `is_active=True`، حدث `activated` (أو `extended`). **الإحالة (D-078):** إن كانت العلامة لها `referred_by_brand_id` **وهذا أول حدث `activated` لها** ⇒ حدث `referral_credit` للعلامة المُحيلة بـ`payload={"referred_brand_id":…, "percent":10, "plan":…, "months":…}` — **حدث مدقَّق فقط، لا رصيد آلي ولا تعديل حدود**. معاملة واحدة مع `SELECT … FOR UPDATE` على صف العلامة المُحالة؛ اختبار PG لتفعيلين متزامنين ⇒ حدث `referral_credit` واحد.
- `set_limits_override(brand_id, limits, actor)` ⇒ حدث `limits_overridden`. `suspend(brand_id, actor, reason)` ⇒ `is_active=False` + حدث `suspended`.

**C-5 — المسارات (4 جديدة محمية ⇒ `PROTECTED_ROUTE_COUNT == 66`):**
| # | المسار | الحارس | الاستجابة |
|---|---|---|---|
| 1 | `POST /api/v1/admin/entitlements/activate` | `PlatformAdmin` | 200 `BrandEntitlementOut`؛ 404 `brand_not_found`؛ 422 |
| 2 | `GET /api/v1/admin/entitlements/{brand_id}` | `PlatformAdmin` | 200 `BrandEntitlementOut` + آخر 20 حدثاً |
| 3 | `GET /api/v1/brands/{brand_id}/entitlement` | `READ_ROLES` | 200 `BrandEntitlementPublic` (الخطة، `active_until`، الحدود، **الاستهلاك الحالي** لكل حدّ) — بلا أحداث ولا `referred_by` |
| 4 | `PATCH /api/v1/brands/{brand_id}/learning-consent` | `GRANT_ROLES` (OWNER فقط) | 200 `{learning_consent}`؛ جسم `{"learning_consent": "signals_only"\|"none"\|"full"}` |
- الوسوم: `APIRouter(tags=["Entitlements (D-073)"])` و`tags=["Platform admin"]`؛ يُركَّبان في `main.py` بعد `brand_brain_router`. لا مسارات عامة جديدة.
- `tests/test_auth_guard.py:75` و`tests/modules/content/test_router.py:1417` ⇒ `66`.

**C-6 — `learning_consent` (D-080):**
- `tenancy/domain/models.py::Brand` يكتسب `learning_consent` و`referred_by_brand_id`؛ `BrandOut` يعرض `learning_consent` فقط.
- `POST /brands` يقبل حقلاً اختيارياً `referred_by_brand_id` (يُتحقق من وجود العلامة) — **ليس مساراً جديداً**.
- **فلترة الكاتب:** `whatsapp/suggestions.py` (كاتب `wa_learning_signals`) و`brand_brain` (كاتب `learning_signals`) يقرآن `learning_consent` عبر بروتوكول `BrandConsentReader` تعرّفه كل وحدة وتنفّذه `tenancy`: `none` ⇒ **لا تُكتب** إشارة جديدة؛ `signals_only`/`full` ⇒ تُكتب. أي قارئ تعلّم عبر العلامات (لا يوجد بعد) **يجب** أن يفلتر `full` فقط — يُسجَّل في `REMOVABILITY.md`.

**C-7 — إحالة CTWA (`wa_messages.referral`):** `WebhookProcessor` يخزّن `messages[i]["referral"]` إن وُجد (قصّ ≤ 512 لكل نص، إسقاط المفاتيح غير المعروفة)؛ `MessageRow.referral: Mapped[dict | None]`؛ يُعرض في مسار الرسائل الموجود كحقل اختياري. لا تغيير في ON CONFLICT. اختبار: حمولة Meta بـ`referral` ⇒ محفوظ؛ `referral` 10 KiB ⇒ قُصّ.

**C-8 — Safe Removability (D-076) — إلزامي للقبول:**
- `src/modules/entitlements/REMOVABILITY.md`: الجداول، المسارات الأربعة، الإعدادات (`FEATURE_ENTITLEMENTS_ENFORCED`, `PLATFORM_ADMIN_USER_IDS`)، نقاط الوصل (`EntitlementLimitsReader` المحقون في `BudgetGuard` عبر `ai_gateway/dependencies.py`)، خطوات الإزالة (تعطيل المفتاح ⇒ إزالة التركيب من `main.py`/`ai_gateway/dependencies.py` ⇒ `alembic downgrade`).
- `tests/modules/entitlements/test_isolation.py`: لا ملف خارج `entitlements/` يستورد `src.modules.entitlements` **إلا** `src/main.py` و`src/modules/ai_gateway/dependencies.py` (قائمة بيضاء صريحة داخل الاختبار).
- `tests/modules/entitlements/test_kill_switch.py`: `FEATURE_ENTITLEMENTS_ENFORCED=false` ⇒ علامة معلّقة تولّد محتوى بنجاح ويُسجَّل WARNING.
- **G5:** `brand_brain/infrastructure/internal_metrics.py:25–27` (استيراد ORM من `content`/`publishing`/`whatsapp` للقراءة) — **لا يُغيَّر في هذا السبرنت** ولا تُضاف قاعدة `ruff` جديدة للاستيراد عبر الحدود؛ اختبار العزل يغطي `entitlements` فقط.

---

## 3. معايير القبول (المخطط يتحقق منها حرفياً على PG 17 UTF8 ببيئة مُعاد بناؤها)

| # | المعيار | كيف يتحقق المخطط |
|---|---|---|
| 1 | **ثلاثة كوميتات متسلسلة A ⇒ B ⇒ C**، كلٌّ يمرّ وحده: `ruff check . && ruff format --check . && pytest -q --cov=src --cov-fail-under=95` + `alembic upgrade head` | `git checkout <sha>` لكل كوميت |
| 2 | `PROTECTED_ROUTE_COUNT == 66` والعامة 6 (لا مسار عام جديد) | `tests/test_auth_guard.py` + مقارنة `openapi.json` |
| 3 | **B6:** 8 معرّفات غير موقَّعة ⇒ ≤1 استعلام ثم 401؛ 9 معرّفات ⇒ 400 بـ0 استعلام؛ 950 KiB/10,000 معرّف ⇒ 400 بـ0 استعلام | عدّاد `before_cursor_execute` على PG |
| 4 | **B2-أ:** `127.0.0.1.nip.io`, `169.254.169.254.nip.io`, `10.0.0.1.nip.io`, `fd00--1.sslip.io` ⇒ `private_address` و**0 طلب شبكي** | `respx` + حاقن `getaddrinfo` |
| 5 | **B2-ب/ج:** robots 4.7 MiB ⇒ ≤64 KiB مقروءة؛ صفحة 503 ⇒ 0 أدلة | اختبارات Part A |
| 6 | **B3:** VIEWER لا يرى `banned_phrases`/`reply_templates` في `/profile/revisions`؛ OWNER يراهما | اختبار + فحص JSON |
| 7 | **B4:** قبولان متزامنان ⇒ `[200, 409]`؛ جسم فارغ يُسقَط | PG `asyncio.gather` |
| 8 | **B5:** توصية مجهولة ⇒ 200 مع `dropped == 1` | اختبار advisor |
| 9 | **B1:** نداء 150 رمزاً ⇒ استهلاك **150**؛ `grep -rn "UNION ALL\|ContentRevisionRow\|ReplyDraftRow\|from src.modules.brand_brain" src/modules/ai_gateway` ⇒ 0 | PG + grep |
| 10 | **احجز ثم سوِّ:** صف `pending` موجود **أثناء** نداء المزوّد؛ timeout ⇒ `error` لا refund؛ `invalid_credential` ⇒ refund؛ stale 90 ث ⇒ `ambiguous` | اختبارات B-5 |
| 11 | **Fail-closed:** نموذج بلا سعر في `platform` ⇒ 503 `ai_model_unpriced` **بلا نداء وبلا صف**؛ `Decimal("0")` ⇒ 200؛ `user_key` غير مسعَّر ⇒ 200 بـ`estimated_cost_usd IS NULL`؛ `AI_PROVIDER=fake` بلا جدول ⇒ يعمل | اختبارات B-5 |
| 12 | **سقف المنصة:** ⇒ 503 `platform_budget_exhausted` + `Retry-After`؛ `user_key` لا يُحتسب | PG |
| 13 | **فشل الكاتب:** `platform` ⇒ 503 `cost_log_unavailable`؛ `user_key` يستمر | اختبار |
| 14 | **هجرات 0013/0014:** `upgrade head → downgrade 0012 → upgrade head` نظيف؛ حذف علامة ⇒ `ai_cost_log.brand_id IS NULL` و`entitlement_events.brand_id IS NULL` والصفوف باقية | PG |
| 15 | **Entitlements:** تفعيل ⇒ `activated` + `active_until`؛ تفعيلان متزامنان لعلامة مُحالة ⇒ `referral_credit` **واحد**؛ علامة معلّقة ⇒ 429 `generation_budget_exceeded` بـ`limit_kind=plan_inactive`؛ مفتاح القتل يعطّل | PG |
| 16 | **platform_admin:** قائمة فارغة ⇒ 403 لكل `/admin/*`؛ مستخدم عادي ⇒ 403؛ المُدرج ⇒ 200 وحدث مدقَّق | اختبار |
| 17 | **learning_consent:** افتراض `signals_only`؛ `none` ⇒ لا إشارات جديدة في `wa_learning_signals` و`learning_signals`؛ MANAGER ⇒ 403 على PATCH | PG |
| 18 | **CTWA:** `referral` يُحفظ ويُقصّ | اختبار |
| 19 | **Removability:** `REMOVABILITY.md` مكتمل؛ `test_isolation.py` يمرّ؛ `grep -rn "src.modules.entitlements" src --include=*.py` ⇒ فقط `main.py` + `ai_gateway/dependencies.py` | grep |
| 20 | **لا تغيير في `ErrorCategory`**؛ لا تبعية جديدة (`git diff main -- pyproject.toml` فارغ أو `per-file-ignores` موثَّق فقط) | diff |
| 21 | `.env.template` يحوي كل متغيرات `AI_*`/`PLATFORM_*`/`FEATURE_*` بقيم مطابقة لـ`Settings` | اختبار A-8 |
| 22 | التغطية ≥ 95% إجمالاً و≥ 90% لكل ملف جديد في `ai_gateway/` و`entitlements/` | `--cov-report=term-missing` |

---

## 4. أوامر

```bash
# 0) البداية (من main @ SHA دمج هذا التوجيه)
git checkout work/implementation && git fetch origin && git merge --ff-only origin/main

# 1) بيئة PG 17 UTF8 (كما في SPRINT-10)
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/aimp_test
alembic upgrade head

# 2) بعد كل Part
ruff check . && ruff format --check .
pytest -q --cov=src --cov-report=term-missing --cov-fail-under=95
alembic downgrade 0012 && alembic upgrade head      # Part B ⇒ 0012؛ Part C ⇒ 0013 ثم head

# 3) فحوص grep الملزمة (يجب أن تُرجع 0 نتائج)
grep -rn "UNION ALL\|ContentRevisionRow\|ReplyDraftRow\|from src.modules.brand_brain" src/modules/ai_gateway
grep -rn "DailyBudget(" src/modules/content src/modules/whatsapp src/modules/brand_brain
grep -rn "budget_unavailable\|budget_exceeded\b" src/modules/ai_gateway/contract.py
grep -rn "src.modules.entitlements" src --include=*.py | grep -v "src/modules/entitlements/\|src/main.py\|ai_gateway/dependencies.py"

# 4) الدفع بعد كل كوميت (قاعدة الساندبوكس)
git push origin work/implementation
```

---

## 5. التزامات المنفذ

1. **كوميت واحد لكل Part** بالترتيب A ⇒ B ⇒ C، كلٌّ أخضر وحده على PG 17 (معيار 1). لا كوميتات «إصلاح لاحق» داخل الـPR — الإصلاح يُدمج في كوميت الـPart قبل الدفع النهائي.
2. **لا تلمس** `ErrorCategory`، لا تضف تبعية، لا تضف مساراً عاماً، لا تبدأ الكتالوج (SPRINT-11.1).
3. **الأسماء حرفية** كما في §2 (`webhook_rejected`, `private_address`, `unavailable`, `profile_conflict`, `ai_model_unpriced`, `platform_budget_exhausted`, `cost_log_unavailable`, `platform_admin_required`, `limit_kind`, `dropped`). أي انحراف يُبرَّر في §6.
4. **ادفع بعد كل كوميت** وحدّث §6 في كوميت رابع منفصل `docs(sprint-11): executor report`.
5. إن اكتشفت تعارضاً بين هذا التوجيه والكود الفعلي (اسم دالة/سطر)، **الكود الفعلي هو المرجع**: نفّذ المقصود وسجّل الفرق في §6 «انحرافات».
6. إن تجاوز Part C وحده 1,200 سطر `src/` فعلياً، **توقف** بعد Part B، ادفع، واكتب في §6 طلب تقسيم — المخطط يراجع A+B ويصدر C كـSPRINT-11.0.1.

---

## 6. تقرير المنفذ (يملؤه المنفذ)

| البند | القيمة |
|---|---|
| SHA الكوميتات | A: `…` · B: `…` · C: `…` · docs: `…` |
| نتائج `pytest` (العدد/التغطية) لكل كوميت | A: … · B: … · C: … |
| `PROTECTED_ROUTE_COUNT` | … (المتوقع 66) |
| هجرات مُختبَرة ذهاباً وإياباً | 0013: … · 0014: … |
| نتائج grep الملزمة (§4-3) | … |
| أسطر `src/` المضافة لكل Part | A: … · B: … · C: … |
| انحرافات عن §2 (مع السبب) | … |
| ما يحتاج قرار المالك | … |

---

## 7. ما ينتظر المالك

1. **تدوير التوكن** (`ghp_…` ظهر في المحادثة ≥ 13 مرة) + تفعيل حماية فرع `main` (مراجعة إلزامية + CI أخضر).
2. تحديد `PLATFORM_ADMIN_USER_IDS` في بيئة التشغيل (معرّف المستخدم الخاص بك) قبل أول تفعيل يدوي.
3. تأكيد `AI_PRICE_TABLE_JSON` للنموذج الافتراضي (أو تبديل `AI_DEFAULT_MODEL` إلى نموذج مسعَّر حياً) — وإلا يرفض وضع `platform` كل نداء (fail-closed بالتصميم).
4. بعد قبول SPRINT-11: المخطط يصدر **SPRINT-11.1** (كتالوج المزودين + `/admin/ai/*` + probe + `AI_ALLOW_LOCAL_PROVIDER_HOSTS`) ثم SPRINT-12 (L1 + Pulse-SQL + أسقف واتساب).
