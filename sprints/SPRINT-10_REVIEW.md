# 📋 SPRINT-10-BRAND-BRAIN — تقرير مراجعة المخطط

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-10-BRAND-BRAIN` (المحطة M7 + إغلاق N1–N5 من مراجعة SPRINT-09) |
| **المُراجِع** | AI Master Planner |
| **تاريخ المراجعة** | 2026-11-01 |
| **الفرع المُراجَع** | `work/implementation` @ `f2859d0` (Part A `5e269d3`، B-1 `d075c79`، B-2 `fc54051`، B-3 `e219e8e`، وثائق `f2859d0`) فوق `main` @ `0156371` |
| **Pull Request** | [#32](https://github.com/belalalibb/ai_agancy_markting/pull/32) — 78 ملفاً، +10,927/−354، 5 كوميتات |
| **الحكم النهائي** | 🟢 **مقبول — دُمج بواسطة المخطط (D-063)**. Part A (N1–N5 + A-6) وB-1/B-2/B-3 منفَّذة ومُتحقَّق منها بالسطر وبالتشغيل على PostgreSQL 17 **UTF8** حقيقية (بيئة أُعيد بناؤها من الصفر **ثلاث مرات** خلال هذه المراجعة — إعادات الضبط #19/#20/#21)؛ الهجرات 0010/0011/0012 تذهب وتعود؛ **1207 اختباراً تمرّ / تغطية 98.33%**؛ ruff نظيف؛ لا تبعية جديدة؛ لا ملف محظور مُسّ؛ 8 من 9 انحرافات مقبولة والتاسع (#4) مرفوض جزئياً لأن ادعاءه ثبت خطؤه (§4 B5). في المقابل **أثبت المخطط بالتجربة على PG الملاحظات الست كلها** التي أرسلها المالك (§4: B1–B6) — اثنتان منها أمنيتان (B2-أ SSRF، B6 تضخيم استعلامات) واثنتان اقتصاديتان (B1 عدّ مضاعف ×8 وعدّ مفاتيح المستخدم على ميزانية المنصة). **لا تمنع الدمج** لأن M7 غير مربوط بمزوّد حي ولا يوجد نشر إنتاجي بعد، لكنها **تُغلق في الجزء A من السبرنت القادم قبل أي تشغيل خارج بيئة المالك** (شرطان صريحان في §5). |

---

## 1. ما تحقق منه المخطط فعلياً (بيئة طازجة ×3؛ PG 17 + pgvector من pgdg؛ `initdb -E UTF8`)

| الفحص | أمر المخطط | النتيجة |
|---|---|---|
| المجموعة الكاملة على PG حقيقية + بوابة التغطية | `pytest -q -p no:cacheprovider --cov=src --cov-fail-under=95` (`TEST_DATABASE_URL` مضبوط) | **1207 passed, 0 failed, 7 warnings** (712.86s) — `Required test coverage of 95% reached. Total coverage: 98.33%` — يطابق تقرير المنفذ (1207 / 98%) |
| ruff | `ruff check . && ruff format --check .` | `All checks passed!` / `290 files already formatted` |
| الهجرات ذهاباً وإياباً | `alembic upgrade head` ⇒ `alembic downgrade 0009` ⇒ `alembic upgrade head` ⇒ `alembic downgrade base` | 12 ترقية ⇒ 3 تنزيلات (0012/0011/0010) ⇒ 3 ترقيات ⇒ `0012 (head)` ⇒ تنزيل كامل حتى `0001 -> ` يترك جدولاً واحداً (`alembic_version`) — **لا بقايا** |
| CI على GitHub | Checks API على `f2859d0` و`b4163c8` | **5/5 success ×2** على كلا الـSHA (lint/unit/integration/secrets/licenses) |
| قابلية الدمج | `GET /pulls/32` | `mergeable: true`، `mergeable_state: clean`، 5 كوميتات، base `main` |
| ملفات محظورة | `git diff --name-only 0156371..f2859d0 -- ARCHITECTURE_LOCK.md SESSION_RECOVERY.md imaster_planner.md README.md AI_Marketing_Platform_Hub sprints` | **(فارغ)** |
| `pyproject.toml` | `git diff 0156371..f2859d0 -- pyproject.toml` | 7 أسطر `banned-api` (bs4/beautifulsoup4/scrapy/playwright/selenium/instaloader/lxml — D-074) + استثناء `TID251` لـ`src/modules/brand_brain/public_fetch.py` فقط — **لا تبعية جديدة** |
| `ai_token_usage` خارج `ai_gateway` | `grep -rln ai_token_usage src \| grep -v ai_gateway` | **(فارغ)** |
| `import httpx` | `grep -rl "^import httpx\|^from httpx" src` | **6 ملفات** (الخمسة المعروفة + `public_fetch.py`) |
| `auto_reply` | `grep -rn auto_reply src` | **(فارغ)** |
| عدّاد المسارات المحمية | `tests/test_auth_guard.py:75`, `tests/modules/content/test_router.py:1417` | `PROTECTED_ROUTE_COUNT == 62` (47 + 15 brain) |
| `len(EXPECTED)` | `tests/modules/content/test_lifecycle.py` | `== 56` بلا تغيير |
| الكوميتات | `git log 0156371..f2859d0` | 4 كوميتات كود (A/B-1/B-2/B-3) + كوميت وثائق — كما أُبلغ |

### 1-أ. التحقق بنداً بنداً (بالسطر @ `f2859d0`)

| البند | الملف:السطر | ما وجده المخطط |
|---|---|---|
| **A-1** (N1, D-072) | `src/core/database.py::independent_session`؛ `whatsapp/infrastructure/postgres_repository.py:614 PgIndependentWrites` | الكتابة المستقلة قبل `send_text`؛ اختبارات `test_a1_i1..i3` على PG تمرّ ضمن الحزمة |
| **A-2** (N2) | `whatsapp/router.py` `POST /account/rotate`؛ `service.py::connect_account ⇒ ConnectOutcome` | إعادة الربط على صف `disabled` + 409 على `active` + التقاط `IntegrityError` |
| **A-3** (N3) | `whatsapp/domain/models.py::DELIVERY_RANK`؛ `update_status_by_wamid(..., brand_id=)` | `WHERE … AND brand_id` + رتابة الرتب |
| **A-4** (N4) | `ai_gateway/budget.py` + `ai_gateway/infrastructure/pg_budget.py:31-59` | قارئ واحد `UNION ALL` (revisions + drafts + **ai_cost_log**) — **انظر B1 أدناه: هذا الاتحاد هو مصدر العدّ المضاعف** |
| **A-5** (N5) | `webhook.py:77-78` `SIGNATURE_PATTERN`, `MAX_BODY_BYTES = 1 MiB`؛ هجرة 0010 | الترتيب: سقف الجسم ⇒ شكل الرأس ⇒ `json.loads` ⇒ استخراج المعرّفات ⇒ DB ⇒ HMAC — **انظر B6** |
| **B-1** | `alembic/versions/0011_brand_brain.py`؛ `brand_brain/{router,service,schemas}.py` | 5 جداول بالقيود المسمّاة؛ `ProfilePublic.from_domain(profile, private=)` يخفي `banned_phrases`/`reply_templates` @`schemas.py:86-87` — لكن `RevisionPublic.from_domain(rev)` @`:105` **بلا** `private` ⇒ **B3** |
| **B-2** | `brand_brain/public_fetch.py:190-279`؛ `domain/hosts.py:34-57`؛ `ai_gateway/cost_logging.py` | المخطط والمضيف والمنفذ والمنصات المحظورة تُفحص قبل الشبكة ✅؛ **لا** فحص لعنوان IP بعد DNS ⇒ **B2-أ**؛ `robots.txt` عبر `_http.get` غير متدفق ثم `text[:65536]` ⇒ **B2-ب**؛ `_get_page` يقبل أي حالة ≠ 3xx ⇒ **B2-ج** |
| **B-3** | `brand_brain/advisor.py` | `_RecommendationOut.kind` @`:103` ثلاث قيم فقط ⇒ **B5**؛ `content_plan` @`:355-430` ينسخ `provenance` بالرموز في كل عنصر ⇒ **B1**؛ `_store_reply_template` @`:502-533` ⇒ **B4-أ**؛ `create_item(body=strip_fake_tags(raw.body))` ⇒ **B4-ب** |

---

## 2. تقييم تقرير المنفذ (§6 @ `f2859d0`)

| # | الانحراف | الحكم | التعليق |
|---|---|---|---|
| 1 | `invalid_output` ليست ضمن الـ12 ⇒ `NON_RETRYABLE_ERROR` + `provider_code="invalid_output"` و502 `ai_invalid_output` في Brain | ✅ مقبول | **خطأ التوجيه لا المنفذ**: نصّ §2 B-2 افترض فئة غير موجودة. القرار في §3-س2 |
| 2 | قائمة المنع في `domain/hosts.py` لا `public_fetch.py` | ✅ مقبول | أفضل: تُستخدم أيضاً عند `POST /competitors` |
| 3 | 15 مساراً (+`GET /recommendations?status=`) | ✅ مقبول | §3-س1 |
| 4 | `content-plan` ⇒ `materialized` منذ الإنشاء؛ «`accept` يبقى لتوصيات `content_plan_item` القادمة من `diagnose`» | ⚠️ **مقبول جزئياً** | النصف الأول صحيح ومنطقي. النصف الثاني **كاذب**: `_RecommendationOut.kind` لا يقبل `content_plan_item` و`accept()` لا يفرّع إلا على `reply_template` — أثبته §4 B5 (502 `ai_invalid_output`). يُصحَّح إمّا بحذف الادعاء وتوثيق أن `content_plan_item` تولد **فقط** من `/content-plan`، أو بإضافة النوع للمخطط وفرع `accept` ينشئ المسودة. **قرار المخطط: الخيار الأول** (أبسط؛ البرومبت `brand_diagnosis_v1` لا يطلب هذا النوع أصلاً) |
| 5 | `create_item` ثم `update_body(provenance=)` | ✅ مقبول | لكن `update_body` يُنشئ مراجعة ثانية تحمل الرموز ⇒ أحد سببَي B1 |
| 6 | `reply_template` accept على علامة بلا ملف ⇒ إنشاء v1 بدل 404 | ✅ مقبول مع تحفّظ | سلوك جيد للمستخدم، لكن `insert_profile` بلا التقاط `IntegrityError` ⇒ B4-أ |
| 7 | PG يجب أن تكون UTF8 | ✅ مقبول | أُضيف إلى إجراء إعادة البناء (`initdb -E UTF8`) — المخطط استخدمه ثلاث مرات |
| 8 | تأكيد حزمة B-1 بعد B-3 | ✅ مقبول | الرأس هو ما يهم؛ تحقق منه المخطط |
| 9 | `test_postgres_repository.py` لتغطية وظيفة `unit` | ✅ مقبول | مبرَّر ببوابة 95 بلا PG |

**تقرير التشغيل الحي:** لم يُنفَّذ (لا مفتاح لدى المنفذ) — مقبول؛ التحقق الحي مسؤولية المالك (§7 من التوجيه).

---

## 3. أجوبة أسئلة المنفذ الثلاثة

1. **`GET /recommendations?status=` يُعتمد** (15 مساراً؛ `PROTECTED_ROUTE_COUNT == 62` هو الأساس الجديد). `DiagnosisPublic` يبقى يعيد توصيات التشخيص الحالي فقط؛ القائمة هي مصدر المعرّفات لـ`accept/dismiss`.
2. **لا.** عقد D-067 يبقى **12 فئة**. `NON_RETRYABLE_ERROR` + `provider_code ∈ {invalid_output, not_object}` في الواجهة، و`InvalidModelOutputError` ⇒ 502 `{"error":"ai_invalid_output","category":"invalid_output"}` في Brain — هذا كافٍ ومستقر. سيُصحَّح نص التوجيه في الـHub (DECISION_LOG تحت D-067: «`invalid_output` حقل `provider_code` لا فئة»).
3. **نعم** — يُضاف `AI_PRICE_TABLE_JSON` (مع `AI_PROVIDER_BASE_URL`/`AI_DEFAULT_MODEL`/`AI_HTTP_TIMEOUT_SECONDS` إن لم تكن موجودة) إلى `.env.template` بقيم فارغة وتعليق، ضمن Part A من السبرنت القادم.

---

## 4. ما أثبته المخطط بالتجربة (ست ملاحظات تدقيق أرسلها المالك — تحقق منها المخطط على PG 17 UTF8 بمِجسّ مستقل `tests/integration/test_zz_probe_s10.py`، ثم حُذف المِجسّ قبل أي `git add`)

> المِجسّ استخدم `CostLoggingGateway` الحقيقي + `PgCostLogWriter` فوق بوابة مُخطَّطة (150 رمزاً/نداء)، و`httpx.MockTransport` لـ`PublicPageFetcher`، وتطبيقَي ASGI للسباق، و`monkeypatch` على `PostgresWhatsAppRepository.get_account_by_phone_number_id` للعدّ. النواتج أدناه **حرفية**.

### B1 — الميزانية: عدّ مضاعف ×(1+N) وعدّ مفاتيح المستخدم على ميزانية المنصة — 🟠 اقتصادية (ثبتت كلتاهما)

```
PROBE-B1a: one model call of 150 tokens ⇒ ai_cost_log rows=1 tokens=150 | content_revisions tokens=1050 (7 items × 150) | DailyBudget combined_usage_since=1200 | factor=8x (expected 1x)
PROBE-B1b: after inserting ONE user_key row (251000 tokens) combined=252200 | platform POST /diagnose ⇒ 429 {'detail': {'error': 'generation_budget_exceeded', 'budget': 200000, 'used': 252200, 'resets_in_seconds': 86399}}
```

- **الآلية:** `advisor.py:_provenance` يضع `prompt_tokens/completion_tokens` في provenance كل عنصر من ≤ 7 عناصر (`update_body(provenance=…)` @`:412`)، و`CostLoggingGateway` يسجّل النداء نفسه في `ai_cost_log`، ثم `pg_budget.py:ai_token_usage_sources` يجمع الاثنين بـ`UNION ALL` ⇒ نداء واحد بـ150 رمزاً يُحتسب **1200**. و`ai_cost_log` لا يُرشَّح بـ`credential_mode='platform'` ⇒ نداءات `X-AI-Key` (يدفعها المستخدم) تستهلك ميزانية المنصة وتحجبها.
- **الأثر:** خطة محتوى واحدة تستهلك 8× حصتها؛ مستخدم BYOK يحجب نفسه (ومن يشاركه العلامة) عن مسارات المنصة.
- **الإصلاح المطلوب (Part A القادم):** (1) الرموز **لا** تُنسخ إلى provenance عناصر `content-plan` (يكفي `recommendation_id` + `prompt_hash`)، أو يُستثنى من فرع `revisions` ما يحمل `recommendation_id`؛ (2) فرع `ai_cost_log` يُرشَّح بـ`credential_mode = 'platform'` و`outcome = 'ok'`؛ (3) اختبار PG: نداء واحد ⇒ `combined_usage_since` = رموزه بالضبط، وصف `user_key` لا يؤثر. الاتجاه الأبعد (D-073/SPRINT-11): `ai_cost_log` المصدر الوحيد للميزانية بعد لفّ كل البوابات بـ`CostLoggingGateway`.

### B2 — الجلب العام: SSRF + robots غير متدفق + صفحات الخطأ كأدلة — 🔴 أمنية (ثبتت الثلاث)

```
PROBE-B2a: localhost | resolves=['127.0.0.1', '::1'] is_global=[False, False] | REFUSED host_not_allowed
PROBE-B2a: 127.0.0.1.nip.io | resolves=['127.0.0.1'] is_global=[False] | FETCHED status=200
PROBE-B2a: 169.254.169.254.nip.io | resolves=['169.254.169.254'] is_global=[False] | FETCHED status=200
PROBE-B2a: 10.0.0.1.nip.io | resolves=['10.0.0.1'] is_global=[False] | FETCHED status=200
PROBE-B2a: metadata.google.internal | resolves=['dns-fail:-2'] is_global=[] | FETCHED status=200
PROBE-B2a: fd00--1.sslip.io | resolves=['fd00::1'] is_global=[False] | FETCHED status=200
PROBE-B2a-net: requests actually sent by fetcher = 10 | ['https://127.0.0.1.nip.io/robots.txt', 'https://127.0.0.1.nip.io/', 'https://169.254.169.254.nip.io/robots.txt', 'https://169.254.169.254.nip.io/']
PROBE-B2b: robots.txt bytes pulled into memory = 4,710,400 (ROBOTS_MAX_BYTES=65,536) in 0.11s | page status=200
PROBE-B2c: upstream 403 text/html | FETCHED title='Error 403' ⇒ evidence confidence=0.40 value={'status': 403, 'title': 'Error 403', ...}
PROBE-B2c: upstream 404 text/html | FETCHED title='Error 404' ⇒ evidence confidence=0.40 ...
PROBE-B2c: upstream 500 text/html | FETCHED title='Error 500' ⇒ evidence confidence=0.40 ...
PROBE-B2c: upstream 503 text/html | FETCHED title='Error 503' ⇒ evidence confidence=0.40 ...
```

- **(أ) SSRF:** `normalize_host` يرفض `localhost` (تسمية واحدة) وIPv4 العاري فقط؛ أي اسم يحلّ إلى loopback/RFC1918/link-local/IPv6 خاص **يُجلب فعلاً** (المِجسّ أحصى 10 طلبات شبكة إلى هذه المضيفات). التخفيفات القائمة حقيقية لكنها غير كافية: منفذ 80/443 فقط، GET فقط، بلا تحويلات، `text/html` فقط (يحجب IMDS النصّي عملياً)، OWNER/MANAGER فقط، 10/ساعة. تبقى المنصة قادرة على **سبر** الخدمات الداخلية (رمز الحالة + العنوان + 4000 حرف من أي صفحة HTML داخلية) من مستأجر مصادَق.
  **الإصلاح المطلوب:** بعد `normalize_host` وقبل أي طلب: `getaddrinfo(host)` ⇒ رفض إن كان **أي** عنوان ليس `ip_address(x).is_global` (يغطي loopback/private/link-local/multicast/reserved/ULA)، ثم **تثبيت** العنوان المحلول للاتصال (أو على الأقل إعادة الفحص عند الاتصال) لسدّ DNS rebinding؛ ورفض `*.internal`/`*.local`/`*.localhost` اسمياً؛ اختبار بـ`MockTransport` + `monkeypatch` على `getaddrinfo` يثبت 0 طلب شبكة لكل حالة أعلاه.
- **(ب) robots غير متدفق:** `_robots_for` يستخدم `self._http.get` فيسحب الجسم كاملاً (4.7 MiB في المِجسّ) ثم يقصّ. **الإصلاح:** `stream("GET")` + `content-length` + عدّاد بايتات يقطع عند 64 KiB (نفس نمط `_get_page`).
- **(ج) صفحات الخطأ كأدلة:** أي حالة ≠ 3xx تُقبل، فتصبح صفحة 503 «Service unavailable» دليلاً `public_page_summary` بثقة **0.40** يغذّي التشخيص. **الإصلاح:** `status >= 400` ⇒ `PublicFetchRefused("unavailable", host=)` (سبب سابع) ⇒ `{"unavailable": "http_<status>"}` بثقة 0.00؛ `2xx` فقط يُقبل.

### B3 — `GET /profile/revisions` يكشف لـVIEWER ما يخفيه `GET /profile` — 🟠 تسريب بيانات خاصة (D-066) (ثبتت)

```
PROBE-B3: VIEWER GET /profile ⇒ 200 contains 'cheap'=False tone={'voice': 'warm', 'formality': 'casual', 'emoji_policy': 'light'} | VIEWER GET /profile/revisions ⇒ 200 contains 'cheap'=True snapshot.tone={'voice': 'warm', 'formality': 'casual', 'emoji_policy': 'light', 'banned_phrases': ['cheap'], 'reply_templates': []}
```

- **الآلية:** `router.py:392 RevisionPublic.from_domain(r)` ⇒ `schemas.py:105-114 snapshot=dict(rev.snapshot)` بلا مرشّح؛ بينما `ProfilePublic.from_domain(..., private=)` يحذف `banned_phrases`/`reply_templates`.
- **الإصلاح المطلوب:** `RevisionPublic.from_domain(rev, *, private)` يطبّق نفس المرشّح على `snapshot["tone"]` (وأي حقل خاص مستقبلي)؛ اختبار وحدة + PG: VIEWER لا يرى `banned_phrases`/`reply_templates` في أي مراجعة.

### B4 — سباق `reply_template` بلا ملف ⇒ 500، وجسم فارغ بعد التنظيف ⇒ 500 — 🟡 متانة (ثبتت كلتاهما)

```
PROBE-B4a: two concurrent reply_template accepts on brand WITHOUT profile row ⇒ [(500, 'Internal Server Error'), (200, '{"id":"…","kind":"reply_template","title":"t2",…')] | brand_profiles=[(1, ['أهلاً 2'])] | recs=[('t1', 'proposed'), ('t2', 'accepted')]
PROBE-B4b: content-plan item whose body is only fake tags ⇒ 500 Internal Server Error
```

- **(أ)** قبولان متزامنان على علامة بلا `brand_profiles` ⇒ كلاهما يرى `None` ⇒ `insert_profile` مرتين ⇒ `IntegrityError` غير ملتقط ⇒ **500** للثاني (الصف الأول يُرجَع بالكامل: التوصية بقيت `proposed` — سلوك آمن لكن الرسالة خاطئة). **الإصلاح:** `_store_reply_template` يلتقط `IntegrityError` على الإدراج ويعيد المحاولة مرة واحدة على مسار CAS؛ وفشل CAS يرفع خطأ خاصاً `ProfileVersionConflictError` ⇒ 409 `profile_conflict` بدل `RecommendationAlreadyDecidedError("proposed")` المضلِّل (يقول للمستخدم إن التوصية «مقرَّرة» وهي ليست كذلك).
- **(ب)** `_PlanItemOut.body` يقبل `min_length=1` لكن `strip_fake_tags` قد يفرغه ⇒ `create_item(body="")` ⇒ `ValidationError` من `content/schemas.py:58` غير ملتقط ⇒ **500**. **الإصلاح:** بعد التنظيف، جسم فارغ أو فوق حدّ القناة ⇒ العنصر يُسقَط ويُعدّ في `items_dropped` (نفس معاملة «بلا دليل»).

### B5 — `content_plan_item` غائب عن `diagnose` و`accept` خلافاً للانحراف #4 — 🟡 اتساق توثيق/كود (ثبتت)

```
PROBE-B5: diagnose with a content_plan_item recommendation ⇒ 502 {"detail":{"error":"ai_invalid_output","category":"invalid_output"}} | _RecommendationOut.kind = ["gap_action", "tone_adjustment", "reply_template"] | accept() branches on kinds: []
```

- توصية من النموذج بنوع `content_plan_item` تُسقط **التشخيص كله** بـ502 (لا تُسقَط التوصية وحدها). البرومبت `brand_diagnosis_v1.py:27` لا يطلب هذا النوع، فالأثر العملي محدود، لكن الادعاء في §6 #4 خاطئ. **القرار:** يُثبَّت أن `content_plan_item` تولد فقط من `/content-plan` (تصحيح docstring/التقرير)، **و** يُجعل `parse_output` متسامحاً مع توصية بنوع غير معروف (تُسقَط وتُعدّ في `recommendations_dropped`) بدل إسقاط التشخيص كله.

### B6 — لا سقف لعدد `phone_number_id` قبل HMAC ⇒ تضخيم استعلامات من نقطة عامة — 🟠 أمنية (DoS) (ثبتت)

```
PROBE-B6: body=950,048 bytes with 10,000 distinct phone_number_ids + bad signature ⇒ 401 | DB lookups performed BEFORE HMAC = 10,000 in 5.81s
```

- طلب واحد غير موقَّع (< 1 MiB) أنتج **10,000 استعلام** على `whatsapp_accounts` قبل أي تحقق توقيع. النقطة عامة بلا مصادقة. **الإصلاح المطلوب:** `MAX_PHONE_NUMBER_IDS = 8` (حمولات Meta الحقيقية تحمل معرّفاً واحداً عادةً) ⇒ أكثر من ذلك ⇒ 401 `webhook_unverified` قبل أي DB؛ واستعلام واحد `WHERE phone_number_id = ANY(:ids)` بدل حلقة؛ اختبار يعدّ الاستعلامات (≤ 1).

**الخلاصة:** 6/6 ملاحظات المالك صحيحة ومثبتة تجريبياً. لا واحدة منها تمسّ بيانات محفوظة أو تكسر اختباراً قائماً، لكن B2-أ وB6 **يجب** أن تُغلقا قبل أن يصل أي طلب من الإنترنت العام إلى المسارين المعنيين.

---

## 5. ما يُفتح الآن (قائمة ترحيل — **ليست توجيهاً**؛ نطاق SPRINT-11 يُعتمد بعد جلسة المالك الاستراتيجية)

**Part A الإلزامي للسبرنت القادم (أياً كان اسمه/نطاقه) — إغلاق B1–B6 كما وُصف في §4، بالترتيب:** B2-أ (SSRF) ⇒ B6 (سقف المعرّفات) ⇒ B1 (الميزانية) ⇒ B3 (المراجعات) ⇒ B2-ب/ج ⇒ B4-أ/ب ⇒ B5 + `.env.template` (س3) + تصحيح docstring #4.

**شرطان صريحان على المالك حتى إغلاق Part A القادم:**
1. 🔴 **لا يُسجَّل عنوان webhook واتساب لدى Meta على خادم مكشوف للإنترنت العام** (B6)؛ الربط الحي للاختبار مقبول خلف نفق/قائمة سماح IP لعناوين Meta فقط.
2. 🔴 **`POST /competitors/{id}/refresh` لا يُعرض لمستأجرين غير المالك** (B2-أ)؛ في بيئة المالك الواحدة لا خطر.

**بنود أخرى تُسجَّل في الـHub (لا تمنع شيئاً):**
- D-067 توضيح: `invalid_output` حقل `provider_code` لا فئة (§3-س2).
- `DATA_AND_INTEGRATIONS.md`: هجرات 0010/0011/0012 وجداول Brain السبعة.
- `SECURITY_BASELINE.md`: N1–N5 ✅ (مغلقة بـ`f2859d0`)، B1–B6 ⚠️ جديدة.

---

## 6. ملاحظة حوكمة

- 🔑 **التوكن ظهر في المحادثة للمرة الحادية عشرة** دون تدوير. يبقى الطلب قائماً: دوّره ثم مرّره مرة واحدة في بداية الجلسة.
- **إعادات ضبط الساندبوكس #19/#20/#21** خلال هذه المراجعة؛ الإجراء §2-ب من `SESSION_RECOVERY.md` (مع `initdb -E UTF8` — انحراف #7) أعاد البيئة في ≈ 1–2 دقيقة كل مرة. المِجسّ أُنشئ وشُغِّل وحُذف كاملاً في جلسة واحدة.
- **التوجيه نفسه أخطأ مرتين** (فئة `invalid_output`؛ عدد المسارات 14): يُسجَّلان كدرسين في `KNOWN_ISSUES.md` (التحقق من عقد D-067 قبل الإحالة إليه؛ عدّ المسارات صراحةً في التوجيه).
- **القرار الموصى به للمالك:** لا `SPRINT-10.1-HOTFIX`؛ الإصلاحات الست صغيرة (≈ يوم عمل) ومكانها الطبيعي Part A بعد اعتماد نطاق SPRINT-11 — ولأن الجلسة الاستراتيجية قد تعيد تشكيل الجلب العام نفسه (D-074) فإصلاحه قبلها قد يكون عملاً مهدوراً.
