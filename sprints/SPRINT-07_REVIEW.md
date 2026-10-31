# 📋 SPRINT-07-PUBLISHING-AND-SCHEDULER — تقرير مراجعة المخطط

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-07-PUBLISHING-AND-SCHEDULER` (المحطة M4 + إغلاق O1–O4 + عقد المزوّد D-067/D-068) |
| **المُراجِع** | AI Master Planner |
| **تاريخ المراجعة** | 2026-10-31 |
| **الفرع المُراجَع** | `work/implementation` @ `3dae6c1` (Part A `00c288c`، B-0 `b3544a7`، B-1 `9234831`، B-2 `364de2b`، وثائق `3dae6c1`) فوق `main` @ `95a14f8` |
| **Pull Request** | [#22](https://github.com/belalalibb/ai_agancy_markting/pull/22) — 57 ملفاً، +6610/−464 |
| **الحكم النهائي** | 🟢 **مقبول — دُمج بواسطة المخطط (D-063)**. كل بنود A-1..A-6 وB-0 وB1–B7 منفَّذة ومُتحقَّق منها بالسطر والتجربة على PostgreSQL 17 حقيقية (بيئة أُعيد بناؤها من الصفر)؛ هجرة واحدة (0006) تصعد وتهبط وتعود؛ لا تبعية جديدة؛ لا ملف محظور مُسّ؛ تنفيذ «مرة واحدة بالضبط» ثابت على محرّكين مستقلين 3/3 + في الحزمة. **ثلاث ملاحظات صغيرة** (§4) تُغلق في SPRINT-08 الجزء A — لا تمسّ الأمان ولا الصحة. |

---

## 1. ما تحقق منه المخطط فعلياً (بيئة طازجة؛ PG 17.11 + pgvector 0.8.7 أُعيد تثبيتهما بعد إعادة ضبط الساندبوكس)

| الفحص | نتيجة المخطط | ادعاء المنفذ | تطابق |
|---|---|---|---|
| CI على `3dae6c1` (Checks API) | `lint` / `unit` / `integration` / `secrets` / `licenses` كلها `success` (×2: push + PR) | 5/5 | ✅ |
| `ruff check` + `ruff format --check` على `src tests alembic scripts` | نظيفان (**139 ملفاً** — بما فيها `scripts/` التي قال المنفذ إنها غير موجودة؛ انظر §2 الانحراف 11) | نظيفان (136 ملفاً بلا `scripts`) | ✅ |
| الحزمة كاملة على PG حقيقية + تغطية | **792 passed، 0 فشل، 98.58%** (4301 عبارة، 42 غير مغطاة، 22 فرعاً جزئياً) في 244 ث | 792 / 98.21% | ✅ |
| فجوات التغطية | `publishing/infrastructure/postgres_repository.py` **77%** بمقياس pytest-cov الافتراضي — لكن **84%** عند تتبّع greenlet (`coverage run --concurrency=thread,greenlet`)؛ فرع `IntegrityError ⇒ ActiveJobExistsError` (الأسطر 62–66) **مُنفَّذ فعلاً** في B2-I1 ولا يراه المُتتبّع الافتراضي لأن SQLAlchemy async يُشغّل `flush()` داخل greenlet. المتبقي الحقيقي: `raise` العام (:67)، `get_job` (:71-72)، `latest_job_for_item` بلا مهمة نشطة (:82-92)، `:258-265` | 76% أدنى ملف | ⚠️ ليست فجوة اختبار بل فجوة **قياس** — تُصلح بإعداد (§4-ب) |
| `git diff 95a14f8 -- pyproject.toml` | إعدادات ruff فقط: `TID` في `select`؛ `flake8-tidy-imports.banned-api` لـ`httpx/openai/anthropic/litellm/google.generativeai` برسالة «Use AIGateway (D-068)»؛ `per-file-ignores TID251` لـ`tests/**`, `ai_gateway/**`, `social_connect/meta_client.py`, `core/**` | ✓ | ✅ |
| greps القبول | `response_model_exclude_none\|next_revision_no\|AIRateLimitedError\|AIUpstreamError` في `src` ⇒ **0**؛ `import httpx` ⇒ **4 ملفات بالضبط** (`core/http.py`, `ai_gateway/dependencies.py`, `ai_gateway/facades/openai_compatible.py`, `social_connect/meta_client.py`)؛ `alembic/versions/` = 0001–**0006** | ✓ | ✅ |
| الملفات المحظورة | `git diff 95a14f8 --name-only` خالٍ من `ARCHITECTURE_LOCK/SESSION_RECOVERY/imaster_planner/README/AI_Marketing_Platform_Hub/sprints` | ✓ | ✅ |
| الاختبارات بالأسماء | A1-U1..U2, A2-U1..U2, A3-I1, A4-U1, A4-I1, A5-U1, A5-I1, A6-U1, B0-U1..U4, B1-M1, B2-U1 (**56 خلية**), B2-U2..U3, B2-I1..I2, B3-U1, B4-U1..U3, B6-U1, B6-I1..I5, B7-U1..U2 — **32/33 بالاسم**؛ **B7-U3** منفَّذ كتأكيدات (`test_auth_guard.py:54` `PROTECTED_ROUTE_COUNT == 32`؛ `test_router.py:871` 14 عملية/13 مساراً) **بلا دالة باسمها** | «كلها موجودة» | ✅ (ملاحظة تسمية §4-ج) |
| **B1-M1 يدوياً** | `DROP SCHEMA` ⇒ `upgrade head` ⇒ `0006` (5 فهارس على `publish_jobs` منها `uq_publish_jobs_active_item`) ⇒ `downgrade 0005` ⇒ الجدولان `NULL`، `approval_events.actor_user_id` عاد `NOT NULL` ⇒ `upgrade head` ⇒ `0006` | ✓ | ✅ |
| **B6-I3 حتمية** | `test_b6_i3_two_workers_two_engines_each_job_published_exactly_once` **3/3 منفرداً** (0.8 ث) + داخل الحزمة الكاملة؛ `asyncio.Barrier(2)`، محرّكان مستقلان، 5 مهام، `batch=3`، `seen == {job: 1}` | 3/3 | ✅ |
| **A3-I1 منفرداً** | 5 طلبات متزامنة ⇒ 5 مراجعات متتالية بلا 500 — ناجح على PG | ✓ | ✅ |

### 1-أ. التحقق بنداً بنداً (بالسطر @ `3dae6c1`)

| # | البند | ما قرأه المخطط | الحكم |
|---|---|---|---|
| **A-1** | فصل `provenance` عام/خاص (D-066) | `content/domain/models.py:33-52` `PUBLIC_PROVENANCE_KEYS = {kind, generated_at, prompt_version}` + `public_provenance()`؛ `schemas.py:99-112` `ContentRevisionPublic.from_domain` يُسقِط؛ `test_router.py:603/678/686/697`: جسد VIEWER ∩ `PRIVATE_KEYS` = ∅ في التفصيل والقائمة؛ المالك يرى الخاصة في `generation_log` فقط. **الكود يطابق التوجيه حرفياً** (تقرير §6 كتب «kind/provider/model/generated_at» خطأً — انظر §2) | ✅ |
| **A-2** | حدود القناة من الخدمة | `service.py` `CHANNEL_LIMITS` ⇒ `BodyTooLongError` 422 `body_too_long{channel,limit,length}`؛ A2-U1/U2 | ✅ |
| **A-3** | ترقيم المراجعات تحت قفل | `content/infrastructure/postgres_repository.py:70-77` `get_for_update` بـ`.with_for_update()`؛ `:160` `COALESCE(MAX(revision_no),0)+1` داخل القفل؛ `next_revision_no` محذوفة (grep 0)؛ A3-I1 | ✅ |
| **A-4** | JOIN واحد + نموذجان بدل `exclude_none` | `:90` `.outerjoin(` على `current_revision_id`؛ `ContentItemDetail`/`ContentItemDetailWithUsage`؛ `response_model_exclude_none` grep 0 | ✅ |
| **A-5** | ميزانية رموز يومية لكل علامة | `ai_token_usage_since`/`oldest_ai_generation_at` ⇒ `GenerationBudgetExceededError` 429 + `Retry-After`؛ `AI_DAILY_TOKEN_BUDGET_PER_BRAND` | ✅ |
| **A-6** | `prompt_version` في `provenance` | `prompts.py:PROMPT_VERSION`؛ ضمن المفاتيح العامة | ✅ |
| **B-0** | عقد المزوّد (D-067) + No-Bypass (D-068) | `ai_gateway/contract.py`: `Operation = Literal["generate_text"]`؛ `CredentialMode = Literal["platform", "user_key"]`؛ `ErrorCategory(StrEnum)` **12 فئة قانونية**؛ `RETRYABLE_DEFAULT`؛ `sanitize_provider_code` `^[a-z0-9_]{1,64}$`؛ `AIError.error = f"ai_{category}"`. `facades/openai_compatible.py`: 401/403⇒INVALID_CREDENTIAL، 429⇒RATE_LIMITED، 404⇒MODEL_UNAVAILABLE، 400/413/422⇒CONTENT_REJECTED إن حمل الكود «content» وإلا BAD_REQUEST، 5xx⇒RETRYABLE_SERVER_ERROR، 200 بلا choices/`insufficient_quota`⇒QUOTA_EXCEEDED، `refusal`/`content_filter`⇒CONTENT_REJECTED، timeout⇒TIMEOUT، شبكة⇒PROVIDER_UNAVAILABLE؛ `_IMMEDIATE_RETRY={TIMEOUT, RETRYABLE_SERVER_ERROR}`. `content/router.py:165-188` `_AI_HTTP`: RATE_LIMITED⇒429 `ai_rate_limited`+Retry-After؛ QUOTA_EXCEEDED⇒429 `ai_quota_exceeded`؛ INVALID_CREDENTIAL/AUTH_EXPIRED⇒503 `ai_provider_misconfigured`؛ CONTENT_REJECTED⇒422؛ البقية 502 ثابت. `test_b0_u3_response_shape_maps_to_category` جدول 17 حالة + `test_no_bypass` AST | ✅ |
| **B1** | هجرة 0006 + 56 خلية | `0006_publishing.py`: `published_at` + CHECK iff؛ `approval_events.action` 16⇒32؛ 8 أفعال؛ الفاعل nullable + `ck_approval_events_actor_required`؛ `publish_jobs` (`max_attempts` server_default 3، `ck_publish_jobs_attempts`، فهرس جزئي نشط، فهرس استحقاق)؛ `publish_attempts`؛ التخفيض بالترتيب الصحيح (إسقاط CHECK ⇒ UPDATE ⇒ تضييق). `lifecycle.py:128` `assert len(TRANSITIONS) == 8*7 == 56`؛ `:133-136` `SYSTEM_TRANSITIONS` = خليتان فقط خارج `transition()` | ✅ |
| **B2** | `schedule` يُنشئ المهمة في المعاملة نفسها | `service.py:91-125` `REQUIRED_PUBLISH_SCOPES`/`missing_publish_scopes` (غير مُتحقَّق ⇒ كلها ناقصة)/`instagram_image_url` (https + jpg/jpeg/png، يتجاهل query)؛ `:438-447` `create_job` بعد CAS في الجلسة نفسها، `ActiveJobExistsError ⇒ PublishInProgressError` 409؛ `:285-291` `_cancel_active_job`: `leased` ⇒ 409 بلا تغيير؛ B2-I1 يثبت التراجع (`approved, None` بعد 409) **والفرع مُنفَّذ فعلاً** (تتبّع greenlet) | ✅ |
| **B3** | نطاقا النشر افتراضياً؛ الرفض عند `schedule` فقط | `config.py` `META_OAUTH_SCOPES` الخمسة؛ B3-U1 | ✅ |
| **B4** | عميل Graph للنشر + خريطة أخطاء | `meta_client.py` `publish_page_post`/`create_instagram_container`/`publish_instagram_container`؛ Bearer + `appsecret_proof`؛ `meta_publisher.py:46-81` `classify_graph_error`: الكود أولاً (190/102، 10/200–299، 4/17/32/613، 1/2، 100)، ثم 5xx/شبكة ⇒ transient، **أي كود آخر ⇒ `unknown`**، بلا كود ⇒ حسب النوع. `domain/models.py:48` `RETRYABLE_ERROR_CLASSES = {rate_limited, transient}` ⇒ **`unknown` فشل نهائي لا إعادة** — خيار محافظ صحيح تحت D-055 (لا تكرار صامت) | ✅ |
| **B5** | `Publisher` Protocol + `MetaPublisher` | `domain/publisher.py` `PublishOutcome` لا يرمي لأخطاء المزوّد؛ `PostgresPageTokenProvider` صفحة مختارة في اتصال `active` فقط؛ IG `last_creation_id` ⇒ `media_publish` فقط؛ `connection_id_to_mark` عند `token_invalid` | ✅ |
| **B6** | المجدول بثلاث معاملات | `scheduler.py:119` `_recover_expired_leases` (محاولة مفتوحة ⇒ ambiguous + failed + publish_failed **بلا ناشر**؛ وإلا requeue)؛ `:146` `_lease_batch` بـ`skip_locked=True` (`infrastructure/postgres_repository.py:106-131`)؛ `:157` `_process`: `start_attempt(outcome NULL)` + commit **قبل** Graph؛ `:223` `attempt_count < job.max_attempts`؛ `:231` `next_attempt_at = now + backoff[attempt_count-1]`؛ `:246-248` `mark_out_of_band` بعد commit. `main.py:67-82` الحلقة عند `scheduler_enabled` فقط (افتراضي `False`)؛ `MetaPublisher(MetaGraphClient(settings, app.state.http_client))` | ✅ |
| **B7** | مساران + نموذجان | `router.py:583` `GET …/publish` (`PublishStatusDetail` للمالك/المدير، `PublishStatusPublic` للمشاهد)؛ `:606` `POST …/publish/retry` (`CurrentUserFresh`؛ `ambiguous_outcome` ⇒ 409 `manual_verification_required`)؛ `PROTECTED_ROUTE_COUNT` 32 | ✅ |

---

## 2. تقييم تقرير المنفذ (§6 @ `3dae6c1`)

التقرير **شامل ومطابق للكود في الجوهر**، لكنه يحمل **ثلاث عدم دقّات لفظية** يجب أن يعرفها المنفذ لأن §6 هو ما يُقرأ عند الاستئناف:

- **A-1:** كتب «kind/provider/model/generated_at للجميع» — الكود `PUBLIC_PROVENANCE_KEYS = {kind, generated_at, prompt_version}` (`provider`/`model` **خاصة**). الكود صحيح والتوجيه مُحترَم؛ التقرير هو الخطأ.
- **B-0:** كتب `credential_mode ∈ {platform, byok}` وأسماء فئات غير قانونية (`context_too_long`, `upstream_unavailable`, `network`, `unknown`) — الكود يستخدم `user_key` والفئات الـ12 القانونية من D-067 (`bad_request`, `retryable_server_error`, `provider_unavailable`, `non_retryable_error`). الكود صحيح.
- **الانحراف 11** («`scripts/` غير موجود في الشجرة») **خاطئ**: `scripts/{backup_progress.sh, check_licenses.py, license_allowlist.txt, verify_hub.py}` موجودة على `main` منذ SPRINT-04. ruff عليها نظيف (تحقّقتُ) فلا ضرر، لكن المنفذ يجب أن يُشغّل أمر §4 كما هو.

الانحرافات المقبولة مع تعليق:
- **1** (ترتيب التخفيض): خطأ حقيقي اكتشفه الاختبار الذي طلبه التوجيه (B1-M1) — هذا هو الغرض من الاختبار؛ **يُسجَّل درساً** (KNOWN_ISSUES 15).
- **2** (`unknown` لا `token_invalid`): قرار صحيح؛ `token_invalid` تحمل أثراً جانبياً (قلب الاتصال) فلا تُمنح لكود مجهول. **ويُضاف:** `unknown` نهائي لا يُعاد — صحيح تحت D-055.
- **7** (`graph_error_code/subcode/trace_id` للمالك/المدير): مقبول — أرقام ومعرّف تتبّع لا نص Meta؛ ضمن D-064.
- **8** (لا `publishing/router.py`): مقبول؛ المسارات تحت `/content/items/{id}` فعلاً. **شرط:** عند M5 حين يصبح `Publisher` متعدد المنصات، منطق الوحدة يبقى في `publishing/` والراوتر يبقى طبقة HTTP فقط.
- **9** (`encryption=None` ⇒ `connection_unavailable`): مقبول كحماية من انهيار الحلقة؛ لكن يجب أن يظهر **تحذير ERROR واحد عند الإقلاع** إن كان `SCHEDULER_ENABLED=true` بلا `TOKEN_ENCRYPTION_KEY` — وإلا تعمل حلقة تفشل كل مهمة بصمت. (§4-أ)
- **12** (مصانع مستودعات قابلة للحقن لرفع تغطية `unit`): مقبول — لا تغيير سلوكي، والاختبارات الـ14 ذات قيمة بذاتها. لكن السبب الجذري الأعمق هو **فجوة القياس** (§1 سطر التغطية): جزء من الفجوة ليس نقص اختبارات بل تتبّع لا يرى greenlet. (§4-ب)

---

## 3. أجوبة أسئلة المنفذ الثلاثة

1. **مسار صريح «تحقّقتُ يدوياً — أعد» بعد نتيجة مبهمة؟** — **مؤجَّل (بند مؤجَّل #14)؛ لا يُبنى في M4.** السبب: المخرج الحالي (`unschedule ⇒ approved ⇒ schedule`) **صريح وآمن وقابل للتدقيق** — يمرّ عبر فعلين بشريين مسجَّلين بحدثي `publish_cancelled` و`scheduled` بالفاعل، وهذا بالضبط ما يطلبه D-055 (موافقة بشرية على الفعل الخارجي). مسار `verified_not_live` يختصر خطوة واحدة فقط مقابل **سطح هجوم منطقي جديد** (علم يتجاوز حارساً) قبل أن نرى حالة مبهمة حقيقية واحدة من Meta. **شرط إعادة الفتح:** أول نتيجة `ambiguous_outcome` في بيئة تشغيل، أو تصميم بوابة الموافقة في M10 حيث يُقرَّر شكل الزر.
2. **`max_attempts`: إعداد أم عمود؟** — **كلاهما بأدوار مختلفة: العمود مصدر الحقيقة لكل مهمة؛ الإعداد `PUBLISH_MAX_ATTEMPTS` (افتراضي 3) هو القيمة التي تُكتب في العمود عند إنشاء المهمة.** المبدأ: **السياسة عند الإنشاء، والدليل في الصف** — تغيير الإعداد لا يُغيّر مهام قائمة بأثر رجعي (وهذا مرغوب: مهمة أُنشئت بوعد 3 محاولات تحصل عليها)، والمجدول لا يقرأ إلا `job.max_attempts` (كما هو الآن). التنفيذ صغير: `_job_for()` يمرّر `settings.publish_max_attempts` بدل الاعتماد على `server_default`. يُدرج في SPRINT-08 A-3 بقيد `ge=1, le=10`.
3. **`SCHEDULER_ENABLED` على نسخة واحدة فقط في بيئة التشغيل الأولى؟** — **نعم.** `SKIP LOCKED` (B6-I3) هو **شبكة أمان** تضمن الصحة، لا **تصميماً** للتوازي: نسختان تُضاعفان استعلامات الاستطلاع على الجدول نفسه بلا مكسب في الإنتاجية عند حجم V1، وتُصعّبان قراءة السجلات (`lease_owner` متعدد). القاعدة التشغيلية: **نسخة واحدة بـ`SCHEDULER_ENABLED=true`؛ الباقي `false`**؛ عند الحاجة لتوسيع فعلي يُفصل المجدول إلى عملية `worker` مستقلة (نفس الكود، نقطة دخول مختلفة) — وهذا بند جاهزية التشغيل M9 (بند مؤجَّل #15). **لا يُضاف قفل «قائد» الآن** — تعقيد بلا حاجة مثبتة.

---

## 4. ملاحظات المخطط (ليست عوائق — تُغلق في SPRINT-08 الجزء A)

- **(أ) تحذير إقلاع المجدول بلا تشفير:** `main.py` يبني `MetaPublisher` بـ`encryption=None` إذا غاب `TOKEN_ENCRYPTION_KEY`؛ مع `SCHEDULER_ENABLED=true` ينتج ذلك حلقة تعمل وتُفشل كل مهمة بـ`connection_unavailable`. المطلوب: سجل `ERROR` واحد صريح عند الإقلاع + عدّاد، أو رفض الإقلاع (`RuntimeError`) — **القرار: رفض الإقلاع** لأن النشر الفعلي بلا مفتاح تشفير حالة إعداد خاطئة لا تشغيلية.
- **(ب) قياس التغطية تحت greenlet:** `pyproject.toml [tool.coverage.run]` بلا `concurrency`؛ المُتتبّع لا يرى ما يُنفَّذ داخل `greenlet_spawn` (كل `flush()/execute()` لـSQLAlchemy async). الأثر: `publishing/infrastructure/postgres_repository.py` يظهر 77% وهو 84% فعلياً، وقد يُخفي فروعاً مُختبَرة في ملفات أخرى. المطلوب: `concurrency = ["thread", "greenlet"]` (greenlet تبعية موجودة أصلاً عبر SQLAlchemy — **لا تبعية جديدة**) + التحقق من أن البوابة 95 ما زالت تمرّ (ستزيد لا تنقص).
- **(ج) تسمية B7-U3:** التأكيدات موجودة لكن بلا دالة `test_b7_u3_*`؛ أمر `pytest -k b7_u3` من §4 التوجيه يُعيد 0. المطلوب: دالة واحدة باسم `test_b7_u3_protected_route_count_and_openapi_operations` تجمع التأكيدين (أو تُغلّفهما).
- **(د) §6 كمصدر استئناف:** عدم الدقّات الثلاث في §2 لا تُصلَّح بأثر رجعي (التوجيه يُستبدل)، لكن المنفذ يلتزم في SPRINT-08 §6 بـ**نسخ أسماء الثوابت من الكود لا من الذاكرة**.

---

## 5. ما يُفتح الآن

- **M4 مكتمل**: النشر الفعلي إلى Facebook Page وInstagram بموافقة بشرية، تراجع زمني، نتيجة مبهمة بلا تكرار، «مرة واحدة بالضبط» مُثبَتة. **ينقص للنشر لصفحات غير مدراء التطبيق: App Review من Meta (بند المالك).**
- **M5 جاهز للإصدار** (SPRINT-08): تجريد `Channel`/`Publisher` متعدد المنصات فوق `Publisher` Protocol القائم؛ Meta مرجعاً؛ محوّلات هيكلية لـLinkedIn/X/TikTok بلا اعتمادات؛ مصفوفة التفعيل.
- **D-068 No-Bypass** صار قابلاً للتنفيذ آلياً (ruff TID251 + AST) — أي مزوّد جديد يمرّ من البوابة حكماً.

---

## 6. ملاحظة حوكمة

- الدمج تمّ بواسطة المخطط وفق D-063؛ المالك لم يُطلب منه الدمج.
- توكن GitHub ظهر في المحادثة للمرة **الثامنة** — طلب التدوير يتكرر في §7 من التوجيه الجديد.
- الساندبوكس أُعيد ضبطه **14 مرة** حتى الآن عبر المشروع (12 عند المنفذ خلال SPRINT-07 + 2 عند المخطط)؛ الدفع بعد كل كتابة ملف ما زال القاعدة المنقذة، وسكربت إعادة البناء `/tmp/setup_env.sh` احتاج تعديلاً هذه المرة (`unix_socket_directories=/tmp` لأن `/var/run/postgresql` غير قابل للكتابة) — مسجَّل في `SESSION_RECOVERY.md`.
