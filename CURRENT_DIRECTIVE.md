# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-07 رُوجع مستقلاً و**قُبل ودُمج** بواسطة المخطط (`main` @ `0ceb125`؛ التقرير في [`sprints/SPRINT-07_REVIEW.md`](sprints/SPRINT-07_REVIEW.md) — اقرأ §2 و§3 و§4 قبل البدء: فيها أجوبة أسئلتك الثلاثة والملاحظات التي يُغلقها Part A). هذا التوجيه يُصدر **SPRINT-08 = المحطة M5**.

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-08-MULTI-PLATFORM-READINESS` |
| **النوع** | Part A: تصحيحات إلزامية من مراجعة SPRINT-07 (A-1..A-4) · Part B: **M5 جاهزية تعدد المنصات** — تجريد `Channel`/`Publisher` بسجل محوّلات، Meta مرجعاً، LinkedIn/X/TikTok محوّلات **هيكلية** بلا اعتمادات ولا نداء شبكي، مصفوفة التفعيل كبيانات تُقرأ من API · **لا هجرة جديدة** (انظر B-1 — `channel` يبقى VARCHAR وقيد CHECK يُوسَّع بهجرة **صغيرة واحدة** فقط إن تعذّر بغيرها) |
| **الحالة** | `ISSUED` — جاهز للتنفيذ فور دمج هذا التوجيه في `main` (المخطط يدمجه بنفسه — D-063) |
| **يبدأ من** | `main` @ `21ff44d` (دمج PR #23 — هذا التوجيه). أول أمر: `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` |
| **الفرع المستهدف** | `work/implementation` ⇒ PR إلى `main` بعنوان `SPRINT-08: multi-platform readiness (M5) — channel registry + structural adapters + SPRINT-07 review fixes` |
| **الكوميتات** | **ثلاثة**: Part A ⇒ Part B-1 (سجل القنوات + نقل Meta خلفه + هجرة 0007 إن لزمت) ⇒ Part B-2 (المحوّلات الهيكلية + مصفوفة التفعيل + API). كل كوميت يمرّ الحزمة كاملة وحده |
| **تبعيات جديدة** | **لا شيء.** لا SDK لأي منصة (`linkedin`, `tweepy`, `tiktok` … ممنوعة — تقع تحت D-068 بالروح وإن لم تكن في قائمة `banned-api`؛ **أضفها إليها**) |
| **الحجم المتوقع** | ~700 سطر كود + ~700 سطر اختبارات |
| **تاريخ الإصدار** | 2026-10-31 |

---

## 0. لماذا؟

1. **A-1 (مراجعة §4-أ):** `main.py:68-72` يبني `MetaPublisher` بـ`encryption=None` حين يغيب `TOKEN_ENCRYPTION_KEY`؛ مع `SCHEDULER_ENABLED=true` تنتج حلقة تعمل وتُفشل كل مهمة بـ`connection_unavailable` بصمت. نشر فعلي بلا مفتاح تشفير **خطأ إعداد** لا حالة تشغيلية ⇒ يُرفض الإقلاع.
2. **A-2 (مراجعة §4-ب، درس 17):** `pyproject.toml [tool.coverage.run]` بلا `concurrency` ⇒ ما يُنفَّذ داخل `greenlet_spawn` (كل `flush()/execute()` لـSQLAlchemy async) غير مرئي؛ `publishing/infrastructure/postgres_repository.py` 77% ظاهرياً و84% فعلياً. أرقام التغطية يجب أن تقيس ما يحدث.
3. **A-3 (مراجعة §3-2):** قرار المخطط على سؤالك: **عمود `publish_jobs.max_attempts` مصدر الحقيقة لكل مهمة؛ الإعداد `PUBLISH_MAX_ATTEMPTS` هو ما يُكتب فيه عند الإنشاء** (السياسة عند الإنشاء، الدليل في الصف). الآن `_job_for()` يعتمد على `server_default`.
4. **A-4 (مراجعة §4-ج):** B7-U3 منفَّذ كتأكيدات في ملفين بلا دالة باسمه؛ `pytest -k b7_u3` ⇒ 0.
5. **Part B (M5):** اليوم `Channel` تعداد بقيمتين داخل `content/domain/models.py:22`، و`CHANNEL_LIMITS` و`REQUIRED_PUBLISH_SCOPES` و`instagram_image_url` و`MetaPublisher` **كلها تعرف Meta بالاسم** في أربعة ملفات. إضافة منصة ثالثة اليوم = لمس `content/service.py` و`publishing/scheduler.py` و`main.py` معاً. M5 يجعل إضافة منصة = **ملف محوّل واحد + صف في السجل**، ويُظهر للمالك **مصفوفة صريحة** بما يلزم (حساب مطوّر، مراجعة تطبيق، OAuth، حدود) لتفعيل كل منصة — بلا كتابة سطر شبكي واحد لمنصة لا نملك اعتمادها (قرار `REPOSITORY_EVALUATIONS.md` §5: لا Postiz/Mixpost؛ نبني التجريد الرقيق فقط).

---

## 1. النطاق

**داخل:** A-1..A-4؛ `ChannelDefinition` + `ChannelRegistry` في `src/modules/publishing/channels/`؛ نقل `CHANNEL_LIMITS`/`REQUIRED_PUBLISH_SCOPES`/قواعد الوسائط/`Publisher` الخاص بـMeta خلف السجل **بلا تغيير سلوكي** (كل اختبارات SPRINT-06/07 تمرّ كما هي)؛ محوّلات هيكلية `linkedin`/`x`/`tiktok` بحالة `activation_status=not_configured` تُرجع `PublishOutcome.failure(channel_not_activated)` بلا أي نداء؛ `GET /api/v1/channels` (القدرات والمصفوفة)؛ منع `schedule` على قناة غير مفعَّلة بـ422 `channel_not_activated`؛ توسيع `ck_content_items_channel` و`ChannelLiteral`.
**خارج (ممنوع):** أي OAuth/نداء شبكي/SDK لـLinkedIn/X/TikTok؛ جداول اعتمادات منصات جديدة (تأتي مع تفعيل كل منصة في سبرنت خاص بها بعد قرار المالك)؛ فيديو/كاروسيل؛ منطق «أفضل وقت للنشر»؛ تغيير آلة الحالات (تبقى 56 خلية)؛ أي لمس لـ`ai_gateway/**` عدا قائمة `banned-api`.

---

## 2. المواصفات

### 2-0. القواعد الحاكمة (D-055 / D-064 / D-068 — إلزامية)

**(أ) لا نداء لمنصة غير مفعَّلة:** المحوّل الهيكلي **لا يستورد `httpx`** ولا يملك عميلاً؛ `publish()` فيه يُعيد `PublishOutcome.failure(ErrorClass.CHANNEL_NOT_ACTIVATED)` فوراً. اختبار حارس: `grep -rn "import httpx" src/modules/publishing/channels/` ⇒ **فارغ**، وأي محوّل غير Meta لا يحوي سلسلة `https://`.

**(ب) السجل مصدر الحقيقة الوحيد للقناة:** `CHANNEL_LIMITS` و`REQUIRED_PUBLISH_SCOPES` و`instagram_image_url` **تُحذف** من `content/` وتُستبدل بقراءة `registry.get(channel).limits / .required_scopes / .validate_media(media_refs)`. grep قبول: `grep -rn "CHANNEL_LIMITS\|REQUIRED_PUBLISH_SCOPES\|instagram_image_url" src` ⇒ **فارغ**.

**(ج) جدول الرؤية لا يتغيّر** (SPRINT-07 §2-0-ب يبقى حرفياً). `GET /channels` متاح لكل مستخدم مُصادَق (لا يحمل أسراراً ولا بيانات علامة؛ قدرات ثابتة للمنصة).

**(د) D-068 يمتد:** أضف إلى `[tool.ruff.lint.flake8-tidy-imports.banned-api]`: `linkedin`, `tweepy`, `tiktok`, `facebook_business`, `requests` (الأخير برسالة «Use src.core.http shared client»). `per-file-ignores` الحالية تبقى كما هي.

---

### Part A — إلزامي قبل أي ميزة (كوميت واحد)

| # | البند | التنفيذ المطلوب | الاختبار |
|---|---|---|---|
| **A-1** | رفض الإقلاع بمجدول بلا تشفير | `main.py::_start_scheduler`: إذا `scheduler_enabled and token_encryption_key is None` ⇒ `raise RuntimeError("SCHEDULER_ENABLED requires TOKEN_ENCRYPTION_KEY")` **قبل** بناء أي شيء؛ `PostgresScheduler.__init__` يرفض `encryption=None` بنفس الرسالة (الدفاع في طبقتين) | **A1-U1** `tests/test_main.py`: `create_app(settings(scheduler_enabled=True, token_encryption_key=None))` ثم دخول `lifespan` ⇒ `RuntimeError` بالرسالة؛ **A1-U2** مع مفتاح ⇒ المهمة تُنشأ (الاختبار الحالي B6-U1 يبقى) |
| **A-2** | قياس التغطية تحت greenlet | `pyproject.toml [tool.coverage.run]`: `concurrency = ["thread", "greenlet"]` (greenlet موجودة عبر SQLAlchemy — **لا تبعية جديدة**؛ تحقّق بـ`pip show greenlet`)؛ البوابة تبقى 95 | **A2-M1** (يدوي، يُسجَّل في §6): `pytest --cov=src -q` قبل/بعد ⇒ تغطية `publishing/infrastructure/postgres_repository.py` ≥ 84% بعد؛ CI `unit` ما زال ≥ 95 |
| **A-3** | `PUBLISH_MAX_ATTEMPTS` | `config.py`: `publish_max_attempts: int = Field(3, ge=1, le=10)`؛ `content/service.py::_job_for` يكتب `max_attempts=settings.publish_max_attempts` صراحةً؛ **المجدول لا يقرأ الإعداد** (يبقى `job.max_attempts`) | **A3-U1** إعداد 5 ⇒ المهمة المنشأة `max_attempts == 5`؛ **A3-U2** تغيير الإعداد بعد الإنشاء لا يغيّر مهمة قائمة (المجدول يحترم الصف)؛ **A3-U3** `PUBLISH_MAX_ATTEMPTS=0` ⇒ `ValidationError` عند بناء `Settings` |
| **A-4** | تسمية B7-U3 | دالة `test_b7_u3_protected_route_count_and_openapi_operations` في `tests/modules/content/test_router.py` تجمع التأكيدين (`PROTECTED_ROUTE_COUNT` من `test_auth_guard` يُستورد لا يُكرَّر؛ 14 عملية/13 مساراً) | `pytest -k b7_u3` ⇒ 1 passed |

> **قاعدة Part A:** لا تغيير سلوكي خارج ما ذُكر. الحزمة الكاملة تمرّ بعده بعدد الاختبارات القديم + الجديدة.

---

### Part B-1 — سجل القنوات + نقل Meta خلفه (كوميت واحد؛ **بلا تغيير سلوكي ملحوظ للمستخدم**)

**B1-أ. الحزمة الجديدة `src/modules/publishing/channels/`:**

```
channels/
  __init__.py          # get_registry() — يبني السجل مرة واحدة من التعريفات أدناه
  definition.py        # ChannelDefinition, ChannelCapabilities, ActivationStatus, ActivationRequirement, MediaRule
  registry.py          # ChannelRegistry: get(channel) / all() / activatable() ؛ KeyError ⇒ UnknownChannelError
  meta_facebook.py     # تعريف facebook_page + Publisher = MetaPublisher الحالي (يُنقل من infrastructure/ أو يُستدعى)
  meta_instagram.py    # تعريف instagram (يشترك مع facebook_page في MetaPublisher ونفس PageTokenProvider)
  linkedin.py          # هيكلي
  x.py                 # هيكلي
  tiktok.py            # هيكلي
  structural.py        # StructuralPublisher: publish() ⇒ failure(channel_not_activated) بلا نداء
```

**B1-ب. `ChannelDefinition` (Pydantic frozen):**

| الحقل | النوع | ملاحظة |
|---|---|---|
| `channel` | `Channel` (StrEnum موسَّع: `facebook_page, instagram, linkedin, x, tiktok`) | يبقى في `content/domain/models.py` كمصدر التعداد؛ `ChannelLiteral` في `schemas.py` يُولَّد منه (`Literal[*Channel]` أو `get_args`) لا يُكرَّر يدوياً |
| `display_name` | `str` | |
| `provider` | `Literal["meta", "linkedin", "x", "tiktok"]` | المزوّد المشترك (facebook_page/instagram ⇒ `meta`) |
| `activation_status` | `ActivationStatus ∈ {active, not_configured}` | Meta ⇒ `active`؛ الثلاثة ⇒ `not_configured`. **يُقرأ من التعريف لا من إعداد** في M5 (التفعيل الحقيقي سبرنت لاحق يقلبه مع اعتماداته) |
| `capabilities` | `ChannelCapabilities{text_limit:int, requires_media:bool, max_media:int, media_kinds:frozenset[str], supports_link:bool, supports_scheduling:bool}` | facebook_page `{5000, False, 1, {image}, True, True}`؛ instagram `{2200, True, 1, {image}, False, True}`؛ linkedin `{3000, False, 1, {image}, True, True}`؛ x `{280, False, 1, {image}, True, True}`؛ tiktok `{2200, True, 1, {video}, False, True}` — أرقام المنصات غير المفعَّلة **توثيقية** من وثائقها العامة وتُراجع عند التفعيل |
| `required_scopes` | `tuple[str, ...]` | Meta كما هي الآن؛ الثلاثة: النطاقات الموثَّقة علناً (`w_member_social`؛ `tweet.write users.read offline.access`؛ `video.publish`) — **توثيقية** |
| `activation_requirements` | `tuple[ActivationRequirement, ...]` | كل عنصر `{key, title, owner ∈ {platform_owner, brand_owner}, status ∈ {done, pending, not_started}, note}` — **هذه مصفوفة التفعيل**. Meta: `developer_app(done)`, `app_review_publishing(pending — بند المالك)`, `brand_oauth(brand_owner)`. LinkedIn: `developer_app`, `marketing_api_access(review)`, `brand_oauth`. X: `developer_account(paid tier)`, `app_oauth2`, `brand_oauth`. TikTok: `developer_app`, `content_posting_api_audit`, `brand_oauth` |
| `validate_media(media_refs) -> None \| raises MediaRuleError(reason)` | دالة | يحلّ محل `instagram_image_url`: instagram ⇒ صورة https jpg/jpeg/png إلزامية؛ facebook_page ⇒ ≤1 صورة اختيارية؛ الهيكلية ⇒ قواعدها التوثيقية (tiktok يتطلب `kind=video`) |
| `publisher_factory` | `Callable[[PublisherDeps], Publisher]` | `PublisherDeps{settings, http_client}`؛ Meta ⇒ `MetaPublisher(MetaGraphClient(settings, http_client))`؛ الهيكلية ⇒ `StructuralPublisher(channel)` |

**B1-ج. النقل بلا تغيير سلوكي:**
- `content/service.py`: `_validate_schedule`/`_validate_target`/`BodyTooLongError`/`/generate` تقرأ من `registry.get(item.channel)`؛ **تُحذف** `CHANNEL_LIMITS`, `REQUIRED_PUBLISH_SCOPES`, `missing_publish_scopes` (تنتقل إلى `channels/registry.py` كدالة تأخذ التعريف), `instagram_image_url`.
- `content/prompts.py` يقرأ `limit` من السجل.
- `publishing/scheduler.py`: بدل `publisher` واحد يأخذ `PublisherResolver` (`Callable[[Channel], Publisher]`) — `_process` يحلّ الناشر من `job.channel`. `main.py::_start_scheduler` يبني الـresolver من السجل.
- **شرط إضافي عند `schedule`:** `registry.get(channel).activation_status is not ACTIVE` ⇒ 422 `channel_not_activated{channel, requirements:[pending/not_started فقط]}` **قبل** أي فحص اتصال.
- `ErrorClass` يكتسب `CHANNEL_NOT_ACTIVATED` (فشل **نهائي** — ليس في `RETRYABLE_ERROR_CLASSES`).

**B1-د. الهجرة `0007_channels` (فقط إن لزمت):** `ck_content_items_channel` (0005:92) يقيّد القيم بـ`('facebook_page','instagram')`؛ عنصر بقناة `linkedin` **لا يُنشأ** في M5 (الإنشاء مسموح؟ — **نعم**: المسودات والموافقة تعمل لأي قناة في السجل؛ الرفض عند `schedule` فقط، كما نطاقات النشر في SPRINT-07 B3). إذن الهجرة **لازمة**: توسيع CHECK إلى القيم الخمس + CHECK مماثل على `publish_jobs.channel` إن وُجد. **لا شيء آخر في 0007.** التخفيض: `UPDATE content_items SET status='archived' WHERE channel NOT IN (القديمة)` **قبل** تضييق CHECK — وطبّق درس 15 (الترتيب) وB1-M1 حرفياً.

**اختبارات B-1:**
- **B1-U1** `test_registry_has_exactly_five_channels_and_meta_only_active` — السجل = `{facebook_page, instagram, linkedin, x, tiktok}`؛ `activatable()` ⇒ الثلاثة.
- **B1-U2** `test_channel_literal_derived_from_enum` — `set(get_args(ChannelLiteral)) == {c.value for c in Channel}`.
- **B1-U3** `test_capabilities_frozen_and_documented` — كل تعريف غير Meta يحمل ≥ 3 `activation_requirements` ولا واحد منها `done`؛ Meta يحمل `app_review_publishing` بحالة `pending`.
- **B1-U4** `test_validate_media_matches_sprint07_behaviour` — جدول: instagram بلا وسائط/http/gif ⇒ `MediaRuleError`؛ jpg/png مع query ✓؛ facebook_page بلا وسائط ✓؛ tiktok بصورة ⇒ خطأ.
- **B1-U5** `test_no_content_module_knows_channel_constants` — `grep` برمجي (AST أو نص) على `src/modules/content/**` ⇒ لا `CHANNEL_LIMITS`/`REQUIRED_PUBLISH_SCOPES`/`instagram_image_url`/`"https://"`.
- **B1-M1** (على PG) هجرة `head ⇒ 0006 ⇒ head` مع صف `channel='linkedin'` في `draft` ⇒ التخفيض يؤرشفه ولا يفشل.
- **كل اختبارات SPRINT-06/07 تمرّ بلا تعديل** عدا استيرادات نُقلت (وثّق كل اختبار عُدِّل في §6 مع السبب).

---

### Part B-2 — المحوّلات الهيكلية + مصفوفة التفعيل + API (كوميت واحد)

**B2-أ. `StructuralPublisher(channel)`:** `publish()` ⇒ `PublishOutcome.failure(ErrorClass.CHANNEL_NOT_ACTIVATED)`؛ لا يلمس `page_token_provider`؛ يسجّل `WARNING` واحداً `publish: channel not activated channel=…` (بلا نص). **لا يصل إليه المجدول عملياً** لأن `schedule` يرفض مسبقاً — لكنه الحارس الثاني (مهمة قديمة بعد تعطيل قناة).

**B2-ب. API:**

| المسار | الدور | الاستجابة |
|---|---|---|
| `GET /api/v1/channels` | أي مُصادَق (`CurrentUser`) | `ChannelListPublic{channels:[ChannelPublic]}`؛ `ChannelPublic{channel, display_name, provider, activation_status, capabilities, required_scopes, activation_requirements:[{key,title,owner,status,note}]}` — **لا حقل سرّي، لا بيانات علامة** |
| `GET /api/v1/channels/{channel}` | أي مُصادَق | `ChannelPublic` أو 404 `unknown_channel` |

- `PROTECTED_ROUTE_COUNT` 32 ⇒ **34**؛ حدّث الثابت والاختبار A-4.
- `POST …/items` بقناة `linkedin` ⇒ 201 (مسودة)؛ `submit/approve` ✓؛ `schedule` ⇒ 422 `channel_not_activated` مع `requirements` غير المكتملة فقط.

**B2-ج. `PublishStatusDetail`** لا يتغيّر؛ `error_class=channel_not_activated` يظهر فيه كأي فئة.

**اختبارات B-2:**
- **B2-U1** `test_structural_publisher_never_calls_token_provider_or_network` — `PageTokenProvider` وهمي يرفع إن استُدعي؛ `respx` بلا مسارات مسجَّلة (`assert_all_mocked`) ⇒ لا نداء؛ النتيجة `permanent_failure/channel_not_activated`.
- **B2-U2** `test_channels_endpoint_shape_and_no_secrets` — 5 عناصر؛ لا مفتاح يحوي `token|secret|key` (حارس OpenAPI الحالي يمتد إلى المسارين).
- **B2-U3** `test_schedule_on_not_configured_channel_is_422_before_connection_checks` — علامة **بلا أي اتصال** + عنصر `approved` بقناة `x` ⇒ 422 `channel_not_activated` (لا 409 `reauth_required`)؛ `requirements` تحوي `developer_account`.
- **B2-U4** `test_meta_channels_schedule_path_unchanged` — السيناريوهات الثلاثة لـSPRINT-07 B2-U3 (نطاقات ناقصة ⇒ 409؛ IG بلا صورة ⇒ 422 `instagram_requires_image`؛ ✓) **بنفس أكواد الخطأ حرفياً** عبر السجل.
- **B2-I1** (PG) المجدول مع `PublisherResolver`: مهمة `facebook_page` تُنشر (FakePublisher)؛ مهمة `linkedin` مُدرجة يدوياً في `publish_jobs` ⇒ `failed/channel_not_activated` + `publish_failed` + حدث نظامي، **بلا استدعاء** أي ناشر Meta.
- **B2-U5** `test_banned_api_extended` — قراءة `pyproject.toml` ⇒ القائمة تحوي `linkedin, tweepy, tiktok, facebook_business, requests`.

---

## 3. معايير القبول

1. `ruff check src tests alembic scripts` + `ruff format --check …` نظيفان.
2. `pytest --cov=src --cov-fail-under=95` على PG حقيقية: **0 فشل**؛ العدد ≥ 792 + الجديدة؛ CI 5/5 على الكوميت الأعلى.
3. `grep -rn "CHANNEL_LIMITS\|REQUIRED_PUBLISH_SCOPES\|instagram_image_url" src` ⇒ **فارغ**.
4. `grep -rn "import httpx\|https://" src/modules/publishing/channels/ | grep -v meta_` ⇒ **فارغ**.
5. `grep -rln "import httpx" src | sort` ⇒ الأربعة نفسها (+ `channels/meta_*.py` **ممنوع** — Meta تستدعي `MetaGraphClient` من `social_connect/meta_client.py` لا httpx مباشرة).
6. `ls alembic/versions/` ⇒ 0001–0007 (0007 فقط إن لزمت وبمحتوى B1-د حصراً).
7. الاختبارات بالأسماء كلها موجودة وناجحة: A1-U1..U2, A3-U1..U3, B7-U3 (A-4), B1-U1..U5, B1-M1, B2-U1..U5, B2-I1. `pytest -k b7_u3` ⇒ 1.
8. `PROTECTED_ROUTE_COUNT == 34`؛ B2-U1 من SPRINT-07 ما زال **56 خلية** (آلة الحالات لم تُمسّ).
9. `pyproject.toml` تغييرات: `concurrency`، قائمة `banned-api` الموسَّعة — **لا تبعية**.
10. ثلاثة كوميتات فوق `main`؛ `git diff <start> --name-only` خالٍ من الملفات المحظورة (`ARCHITECTURE_LOCK.md`, `SESSION_RECOVERY.md`, `imaster_planner.md`, `README.md`, `AI_Marketing_Platform_Hub/**`, `sprints/**`).
11. §6 مكتمل **بأسماء منسوخة من الكود** (درس 18): كل ثابت/فئة/مسار يُذكر في §6 يجب أن يطابق `grep` على الشجرة.

---

## 4. أوامر

```bash
git checkout work/implementation && git fetch origin && git merge --ff-only origin/main
pip install -e ".[dev]" && pip show greenlet | head -2
export TEST_DATABASE_URL=postgresql+asyncpg://aigos@localhost:5433/ai_growth_os_test
export DATABASE_URL=$TEST_DATABASE_URL
export AUTH_JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
ruff check src tests alembic scripts && ruff format --check src tests alembic scripts
pytest --cov=src --cov-fail-under=95
grep -rn "CHANNEL_LIMITS\|REQUIRED_PUBLISH_SCOPES\|instagram_image_url" src
grep -rn "import httpx\|https://" src/modules/publishing/channels/ | grep -v meta_
grep -rln "import httpx" src | sort
ls alembic/versions/
pytest -k "b7_u3 or b1_u or b2_u or b2_i1 or a1_u or a3_u" -q
```

> **ملاحظة ساندبوكس (من المخطط):** إن كان `/var/run/postgresql` غير قابل للكتابة بعد إعادة الضبط، شغّل PG بـ`-c unix_socket_directories=/tmp` واستخدم `psql -h localhost`.

---

## 5. التزامات المنفذ

1. **Part A** كوميت: `fix(publishing,ci): SPRINT-08 Part A — refuse scheduler without encryption key (A-1), greenlet coverage tracing (A-2), PUBLISH_MAX_ATTEMPTS written at job creation (A-3), named B7-U3 (A-4)` ⇒ ادفع.
2. **Part B-1** كوميت: `refactor(publishing): SPRINT-08 Part B-1 — channel registry + definitions, Meta behind registry (no behaviour change), PublisherResolver in scheduler, migration 0007 channel CHECK` ⇒ ادفع.
3. **Part B-2** كوميت: `feat(publishing): SPRINT-08 Part B-2 — structural adapters (linkedin/x/tiktok), activation matrix, GET /channels, 422 channel_not_activated on schedule` ⇒ ادفع.
4. **ادفع بعد كل ملف** (`wip:` ثم سحق إلى الكوميتات الثلاثة قبل التسليم).
5. املأ §6 بالسطر **وبأسماء منسوخة من الكود**؛ `WORK_STATE.md` (§1 ⇒ `SPRINT_08_DELIVERED_AWAITING_REVIEW`؛ سطر جديد أعلى §6). عند التسليم اكتب حرفياً: **«PR #N جاهز لمراجعة المخطط»**. لا تدمج (D-063).
6. أي بند متعذر ⇒ وثّق الانحراف بالسطر والسبب؛ **لا** تُضعف: جدول الرؤية، «لا نداء لمنصة غير مفعَّلة»، «السجل مصدر الحقيقة الوحيد»، آلة الحالات 56.
7. إن اكتشفت أن نقل Meta خلف السجل يتطلب تغيير **سلوك** ملحوظ (كود خطأ، شكل استجابة) ⇒ **توقّف واسأل في §6** بدل التغيير الصامت — M5 refactor يجب أن يكون غير مرئي لعميل SPRINT-07.

---

## 6. تقرير المنفذ (يملؤه المنفذ)

- **حالة التنفيذ:** `[ ] مكتمل` / `[ ] جزئي — السبب:`
- **Commit SHA Part A:** — **B-1:** — **B-2:** — **docs:**
- **PR:** #— (`work/implementation` ⇒ `main` @ …) — **CI:** —/5 على الكوميت الأعلى
- **الاختبارات:** وحدة — / تكامل — / الكل — ناجحاً، — فشل — **التغطية:** —% (قبل A-2: —%، بعد: —%)
- **A-1..A-4:** (بالملف والسطر)
- **B-1:** (السجل، التعريفات الخمسة، ما حُذف من `content/`، `PublisherResolver`، 0007 إن وُجدت، الاختبارات التي عُدِّلت ولماذا)
- **B-2:** (المحوّلات، `/channels`، 422 عند `schedule`، `PROTECTED_ROUTE_COUNT`)
- **القبول §3:** (نواتج الـgreps حرفياً)
- **انحرافات (بالسطر والسبب):**
- **أسئلة:**

---

## 7. ما ينتظر المالك

1. 🔑 **تدوير توكن GitHub — الطلب الثامن.** التوكن ظهر في المحادثة مجدداً؛ يُعدّ مكشوفاً. بعد التدوير يُمرَّر للمخطط مرة واحدة في بداية الجلسة فقط.
2. 🛡️ حماية `main` بالفحوص الخمسة + منع الدفع المباشر (ما زالت غير مفعَّلة؛ الدمج يتم عبر API بلا قيود).
3. **M4 — App Review من Meta** لنطاقي `pages_manage_posts` و`instagram_content_publish`: النشر الفعلي جاهز تقنياً ويعمل لمدراء/مختبري التطبيق فقط حتى تكتمل المراجعة. **ابدأ الآن** (أسابيع). مصفوفة التفعيل في M5 ستُظهر هذا البند `pending`.
4. **M5 — قرار بعد هذا السبرنت لا قبله:** أي من LinkedIn/X/TikTok تُفعَّل أولاً؟ المصفوفة من `GET /channels` ستُظهر ما يلزم لكل واحدة (حساب مطوّر مدفوع لـX؛ مراجعة Marketing API لـLinkedIn؛ تدقيق Content Posting لـTikTok). لا تُنشئ حسابات الآن.
5. **قبل M6:** حساب WhatsApp Business (Cloud API) ورقم اختبار.
6. **قبل M7:** مزوّد الذكاء الاصطناعي الحقيقي الأول (OpenAI-compatible؛ الآن `AI_PROVIDER=fake`).
7. **للعلم (قرارات المخطط على أسئلة المنفذ في SPRINT-07):** إعادة «تحقّقتُ يدوياً» بعد نتيجة مبهمة **مؤجَّلة** (#14)؛ `max_attempts` عمود المهمة + إعداد عند الإنشاء (A-3 هنا)؛ `SCHEDULER_ENABLED` على **نسخة واحدة** في بيئة التشغيل الأولى (#15) — سيُوثَّق في M9.
