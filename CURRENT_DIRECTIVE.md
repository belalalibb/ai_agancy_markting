# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-06 رُوجع مستقلاً و**قُبل ودُمج** بواسطة المخطط (`main` @ `20a18fc`؛ التقرير في [`sprints/SPRINT-06_REVIEW.md`](./sprints/SPRINT-06_REVIEW.md)). هذا التوجيه يفتح **المحطة M4 — الجدولة والنشر الفعلي** (خريطة المحطات في `WORK_STATE.md` §2-ب) فوق نواة المحتوى، مع **الجزء A الإلزامي** لإغلاق ملاحظات المالك الأربع التي ثبتت كلها في مراجعة 06، و**الجزء B-0** لتثبيت عقد المزوّد (D-067/D-068) قبل أن تتراكم محركات على تصنيف أخطاء ناقص.

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-07-SCHEDULING-AND-PUBLISHING` |
| **النوع** | Part A: تصحيحات إلزامية (A-1..A-6) · Part B-0: عقد المزوّد (بلا سلوك جديد للمستخدم) · Part B-1/B-2: ميزة M4 (جدولة + نشر Meta) · **هجرة واحدة** `0006_publishing` |
| **الحالة** | `ISSUED` — جاهز للتنفيذ فور دمج هذا التوجيه في `main` (المخطط يدمجه بنفسه — D-063) |
| **يبدأ من** | `main` @ `8361c22` (دمج PR #20 — هذا التوجيه). أول أمر: `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` |
| **الفرع المستهدف** | `work/implementation` ⇒ PR إلى `main` بعنوان `SPRINT-07: scheduling + Meta publishing (M4) + provider contract (D-067/D-068) + SPRINT-06 review fixes` |
| **الكوميتات** | **أربعة**: Part A ⇒ Part B-0 ⇒ Part B-1 (هجرة + دورة الحياة الموسَّعة + مستودع المهام) ⇒ Part B-2 (الناشر + العامل + API). كل كوميت يُدفع فوراً، **وكل ملف يُدفع فور اكتماله** (`wip:` مسموح ثم يُسحق) |
| **تبعيات جديدة** | **لا شيء.** لا Temporal، لا Redis، لا LiteLLM. العامل حلقة `asyncio` داخل العملية فوق PostgreSQL (`FOR UPDATE SKIP LOCKED`) خلف `Scheduler` Protocol — قرار `REPOSITORY_EVALUATIONS.md` §5-هـ |
| **الحجم المتوقع** | ~1300 سطر كود + ~1200 سطر اختبارات |
| **تاريخ الإصدار** | 2026-10-30 |

---

## 0. لماذا؟

1. **A-1 (O1، ثبت):** `schemas.py:72-84` `ContentRevisionPublic.provenance` يمرّ كاملاً (provider/model/tokens/latency) لكل الأدوار بينما `generation_log` (مشتق منه، `router.py:230-245`) يُخفى عن VIEWER — إخفاء وهمي يخرق جدول الرؤية D-064.
2. **A-2 (O2، ثبت):** `schemas.py:27-38` و`domain/models.py:53` تقبلان 5000 حرفاً لأي قناة؛ `CHANNEL_LIMITS` (`prompts.py:18`) يُطبَّق في `/generate` فقط ⇒ مسودة إنستغرام 2201–5000 حرفاً تمرّ الدورة كلها لتفشل عند النشر.
3. **A-3 (O3، ثبت تجريبياً 3/3 على PG):** `postgres_repository.py:113-119` `max+1` بلا قفل + `uq_content_revisions_item_no` ⇒ الخاسر `IntegrityError` 500 (لا معالجة في الوحدة ولا `main.py`).
4. **A-4 (O4، ثبت):** `router.py:152/206` استعلام `current_body` لكل عنصر (N+1)؛ `router.py:216` `response_model_exclude_none=True` يُسقط `target_page_id`/`scheduled_for`/`current_revision_id`/`current_body` المشروعة (نقيض درس KNOWN_ISSUES 9).
5. **A-5/A-6 (ملاحظات المخطط §5 في المراجعة):** حدّ 20 طلباً/ساعة لا يحدّ الرموز؛ `prompt_hash` بلا `prompt_version` يمنع قياس الجودة لاحقاً (D-069).
6. **B-0 (D-067/D-068):** البوابة الحالية تصنّف الأخطاء في ثلاث فئات فقط؛ أي محرك جديد سيبني على تصنيف ناقص ولن يقود Fallback آلياً. التثبيت الآن رخيص (≤ 300 سطر) ومعه قاعدة **No-Bypass** آلية في CI قبل أن يظهر ثاني مستهلك للنماذج.
7. **Part B (M4):** المنصة تصل بالعنصر إلى `scheduled` ثم لا يحدث شيء. النشر الفعلي خلف الموافقة البشرية هو أول «قيمة مُسلَّمة» للعميل (TTFV — `COMPETITOR_AND_PRODUCT_RESEARCH.md` §2) وأول فعل خارجي يستدعي كامل D-055: موافقة صريحة، سجل محاولات قابل للتدقيق، تراجع عند الفشل، ولا تكرار صامت.

---

## 1. النطاق

**داخل:** A-1..A-6؛ B-0 عقد المزوّد + قاعدة No-Bypass؛ هجرة `0006_publishing`؛ حالتا `published`/`publish_failed`؛ `publish_jobs` + `publish_attempts`؛ `Scheduler` Protocol + `PostgresScheduler` (عامل داخل العملية خلف علم إعدادات)؛ `Publisher` Protocol + `MetaPublisher` (Facebook Page نصّ/رابط، Instagram صورة واحدة بخطوتين)؛ نقاط `POST /{page_id}/feed`، `POST /{ig_user_id}/media`، `POST /{ig_user_id}/media_publish` في `meta_client.py`؛ نطاقا النشر في الإعدادات؛ API سجل النشر؛ اختباراتها؛ تحديث `WORK_STATE.md` §1/§6 من المنفذ.
**خارج (ممنوع):** رفع ملفات/تخزين وسائط (روابط `https` فقط في `media_refs`)، فيديو/كاروسيل/قصص، منصات غير Meta (M5)، رابط موافقة للعميل الخارجي (M10)، Temporal/Redis/LiteLLM، BYOK وجدول `brand_provider_keys` (M7)، `learning_signals` (M7)، أي تغيير في «الموافقة للمالك فقط»، الملفات المحظورة (`ARCHITECTURE_LOCK.md`, `SESSION_RECOVERY.md`, `imaster_planner.md`, `README.md`, `AI_Marketing_Platform_Hub/**`, `sprints/**`).

---

## 2. المواصفات

### 2-0. القواعد الحاكمة (D-055 / D-064 / D-066 / D-068 — إلزامية)

**(أ) قناة الأسرار:** توكن الصفحة يُفكّ داخل `MetaPublisher` لحظة النداء ويُحذف في `finally` (نمط SPRINT-05)؛ Bearer + `appsecret_proof` فقط؛ لا توكن في الرابط ولا في `publish_attempts` ولا في أي سجل. مفتاح المزوّد كما في SPRINT-06.

**(ب) جدول الرؤية (امتداد SPRINT-06 §2-0-ب):**

| البيان | OWNER | MANAGER | VIEWER |
|---|---|---|---|
| `revisions[].provenance` **العام** `{kind, generated_at, prompt_version}` | ✅ | ✅ | ✅ |
| `generation_log` (provider/model/prompt_hash/tokens/latency/credential_mode/truncated) | ✅ | ✅ | ❌ (المفتاح غائب من الاستجابة — لا `null`) |
| سجل النشر: `status`, `run_at`, `external_permalink`, `published_at`, `attempt_count` | ✅ | ✅ | ✅ |
| سجل النشر: `attempts[].error_class`, `graph_error_code`, `graph_trace_id` | ✅ | ✅ | ❌ |
| `unschedule`/`archive` عنصر `scheduled` أو `publish_failed` | ✅ | ✅ (عناصره فقط للأرشفة) | ❌ |
| `archive` عنصر `published` (أرشفة محلية؛ **لا حذف من Meta**) | ✅ | ✅ (عناصره) | ❌ |
| `publish/retry` | ✅ | ✅ | ❌ |

**(ج) Evidence Schema للنشر (D-055):** كل محاولة نشر صفّ في `publish_attempts` يُكتب **قبل** نداء Graph (`outcome=NULL`) ويُحدَّث بعده. محاولة بلا نتيجة بعد انقضاء الإيجار = **نتيجة مبهمة** ⇒ المهمة `failed` بـ`error_class=ambiguous_outcome` و**لا إعادة تلقائية** (قد يكون المنشور خرج فعلاً) — المالك يقرّر. لا تكرار صامت تحت أي ذريعة.

**(د) No-Bypass (D-068):** لا استيراد لـ`httpx`/أي SDK نماذج خارج `src/modules/ai_gateway/` و`src/modules/social_connect/meta_client.py` (و`src/core/**` إن احتاج `health`)؛ يُفرض بقاعدة `ruff` + اختبار (B-0).

---

### Part A — إلزامي قبل أي ميزة (كوميت واحد)

#### A-1. `provenance` بطبقتين (D-066)
- `domain/models.py`: `PUBLIC_PROVENANCE_KEYS: Final[frozenset[str]] = frozenset({"kind", "generated_at", "prompt_version"})`.
- `schemas.py`: `ContentRevisionPublic.provenance` يُبنى بـ`{k: v for k, v in rev.provenance.items() if k in PUBLIC_PROVENANCE_KEYS}`. `generation_log` يبقى المصدر الوحيد للحقول الخاصة. التخزين لا يتغيّر (قاموس JSONB واحد).
- **`A1-U1`**: استجابة VIEWER لـ`GET /items/{id}`: لا مفتاح من `{provider, model, prompt_hash, prompt_tokens, completion_tokens, latency_ms}` في أي مكان من الجسم (مسح تكراري للـJSON). **`A1-U2`**: OWNER يراها في `generation_log` فقط لا في `revisions[].provenance`.

#### A-2. حدود القناة في الخدمة
- `CHANNEL_LIMITS` ينتقل إلى `domain/models.py` (يُستورد منه في `prompts.py`). دالة `enforce_channel_limit(channel, body) -> None` ترفع `BodyTooLongError(channel, limit, length)` ⇒ 422 `{"error":"body_too_long","channel","limit","length"}`.
- تُستدعى في `create_item`، `update_body`؛ في `generate` يبقى القصّ لكنه **يُسجَّل**: `provenance.truncated=true` (حقل خاص). المخططات تبقي سقف 5000 عاماً.
- **`A2-U1`** (parametrize): `instagram` 2201 ⇒ 422 على create وupdate؛ 2200 ⇒ 201/200؛ `facebook_page` 5000 ⇒ مقبول. **`A2-U2`**: `generate` بمخرج 2300 حرفاً لإنستغرام ⇒ مراجعة 2200 + `truncated=true` في `generation_log`.

#### A-3. ترقيم المراجعات تحت قفل صف العنصر
- `repository.py` Protocol: `get_for_update(brand_id, item_id) -> ContentItem | None` (`SELECT … FOR UPDATE`؛ في Fake: `asyncio.Lock` لكل عنصر). يُحذف `next_revision_no` من الـProtocol؛ `revision_no` يُحسب بعد القفل بـ`SELECT COALESCE(MAX)+1` داخل المعاملة نفسها (آمن تحت القفل) — أو عمود `revision_count` على العنصر إن فضّلت (وثّق الاختيار؛ العمود يستلزم ضمّه إلى هجرة 0006).
- `service.update_body`/`generate` تمرّان عبر `get_for_update` قبل أي كتابة (الإنشاء الأول بلا سباق: العنصر جديد).
- **`A3-I1`** على PG: إعادة تجربة المخطط (جلستان، `asyncio.Barrier(2)` بعد القراءة وقبل الكتابة) على `update_body` ⇒ `[200, 200]` بأرقام مراجعات `2, 3` أو `[200, 409]` إن تعارض CAS الحالة — **لا 500 ولا `IntegrityError`** (3 تكرارات في الاختبار).

#### A-4. قائمة بلا N+1 ونماذج استجابة حسب الدور
- `repository.list_items` يُرجع `current_body` بـ`LEFT JOIN content_revisions ON content_revisions.id = content_items.current_revision_id` (Protocol: `list_items(...) -> tuple[list[tuple[ContentItem, str | None]], bool]`). تُحذف `service.current_body()` و`router._public()`.
- `get_item`: احذف `response_model_exclude_none`. نموذجان: `ContentItemDetail` (بلا `generation_log`) و`ContentItemDetailWithUsage(ContentItemDetail)` (+ `generation_log: list[GenerationLogEntry]` بنموذج صريح لا `dict`)؛ `response_model=ContentItemDetail | ContentItemDetailWithUsage`.
- **`A4-U1`**: استجابة VIEWER تحوي `"target_page_id": null` و`"scheduled_for": null` ولا تحوي مفتاح `generation_log`. **`A4-I1`** على PG: قائمة من 25 عنصراً ⇒ عدد استعلامات SQL ≤ 2 (عدّاد عبر `event.listen(engine.sync_engine, "before_cursor_execute")`).

#### A-5. ميزانية رموز يومية لكل علامة
- إعداد `AI_DAILY_TOKEN_BUDGET_PER_BRAND: int = 200_000`. قبل `generate`: `SUM((provenance->>'prompt_tokens')::int + (provenance->>'completion_tokens')::int)` لمراجعات `ai_generated` في آخر 24 ساعة للعلامة (استعلام واحد في المستودع: `ai_token_usage_since(brand_id, since) -> int`). التجاوز ⇒ 429 `{"error":"generation_budget_exceeded","budget","used","resets_in_seconds"}`.
- **`A5-U1`**: استخدام 199_900 + طلب جديد ⇒ يمرّ؛ 200_000 ⇒ 429. **`A5-I1`** على PG: الاستعلام يحسب من JSONB فعلاً (صفوف `human` لا تُحتسب).

#### A-6. `prompt_version`
- `prompts.py`: `PROMPT_VERSION: Final = "content_draft@1"`؛ يُكتب في `provenance` (حقل عام). **`A6-U1`**: موجود في `revisions[].provenance` لكل الأدوار.

---

### Part B-0 — عقد المزوّد (D-067/D-068؛ كوميت واحد؛ بلا سلوك جديد للمستخدم)

- `src/modules/ai_gateway/contract.py`: `Operation = Literal["generate_text"]` (الأسماء السبعة الأخرى في تعليق محجوز من D-067 — **لا تُنفَّذ**)؛ `ErrorCategory(StrEnum)` بالفئات الـ12 حرفياً: `auth_expired, invalid_credential, rate_limited, quota_exceeded, model_unavailable, provider_unavailable, unsupported_capability, bad_request, content_rejected, timeout, retryable_server_error, non_retryable_error`؛ `RETRYABLE_DEFAULT: dict[ErrorCategory, bool]` (yes: `auth_expired, rate_limited, timeout, retryable_server_error`؛ الباقي no)؛ `AIError(Exception)` بحقول `category, retryable, retry_after_ms | None, provider_code: str | None` (**مُنقّى**: `^[a-z0-9_]{1,64}$`، بلا نص رسالة المزوّد)؛ `Usage(prompt_tokens, completion_tokens)`؛ `CredentialMode = Literal["platform", "user_key"]`؛ `CompletionRequest.credential_mode: CredentialMode = "platform"`؛ `CompletionResult.usage: Usage`, `.credential_mode`.
- `providers.py` ⇒ حزمة `facades/`: `fake.py`، `openai_compatible.py`. الأخطاء القديمة (`AIRateLimitedError`, `AIUpstreamError`) تُستبدل بـ`AIError` بالفئة الصحيحة: 401/403 ⇒ `invalid_credential`؛ 429 ⇒ `rate_limited` (+`retry_after_ms`)؛ 404 ⇒ `model_unavailable`؛ 400/413/422 ⇒ `bad_request` (أو `content_rejected` إن احتوى `error.code` على `content`)؛ 5xx ⇒ `retryable_server_error`؛ timeout ⇒ `timeout`؛ شبكة ⇒ `provider_unavailable`؛ **200 بجسم بلا `choices` أو بـ`finish_reason`/رسالة رفض رصيد** ⇒ `quota_exceeded` (درس D-01 من `general_ai_core`). إعادة المحاولة الواحدة تبقى **فقط** حين `retryable`.
- `content/router.py` خريطة HTTP واحدة: `rate_limited`⇒429 (+`Retry-After`)، `quota_exceeded`⇒429 `ai_quota_exceeded`، `invalid_credential`/`auth_expired`⇒503 `ai_provider_misconfigured` (مفتاح المنصة — ليس خطأ المستخدم)، `content_rejected`⇒422 `ai_content_rejected`، الباقي ⇒ 502 `ai_upstream_unavailable`.
- `provenance` يحمل `credential_mode` (خاص).
- **No-Bypass:** `pyproject.toml`: أضف `"TID"` إلى `select`؛ `[tool.ruff.lint.flake8-tidy-imports.banned-api]` يمنع `httpx`, `openai`, `anthropic`, `litellm`, `google.generativeai` بالرسالة «Use AIGateway (D-068)»؛ `per-file-ignores` `TID251` لـ`src/modules/ai_gateway/**`, `src/modules/social_connect/meta_client.py`, `src/core/**`, `tests/**`. اختبار **`B0-U1`** `test_no_bypass_imports` يمسح `src/` بـ`ast` ويؤكد القائمة نفسها.
- **`B0-U2`** `test_contract_freeze`: القائمة الـ12 وقيم `RETRYABLE_DEFAULT` مطابقة لثابت مكتوب حرفياً في الاختبار (أي تعديل يفشل الاختبار عمداً). **`B0-U3`** (parametrize ≥ 9 أشكال استجابة عبر `respx`): كل شكل ⇒ الفئة المتوقعة وقيمة `retryable`؛ يشمل الـ200 الرافض. **`B0-U4`**: `provider_code` لا يحوي نص رسالة المزوّد (رسالة تحوي `sk-abc…` ⇒ غائب).

---

### Part B-1 — هجرة `0006_publishing` + دورة الحياة الموسَّعة + مستودع المهام

#### B1. مخطط البيانات (صعوداً ونزولاً، مُختبَرة على PG)
```
content_items.status CHECK += 'published', 'publish_failed'      -- ALTER CHECK
content_items.published_at TIMESTAMPTZ NULL                       -- CHECK: (status='published') = (published_at IS NOT NULL)
approval_events.action CHECK += 'published', 'publish_failed', 'publish_cancelled'

publish_jobs
  id UUID PK
  item_id UUID NOT NULL → content_items(id) ON DELETE CASCADE
  revision_id UUID NOT NULL → content_revisions(id)             -- المراجعة المعتمدة لحظة الجدولة (الدليل)
  brand_id UUID NOT NULL → brands(id) ON DELETE CASCADE
  channel VARCHAR(16) NOT NULL
  target_page_id VARCHAR(64) NOT NULL
  run_at TIMESTAMPTZ NOT NULL
  status VARCHAR(16) NOT NULL CHECK (status IN ('queued','leased','succeeded','failed','cancelled'))
  attempt_count INT NOT NULL DEFAULT 0, max_attempts INT NOT NULL DEFAULT 3
  leased_until TIMESTAMPTZ NULL, lease_owner VARCHAR(64) NULL
  next_attempt_at TIMESTAMPTZ NULL
  external_post_id VARCHAR(128) NULL, external_permalink TEXT NULL
  last_error_class VARCHAR(32) NULL
  created_at / updated_at TIMESTAMPTZ NOT NULL
  UNIQUE INDEX (item_id) WHERE status IN ('queued','leased')     -- مهمة نشطة واحدة لكل عنصر
  INDEX (status, run_at), INDEX (status, next_attempt_at)

publish_attempts
  id UUID PK
  job_id UUID NOT NULL → publish_jobs(id) ON DELETE CASCADE
  attempt_no INT NOT NULL, UNIQUE (job_id, attempt_no)
  started_at TIMESTAMPTZ NOT NULL
  finished_at TIMESTAMPTZ NULL
  outcome VARCHAR(20) NULL CHECK (outcome IN ('succeeded','retryable_failure','permanent_failure','ambiguous'))
  error_class VARCHAR(32) NULL                                   -- فئة داخلية (لا نص Meta)
  graph_error_code INT NULL, graph_error_subcode INT NULL, graph_trace_id VARCHAR(64) NULL
  external_post_id VARCHAR(128) NULL                             -- أو creation_id لحاوية IG
```
**`B1-M1`**: `upgrade head ⇒ downgrade 0005 ⇒ upgrade head` + إدخالات حدّية (status جديد مقبول، `published` بلا `published_at` ⇒ CHECK، مهمتان نشطتان لعنصر ⇒ فهرس فريد جزئي، `outcome='maybe'` ⇒ CHECK).

#### B2. دورة الحياة الموسَّعة
- `ContentStatus` += `PUBLISHED`, `PUBLISH_FAILED`. مصفوفة المستخدم تصبح **8 حالات × 7 أفعال = 56 خلية** صريحة: `published` ⇒ `archive` فقط؛ `publish_failed` ⇒ `unschedule → approved` (لإعادة الاستهداف/الموعد) و`archive`؛ الباقي `None`. حارس `assert len == 56`.
- انتقالان **نظاميان** خارج `Action` (ليسا أفعال مستخدم؛ لا يظهران في المصفوفة): `scheduled → published` و`scheduled → publish_failed`، يُنفَّذان بـCAS من العامل فقط (`SYSTEM_TRANSITIONS` في `lifecycle.py` بقائمة مغلقة من اثنتين).
- `schedule` (امتداد B2-U4 السابق) يتحقق إضافياً: (أ) النطاق المطلوب: `facebook_page` ⇒ `pages_manage_posts` ∈ `granted_scopes` **و**`scopes_verified`؛ `instagram` ⇒ `instagram_content_publish` كذلك؛ وإلا 409 `{"error":"reauth_required","required_action":"reconnect","missing_scopes":[...]}`؛ (ب) `instagram` يتطلب `media_refs[0].url` بـ`https` وامتداد صورة (`jpg|jpeg|png`) وإلا 422 `instagram_requires_image`؛ (ج) ينشئ `publish_jobs` (`queued`, `run_at=scheduled_for`, `revision_id=current_revision_id`) في **المعاملة نفسها** مع CAS الحالة.
- `unschedule`/`archive` من `scheduled`: CAS على المهمة `queued → cancelled` في المعاملة نفسها؛ إن كانت `leased` ⇒ 409 `publish_in_progress` (لا تُلمس الحالة). حدث `publish_cancelled`.
- **`B2-U1`**: 56 خلية مسماة (parametrize) + حارس. **`B2-U2`**: نطاق ناقص ⇒ 409 بالقائمة؛ `scopes_verified=false` مع النطاق موجوداً ⇒ 409 أيضاً (مجهول ≠ مؤكَّد). **`B2-U3`**: إنستغرام بلا صورة ⇒ 422؛ برابط `http` ⇒ 422. **`B2-I1`** على PG: `schedule` ينشئ المهمة والحالة ذرّياً (فشل الإدراج ⇒ لا تغيير حالة). **`B2-I2`**: `unschedule` على مهمة `leased` ⇒ 409 والحالة والمهمة بلا تغيير.

#### B3. إعدادات
`META_OAUTH_SCOPES` الافتراضي يصبح `pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish` (قابل للتضييق بالبيئة). `SCHEDULER_ENABLED: bool = False`، `SCHEDULER_POLL_SECONDS: int = 15`، `SCHEDULER_LEASE_SECONDS: int = 120`، `SCHEDULER_BATCH_SIZE: int = 10`، `PUBLISH_RETRY_BACKOFF_SECONDS: tuple[int, ...] = (60, 300, 900)`. **`B3-U1`**: الاتصالات القديمة (بلا نطاقي النشر) تظلّ تعمل للقراءة؛ `schedule` وحده يطلب إعادة الربط.

---

### Part B-2 — الناشر والعامل وAPI

#### B4. `meta_client.py` — نقاط النشر (Bearer توكن الصفحة + `appsecret_proof`؛ جسم POST فقط)
- `publish_page_post(page_token, page_id, message, link: str | None) -> MetaPublishResult(post_id, permalink | None)` ⇒ `POST /{page_id}/feed` بـ`message` (+`link`). ثم `GET /{post_id}?fields=permalink_url` (best-effort؛ فشله لا يُفشل النشر).
- `create_instagram_container(page_token, ig_user_id, image_url, caption) -> str` ⇒ `POST /{ig_user_id}/media {image_url, caption}` ⇒ `creation_id`؛ `publish_instagram_container(page_token, ig_user_id, creation_id) -> MetaPublishResult` ⇒ `POST /{ig_user_id}/media_publish {creation_id}` ⇒ `id`؛ ثم `GET /{id}?fields=permalink`. الناشر (B5) يحفظ `creation_id` في `publish_attempts.external_post_id` بعد الخطوة الأولى؛ عند إعادة المحاولة يعيد **`media_publish` فقط** بنفس `creation_id` (لا حاوية جديدة).
- تصنيف أخطاء Graph إلى `error_class` داخلي: 190/102 ⇒ `token_invalid` (دائم + `mark_out_of_band(reauth_required)`)؛ 200–299/10 ⇒ `permission_denied` (دائم)؛ 4/17/32/613 ⇒ `rate_limited` (قابل للإعادة)؛ 1/2 و5xx وشبكة ⇒ `transient` (قابل للإعادة)؛ 100 ⇒ `invalid_request` (دائم)؛ غيره ⇒ `unknown` (دائم).
- **`B4-U1`** التقاط الطلبات: `POST /{page_id}/feed` بجسم `message`، Bearer، `appsecret_proof` صحيح، لا توكن في الرابط/السجل. **`B4-U2`**: مسار إنستغرام بخطوتين. **`B4-U3`** (parametrize): خريطة الأخطاء الست.

#### B5. `Publisher` Protocol + `MetaPublisher`
- وحدة جديدة `src/modules/publishing/`: `domain/models.py` (`PublishJob`, `PublishAttempt`, `PublishOutcome(kind, error_class, graph_error_code, graph_error_subcode, graph_trace_id, external_post_id, permalink)`)، `domain/publisher.py` (`Publisher` Protocol: `publish(job, revision, page_token_provider) -> PublishOutcome`)، `domain/repository.py` (Protocol)، `infrastructure/meta_publisher.py`، `infrastructure/postgres_repository.py`، `scheduler.py`، `router.py`، + `FakePublisher` في الاختبارات يعدّ النداءات لكل `job_id`.
- `MetaPublisher.publish`: يحلّ الصفحة المختارة بتوكن عبر مستودع `social_connect` (اتصال `active` فقط؛ غيره ⇒ `permanent_failure(error_class=connection_unavailable)`)؛ يفكّ التوكن؛ ينشر حسب القناة (IG: إن كانت المحاولة السابقة تركت `creation_id` ⇒ `media_publish` فقط)؛ `del page_token` في `finally`.

#### B6. `Scheduler` Protocol + `PostgresScheduler`
- `run_once(now) -> int` (عدد المهام المعالَجة) — **قابل للاختبار بلا حلقة**: (1) **استرداد الإيجارات المنتهية** أولاً: `leased` و`leased_until < now` ⇒ إن كان لها محاولة بـ`outcome IS NULL` ⇒ تلك المحاولة `ambiguous` والمهمة `failed(last_error_class=ambiguous_outcome)` + العنصر CAS `scheduled → publish_failed` + حدث `publish_failed` (2-0-ج)؛ وإلا تعود `queued`. (2) معاملة إيجار: `SELECT … FROM publish_jobs WHERE status='queued' AND run_at <= :now AND (next_attempt_at IS NULL OR next_attempt_at <= :now) ORDER BY run_at LIMIT :batch FOR UPDATE SKIP LOCKED` ⇒ `status='leased', leased_until=now+lease, lease_owner=<hostname:pid>, attempt_count+=1` ⇒ commit. (3) لكل مهمة: إدراج `publish_attempts(outcome=NULL)` + commit ⇒ نداء `Publisher` ⇒ معاملة النتيجة: `succeeded` ⇒ المهمة `succeeded` + العنصر CAS `scheduled → published` (+`published_at`) + حدث `published`؛ `retryable_failure` و`attempt_count < max_attempts` ⇒ `queued` + `next_attempt_at = now + backoff[attempt_count-1]`؛ `retryable_failure` مستنفَد أو `permanent_failure` ⇒ المهمة `failed` + العنصر CAS `scheduled → publish_failed` + حدث `publish_failed`.
- `lifespan` في `main.py`: إن `SCHEDULER_ENABLED` ⇒ `asyncio.create_task(loop)` يستدعي `run_once` كل `SCHEDULER_POLL_SECONDS` ويُلغى عند الإغلاق؛ أي استثناء غير متوقع في الدورة يُسجَّل `ERROR` + `counters["scheduler_cycle_errors"]` ولا يقتل الحلقة. عدّادات: `publish_succeeded`, `publish_failed`, `publish_retried`. سجل INFO بلا نص منشور: `publish: job= item= brand= channel= outcome= attempt=`.
- **`B6-I1`** على PG: مهمة مستحقة + `FakePublisher` ناجح ⇒ `succeeded`، العنصر `published` بـ`published_at`، حدث `published`، `external_permalink` مخزَّن. **`B6-I2`**: فشل قابل للإعادة ×3 ⇒ `next_attempt_at` بالتراجع 60/300 ثم `failed` + `publish_failed` + 3 صفوف محاولات. **`B6-I3`** (الأهم): عاملان (`run_once` متزامنان بمحركين، `asyncio.Barrier`) على 5 مهام مستحقة ⇒ كل مهمة نُشرت **مرة واحدة بالضبط**. **`B6-I4`**: إيجار منتهٍ بمحاولة بلا نتيجة ⇒ `ambiguous` + `failed` + العنصر `publish_failed` + **لا نداء ناشر**. **`B6-I5`**: `token_invalid` ⇒ `permanent_failure` + حالة الاتصال `reauth_required` (عبر `StatusMarker` خارج المعاملة — درس 7). **`B6-U1`**: حلقة `lifespan` لا تبدأ حين `SCHEDULER_ENABLED=false` (الافتراضي في الاختبارات).

#### B7. API
- `GET /api/v1/brands/{brand_id}/content/items/{item_id}/publish` ⇒ `PublishStatusPublic` (المهمة الحالية/الأخيرة + `attempts[]` حسب جدول الرؤية 2-0-ب: VIEWER يرى `attempts[].attempt_no/started_at/finished_at/outcome` فقط — نموذجان كما في A-4). 404 `no_publish_job` إن لم تُجدول قط.
- `POST …/items/{item_id}/publish/retry` (OWNER/MANAGER): على `publish_failed` بسبب **غير مبهم** ⇒ CAS `publish_failed → scheduled` + مهمة جديدة `queued` بـ`run_at=now` (+ حدث `scheduled`) — يعيد التحقق من النطاق/الاتصال كما `schedule`. على `ambiguous_outcome` ⇒ 409 `manual_verification_required` (المالك يتحقق من Meta ثم `unschedule`/`archive`).
- **`B7-U1`**: جدول الرؤية للمحاولات (VIEWER بلا `error_class`/`graph_*`). **`B7-U2`**: retry على مبهم ⇒ 409. **`B7-U3`**: المسارات المحمية 30 ⇒ **32** في `test_auth_guard.py`؛ OpenAPI content+publishing = 14 مساراً.

---

## 3. معايير القبول

- [ ] CI خضراء (الفحوص الخمسة) على الـPR؛ `ruff` بقاعدة `banned-api` مفعَّلة.
- [ ] 0 فشل؛ تغطية ≥ 95% (لا تراجع عن 99%)؛ `ruff check` + `ruff format --check` نظيفان.
- [ ] `grep -rn "response_model_exclude_none\|next_revision_no\|AIRateLimitedError\|AIUpstreamError" src` ⇒ فارغ؛ `grep -rln "import httpx" src | sort` ⇒ ملفات `ai_gateway/` و`meta_client.py` (و`core/` إن لزم) فقط.
- [ ] `alembic/versions/` = 0001–**0006**؛ `0006` تمرّ صعوداً ونزولاً على PG (**`B1-M1`**).
- [ ] الاختبارات بالأسماء: `A1-U1..U2`, `A2-U1..U2`, `A3-I1`, `A4-U1`, `A4-I1`, `A5-U1`, `A5-I1`, `A6-U1`, `B0-U1..U4`, `B1-M1`, `B2-U1..U3`, `B2-I1..I2`, `B3-U1`, `B4-U1..U3`, `B6-U1`, `B6-I1..I5`, `B7-U1..U3` — موجودة وناجحة على PG حقيقية.
- [ ] `B2-U1` يغطي **56** خلية مسماة `<state>--<action>`؛ `B0-U2` تجميد الفئات الـ12.
- [ ] `B6-I3` حتمي 3/3 (exactly-once تحت عاملين).
- [ ] لا تبعية جديدة في `pyproject.toml` (تغيير إعدادات ruff فقط)؛ أربعة كوميتات فوق `main`؛ لا ملف محظور مُسّ.
- [ ] §6 مملوء بالسطر؛ `WORK_STATE.md` §1 ⇒ `SPRINT_07_DELIVERED_AWAITING_REVIEW`.

---

## 4. أوامر

```bash
git checkout work/implementation && git fetch origin && git merge --ff-only origin/main
pip install -e ".[dev]"
export TEST_DATABASE_URL=postgresql+asyncpg://aigos@localhost:5433/ai_growth_os_test
export AUTH_JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
ruff check src tests alembic scripts && ruff format --check src tests alembic scripts
pytest --cov=src --cov-fail-under=95
grep -rn "response_model_exclude_none\|next_revision_no\|AIRateLimitedError\|AIUpstreamError" src
grep -rln "import httpx" src | sort
ls alembic/versions/
```

---

## 5. التزامات المنفذ

1. **Part A** كوميت: `fix(content): SPRINT-07 Part A — public/private provenance (A-1), channel limits in service (A-2), revision numbering under row lock (A-3), no N+1 + role response models (A-4), daily token budget (A-5), prompt_version (A-6)` ⇒ ادفع ⇒ افتح الـPR.
2. **Part B-0** كوميت: `refactor(ai_gateway): SPRINT-07 Part B-0 — provider contract (12 error categories, usage, credential_mode) + no-bypass lint rule (D-067/D-068)` ⇒ ادفع.
3. **Part B-1** كوميت: `feat(publishing): SPRINT-07 Part B-1 — migration 0006, published/publish_failed states (56-cell matrix), publish_jobs/attempts, schedule creates job atomically, publishing scopes` ⇒ ادفع.
4. **Part B-2** كوميت: `feat(publishing): SPRINT-07 Part B-2 — MetaPublisher (feed + IG two-step), PostgresScheduler (SKIP LOCKED lease, backoff, ambiguous outcome), publish status API` ⇒ ادفع.
5. **ادفع بعد كل ملف** (`wip:` ثم سحق إلى الكوميتات الأربعة قبل التسليم).
6. املأ §6 بالسطر؛ `WORK_STATE.md` (§1 ⇒ `SPRINT_07_DELIVERED_AWAITING_REVIEW`؛ سطر جديد أعلى §6). عند التسليم اكتب حرفياً: **«PR #N جاهز لمراجعة المخطط»**. **لا تدمج** (D-063).
7. أي بند متعذر ⇒ وثّق الانحراف بالسطر والسبب؛ **لا** تُضعف: جدول الرؤية، «الموافقة للمالك فقط»، قاعدة «لا تكرار صامت» (2-0-ج)، أو قاعدة No-Bypass — هذه قرارات مالك/معمارية.
8. **اختبار يدوي اختياري** إن توفّر للمالك تطبيق Meta في وضع التطوير وصفحة يملكها: المدراء/المطوّرون/المختبرون لا يحتاجون App Review لنطاقي النشر. لا تُسجَّل أي توكنات في التقرير.

---

## 6. تقرير المنفذ (يملؤه المنفذ)

- **حالة التنفيذ:** `[x] مكتمل` / `[ ] جزئي — السبب:` —
- **Commit SHA Part A:** `00c288c` — **B-0:** `b3544a7` — **B-1:** `9234831` — **B-2:** `364de2b` — **docs:** `1489448`→(amended) الكوميت الأعلى في PR #22
- **PR:** [#22](https://github.com/belalalibb/ai_agancy_markting/pull/22) (`work/implementation` ⇒ `main` @ `95a14f8`) — **CI:** أول تشغيل كشف فجوة: `unit` (`-m "not integration"`) 94.26% < 95 لأن `scheduler.py` كان مغطّى بتكامل PG فقط ⇒ أُضيفت `tests/modules/publishing/test_scheduler_unit.py` (14 اختباراً ضد الـfakes عبر مصانع مستودعات قابلة للحقن) ⇒ unit محلياً **96.08%** (740 ناجحاً)؛ الكل **98%+**. الفحوص الخمسة تُتحقَّق على الكوميت الأعلى.
- **الاختبارات:** وحدة **740** / تكامل **52** (45 + 7 في `tests/integration/test_scheduler_pg.py`) / الكل **792 ناجحاً، 0 فشل** على PG 17 حقيقية (Python 3.13 محلياً) — **التغطية:** **98.21%** (البوابة 95 ✓؛ أدنى ملف `publishing/infrastructure/postgres_repository.py` 76%).
- **A-1..A-6:** **A-1** `content/domain/models.py` `public_provenance()` (kind/provider/model/generated_at للجميع؛ `prompt_hash`/`usage` في `generation_log` للمالك/المدير فقط) + `schemas.py:ContentRevisionPublic.from_domain` — A1-U1/U2. **A-2** `service.py` `CHANNEL_LIMITS` ⇒ `BodyTooLongError` 422 `body_too_long{channel,limit,length}` من الخدمة — A2-U1/U2. **A-3** `domain/repository.py:get_for_update` (`SELECT … FOR UPDATE`) + `add_revision` يرقّم داخل القفل؛ الدالة القديمة حُذفت — A3-I1 على PG (5 طلبات متزامنة ⇒ 5 مراجعات متتالية، لا 500). **A-4** `postgres_repository.py:list_items` JOIN واحد على `current_revision_id` + نموذجان `ContentItemDetail`/`ContentItemDetailWithUsage` بدل `exclude_none` — A4-U1/A4-I1. **A-5** `ai_token_usage_since`/`oldest_ai_generation_at` ⇒ `GenerationBudgetExceededError` 429 + `Retry-After` (`AI_DAILY_TOKEN_BUDGET_PER_BRAND`) — A5-U1/I1. **A-6** `prompts.py:PROMPT_VERSION` داخل `provenance` — A6-U1.
- **B-0:** `ai_gateway/contract.py`: `ErrorCategory` 12 فئة مجمَّدة (B0-U2)، `Usage`, `credential_mode ∈ {platform, byok}`؛ `facades/openai_compatible.py` مصنِّف واحد؛ ruff `TID251` banned-api لـ`httpx/openai/anthropic/litellm/google.generativeai` خارج `ai_gateway/**`, `meta_client.py`, `core/**` (`pyproject.toml` — التغيير الوحيد) + `test_no_bypass` (B0-U4). **جدول B0-U3** (شكل ⇒ فئة ⇒ HTTP في `content/router.py:_AI_HTTP`): `401/403 invalid_api_key` ⇒ `invalid_credential` ⇒ 503 `ai_provider_misconfigured`؛ `401 expired` ⇒ `auth_expired` ⇒ 503؛ `429 rate_limit` ⇒ `rate_limited` ⇒ 429 + Retry-After؛ `429 insufficient_quota` ⇒ `quota_exceeded` ⇒ 429 `ai_quota_exceeded`؛ `400 content_policy` ⇒ `content_rejected` ⇒ 422؛ `400 context_length` ⇒ `context_too_long` ⇒ 502؛ `404 model` ⇒ `model_unavailable` ⇒ 502؛ `400 invalid_request` ⇒ 502؛ `5xx` ⇒ `upstream_unavailable` ⇒ 502؛ timeout ⇒ `timeout` ⇒ 502؛ `ConnectError` ⇒ `network` ⇒ 502؛ غير مفهوم ⇒ `unknown` ⇒ 502 — كل ما عدا الخمسة الأولى 502 ثابت بلا نص المزوّد.
- **B1/B2/B3:** **B1** `alembic/versions/0006_publishing.py`: `ck_content_items_status` بـ8 حالات؛ `published_at` + `ck_content_items_published_at_iff_published`؛ `approval_events.action` **VARCHAR(16)⇒(32)** مع تخفيض متماثل (ملاحظة المالك ✓)؛ `ck_approval_events_action` بالأفعال **الثمانية** (`submitted, approved, rejected, withdrawn, scheduled, published, publish_failed, publish_cancelled`)؛ **الفاعل** `actor_tenant_id/actor_user_id` nullable + `ck_approval_events_actor_required` (NULL **فقط** لـ`published`/`publish_failed`؛ هوية من أذن بالنشر في `publish_jobs.scheduled_by_tenant_id/user_id` — D-055؛ ملاحظة المالك ✓)؛ `publish_jobs` (CHECK status/`attempt_count<=max_attempts`، فهرس جزئي `uq_publish_jobs_active_item` على `status IN (queued, leased)`، فهرس الاستحقاق على `run_at WHERE status='queued'`) + `publish_attempts` (`uq_publish_attempts_job_no`, CHECK outcome). **التخفيض:** إسقاط CHECK `published_at` ⇒ `UPDATE … 'archived' WHERE status NOT IN (OLD)` ⇒ تضييق `action` إلى 16 (ترتيب صحَّحه B1-M1 — انحراف 1). `lifecycle.py`: 8×7 = **56** خلية؛ `SYSTEM_TRANSITIONS={(scheduled,published),(scheduled,publish_failed)}` خارج `transition()`؛ `EDIT_FORBIDDEN={archived, published}`؛ `EDIT_RESETS_TO_DRAFT` تضم `publish_failed`؛ `published ⇒ archive` فقط؛ `publish_failed ⇒ unschedule(⇒approved) | archive`. B1-M1 على PG: head⇒0005⇒head + حواف (published بلا `published_at` ⇒ CHECK؛ مهمتان نشطتان ⇒ فهرس جزئي؛ `outcome='maybe'` ⇒ CHECK؛ `action` 32 حرفاً ✓؛ `published` بلا فاعل ✓ و`approved` بلا فاعل ✗). **B2** `service.py:act(SCHEDULE)`: دور/حالة ⇒ `_validate_schedule` (`>now+5m`؛ صفحة مختارة بتوكن في اتصال `active`؛ IG مرتبط؛ **نطاقا النشر مُتحقَّق منهما** وإلا 409 `reauth_required{required_action=reconnect, missing_scopes}`؛ **IG يتطلب `media_refs[0].url` https `.jpg/.jpeg/.png`** وإلا 422 `instagram_requires_image`) ⇒ CAS ⇒ `create_job` **في نفس المعاملة** (`run_at=scheduled_for`, `revision_id=current`)؛ `IntegrityError(uq_publish_jobs_active_item)` ⇒ `ActiveJobExistsError` ⇒ 409 `publish_in_progress` ولا شيء يُكتب (B2-I1 على PG). `unschedule/archive/edit` من `scheduled`: مهمة `queued` ⇒ `cancelled` + حدث `publish_cancelled`؛ مهمة `leased` ⇒ **409 `publish_in_progress` بلا أي تغيير** (B2-I2 على PG؛ قاعدة 2-0-ج). `MediaRef{url, kind=image}` (عنصر واحد كحد أقصى). B2-U1 (56 خلية `<state>--<action>` + حارس `56 == len(ContentStatus)*len(Action)`)، B2-U2، B2-U3 (بلا وسائط/http/gif/mp4 ⇒ 422؛ jpg/png مع query ✓). **B3** `config.py` `META_OAUTH_SCOPES` الافتراضي = الخمسة؛ `REQUIRED_PUBLISH_SCOPES` لكل قناة؛ اتصال قديم بلا نطاقي النشر يقرأ كالمعتاد ويُرفض عند `schedule` فقط (B3-U1).
- **B4/B5/B6/B7:** **B4** `social_connect/meta_client.py:350-420` `publish_page_post` (`POST /{page_id}/feed` form `{message[, link]}` ⇒ `GET /{id}?fields=permalink_url` بأفضل جهد) / `create_instagram_container` (`POST /{ig}/media` ⇒ `creation_id`) / `publish_instagram_container` (`POST /{ig}/media_publish` ⇒ `GET /{id}?fields=permalink`)؛ Bearer + `appsecret_proof` فقط؛ `MetaPublishResult{post_id, permalink|None}`. **خريطة الأخطاء** `publishing/infrastructure/meta_publisher.py:classify_graph_error` — الكود أولاً: 190/102 ⇒ `token_invalid`؛ 10/200–299 ⇒ `permission_denied`؛ 4/17/32/613 ⇒ `rate_limited`؛ 1/2 أو 5xx أو شبكة ⇒ `transient`؛ 100 ⇒ `invalid_request`؛ **أي كود آخر ⇒ `unknown`**؛ بلا كود ⇒ حسب نوع الاستثناء. B4-U1 (form body، Bearer، proof مُعاد حسابه ✓، لا توكن في الرابط/السجل؛ فشل permalink لا يُفشل النشر)، B4-U2، B4-U3 (16 حالة + 4 fallback) — `tests/modules/publishing/test_meta_publish_client.py` (27). **B5** `publishing/domain/publisher.py`: `Publisher.publish(job, revision, page_token_provider, *, last_creation_id) -> PublishOutcome` + `PageTokenProvider.resolve -> ResolvedPageTarget|None` (`page_token` بـ`repr=False`)؛ `meta_publisher.py`: `PostgresPageTokenProvider` (صفحة **مختارة** بتوكن في اتصال **`active`** فقط) + `MetaPublisher` (`None` ⇒ `permanent_failure(connection_unavailable)` بلا Graph؛ IG: `last_creation_id` ⇒ `media_publish` فقط؛ فشل الخطوة 2 يُعيد `external_post_id=creation_id`؛ `token_invalid` ⇒ `connection_id_to_mark`؛ `del page_token` في `finally`) — `test_meta_publisher.py` (13). **B6** `publishing/scheduler.py` `PostgresScheduler.run_once(now)` بثلاث معاملات مستقلة: (1) استرداد الإيجارات المنتهية — محاولة مفتوحة ⇒ `ambiguous` + `failed(ambiguous_outcome)` + CAS `→publish_failed` + حدث **بلا نداء ناشر**؛ بلا محاولة ⇒ `requeue` (المحاولة الضائعة تُحتسب)؛ (2) `FOR UPDATE SKIP LOCKED` بـ`lease_owner=hostname:pid`, `attempt_count+=1`؛ (3) `start_attempt(outcome NULL)`+commit ⇒ الناشر بلا جلسة ⇒ معاملة النتيجة: نجاح ⇒ `succeeded` + CAS `→published(+published_at)` + حدث `published`؛ قابل للتكرار و`attempt_count<max` ⇒ `queued` بـ`next_attempt_at=now+backoff[attempt_count-1]` (60/300/900)؛ وإلا `failed` + CAS `→publish_failed` + حدث؛ ثم `mark_out_of_band(reauth_required)` **خارج** المعاملة. `main.py:lifespan` ⇒ `create_task(scheduler_loop)` عند `SCHEDULER_ENABLED` فقط (افتراضي false)، يُلغى عند الإيقاف؛ استثناء الدورة ⇒ ERROR + `counters[scheduler_cycle_errors]`؛ عدّادات `publish_succeeded/publish_failed/publish_retried`؛ INFO `publish: job= item= brand= channel= outcome= attempt=` بلا نص. B6-U1 (`test_main.py`). **B6-I1..I5 على PG** (`tests/integration/test_scheduler_pg.py`): I1 نشر مرة واحدة + محاولة + حالة + حدث نظامي بلا فاعل + `published_at`؛ المستقبلية لا تُمس؛ دورة ثانية ⇒ 0. I2 `rate_limited` ⇒ `queued` +60 ⇒ لا شيء قبل الموعد ⇒ محاولة 2 ⇒ +300 ⇒ محاولة 3 `permission_denied` ⇒ `failed` + `publish_failed` + حدث (+ `max_attempts=1` مع فئة قابلة للتكرار ⇒ نهائي). **I3 عاملان على محرّكين مستقلين و`asyncio.Barrier` و5 مهام (`batch=3`) ⇒ المجموع 5، كل مهمة مرة واحدة بالضبط، 5 `published`، لا `lease_owner` متبقٍ — حتمي 3/3 + في الحزمة الكاملة.** I4 إيجار منتهٍ بمحاولة مفتوحة ⇒ `ambiguous` بلا نداء + إيجار منتهٍ بلا محاولة ⇒ `requeue` ونُشر بالدورة نفسها بـ`attempt_no=2`. I5 `token_invalid` ⇒ `failed` من المحاولة الأولى + اتصال `reauth_required` عبر `PostgresStatusMarker` + `resolve ⇒ None` بعدها (+ IG: `creation_id` من المحاولة الفاشلة يصل الناشر في الإعادة). **B7** `content/router.py` `GET …/items/{id}/publish` ⇒ `PublishStatusDetail` (OWNER/MANAGER) / `PublishStatusPublic` (VIEWER: محاولات `attempt_no/started_at/finished_at/outcome` فقط) — نموذجان كـA-4؛ 404 `no_publish_job`. `POST …/publish/retry` (OWNER/MANAGER، `CurrentUserFresh`): `publish_failed` وآخر مهمة ليست `ambiguous_outcome` ⇒ `_validate_target` (فحوص `schedule` عدا الوقت) ⇒ CAS `publish_failed→scheduled` + مهمة `queued` بـ`run_at=now` + حدث `scheduled` بالفاعل (`note=publish_retry`)؛ `ambiguous` ⇒ **409 `manual_verification_required` بلا تغيير**. B7-U1، B7-U2، B7-U3 (`PROTECTED_ROUTE_COUNT` 30⇒**32**؛ OpenAPI content+publishing = **14** عملية/13 مساراً) + تدفق الإعادة وإعادة التحقق من الهدف.
- **القبول §3:** `grep -rn "response_model_exclude_none\|next_revision_no\|AIRateLimitedError\|AIUpstreamError" src` ⇒ **فارغ**؛ `grep -rln "import httpx" src | sort` ⇒ `src/core/http.py`, `src/modules/ai_gateway/dependencies.py`, `src/modules/ai_gateway/facades/openai_compatible.py`, `src/modules/social_connect/meta_client.py`؛ `ls alembic/versions/` ⇒ 0001–**0006**؛ `ruff check src tests alembic` + `ruff format --check` نظيفان (136 ملفاً)؛ **778 ناجحاً / 0 فشل / 98.21%**؛ الاختبارات بالأسماء كلها موجودة وناجحة على PG (A1-U1..U2, A2-U1..U2, A3-I1, A4-U1, A4-I1, A5-U1, A5-I1, A6-U1, B0-U1..U4, B1-M1, B2-U1..U3, B2-I1..I2, B3-U1, B4-U1..U3, B6-U1, B6-I1..I5, B7-U1..U3)؛ B2-U1 = 56 خلية؛ **B6-I3 3/3**؛ `pyproject.toml` تغيير إعدادات ruff فقط (لا تبعية)؛ أربعة كوميتات فوق `main` (`00c288c`, `b3544a7`, `9234831`, `364de2b`) + كوميتات التوثيق؛ `git diff 95a14f8 --name-only` خالٍ من الملفات المحظورة.
- **انحرافات (بالسطر والسبب):**
  1. `alembic/versions/0006_publishing.py:downgrade` — **خطأ حقيقي اكتشفه B1-M1:** `UPDATE … status='archived'` كان قبل إسقاط `ck_content_items_published_at_iff_published` فيفشل التخفيض على صف `published`. أُعيد الترتيب: إسقاط CHECK ⇒ UPDATE ⇒ تضييق `action`.
  2. `publishing/infrastructure/meta_publisher.py:classify_graph_error` — كود Graph غير مُدرج بنوع `OAuthException` (مثل IG 9007) ⇒ `unknown` لا `token_invalid`؛ لأن `token_invalid` تقلب الاتصال إلى `reauth_required` (B6-I5). التزام بـ«else ⇒ unknown» مُثبَّت باختبار.
  3. `publishing/scheduler.py:_process` — `next_attempt_at = now(الدورة) + backoff` حرفياً كما في B6 (لا `finished_at`)؛ اكتشفه B6-I2 بقفزات زمن مُحقَّنة.
  4. `content/service.py:retry_publish` — إعادة التحقق عبر `_validate_target` المُستخلَص من `_validate_schedule` بلا قاعدة `>now+5m` لأن الإعادة فورية.
  5. `tests/core/test_config.py::test_default_scopes_are_read_only` ⇒ `…include_publishing_scopes` — افتراض SPRINT-03 تجاوزه B3 صراحةً.
  6. B7-U3 «14 مساراً» فُسِّرت **عمليات** (13 مساراً؛ `/items` GET+POST)؛ الاختبار يثبّت 14 عملية ومجموعة المسارات الـ13 حرفياً.
  7. `PublishStatusDetail` يُظهر `graph_error_code/subcode/trace_id` للمالك/المدير (أرقام ومعرّف تتبّع؛ **لا نص Meta** في أي نموذج/سجل) — لتمكين الدعم ضمن D-064.
  8. لا `publishing/router.py` منفصل: المساران في `content/router.py` لأنهما تحت `/content/items/{id}` ويشاركان `_authorise`/`to_http`/`Service`؛ منطق الوحدة كله في `publishing/`.
  9. `MetaPublisher` في `lifespan` يُبنى بـ`MetaGraphClient(settings, app.state.http_client)` (العميل المشترك D-068)؛ `encryption=None` إن غاب `TOKEN_ENCRYPTION_KEY` ⇒ `connection_unavailable` بدل انهيار الحلقة.
  10. ساندبوكس أُعيد **12 مرة** خلال SPRINT-07؛ كل `wip:` سُحق في الكوميتات الأربعة (`reset --soft` + `push -f`)؛ §6 كُتب على ثلاثة كوميتات لتجاوز الإعادات.
  11. `scripts/` غير موجود في الشجرة؛ أمر ruff في §4 نُفِّذ على `src tests alembic`.
  12. `publishing/scheduler.py:PostgresScheduler.__init__` يقبل `jobs_repo/content_repo/connections_repo` (مصانع تأخذ الجلسة؛ الافتراضي مستودعات PG) — أُضيف بعد أول تشغيل CI لأن حارس تغطية `unit` يستثني التكامل، فكان المجدول غير مغطّى هناك (94.26%). لا تغيير سلوكي.
- **أسئلة:**
  1. الإعادة بعد نتيجة **مبهمة** محجوبة (409)؛ المخرج الحالي `unschedule ⇒ approved` ثم `schedule`. هل يُراد مسار صريح «تحقّقتُ يدوياً — أعد» (مثل `POST …/publish/retry {"verified_not_live": true}`) في M4 أم يبقى مؤجَّلاً؟
  2. `publish_jobs.max_attempts=3` من الهجرة؛ هل يُفضَّل إعداد `PUBLISH_MAX_ATTEMPTS` أم يبقى عمود المهمة مصدر الحقيقة؟
  3. `scheduler_loop` داخل عملية الـAPI (D-069). نسختان من الـAPI ⇒ حلقتان — آمن بـ`SKIP LOCKED` (B6-I3)؛ هل يُراد `SCHEDULER_ENABLED` على نسخة واحدة فقط في بيئة التشغيل الأولى؟

---

## 7. ما ينتظر المالك

1. 🔑 **تدوير توكن GitHub — الطلب السابع.** التوكن ظهر في المحادثة مجدداً؛ يُعدّ مكشوفاً.
2. 🛡️ حماية `main` بالفحوص الخمسة + منع الدفع المباشر.
3. **قرار مطلوب الآن (M4):** نطاقا النشر `pages_manage_posts` و`instagram_content_publish` يُضافان افتراضياً (B3). للنشر لصفحات **غير** مدراء/مختبري التطبيق يلزم **App Review** من Meta (نموذج الاستخدام + فيديو شاشة) — يبدأ المالك الطلب الآن لأن مدته أسابيع؛ الاختبار الداخلي لا ينتظره.
4. **قرار مطلوب قبل M7:** مزوّد الذكاء الاصطناعي الحقيقي الأول خلف البوابة (OpenAI-compatible؛ الآن `AI_PROVIDER=fake`) — عقد B-0 يجعله تغيير إعدادات + ملف facade.
5. **قرار مطلوب قبل M6:** حساب WhatsApp Business (Cloud API) ورقم اختبار؛ المرجع المرشَّح `PyWa` (`REPOSITORY_EVALUATIONS.md` §5-د).
6. **للعلم (لا قرار):** الرأي المعماري الكامل للمخطط في `AI_PROVIDER_STRATEGY.md` §3–§5 (D-067/D-068/D-069) — البوابة مكتبة داخل الوحدة لا خدمة؛ Temporal مؤجَّل حتى أول مسار طويل؛ حلقة التعلّم من إشارات الموافقة لا النصوص.
