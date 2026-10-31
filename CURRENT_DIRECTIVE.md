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

- **حالة التنفيذ:** `[x] مكتمل` / `[ ] جزئي`
- **Commit SHA Part A:** `e2b55a4` — **B-1:** `e5ed7bc` — **B-2:** `7ec7fed` — **docs:** (كوميت هذا التقرير — يُسجَّل في `WORK_STATE.md` §6)
- **PR:** #— (`work/implementation` ⇒ `main` @ `e73e9cc`) — **CI:** —/5 على الكوميت الأعلى (يُحدَّث بعد فتح الـPR)
- **الاختبارات:** وحدة **776** / تكامل **54** / الكل **830** ناجحاً، **0** فشل (`pytest --cov=src --cov-report=term-missing --cov-fail-under=95` على PG 17 حقيقي، 343s) — **التغطية:** **98.87%** (قبل A-2: TOTAL **94.30%** و`src/modules/publishing/infrastructure/postgres_repository.py` **76%** (95 عبارة، 20 مفقودة: 62-67,71-72,79,82-92,96-98,104,258-265)؛ بعد A-2: TOTAL **98.97%** على كود Part A وحده / **98.87%** على الكل، والملف نفسه **84%** (13 مفقودة: 67,71-72,82-92,97,258-265) ⇒ A2-M1 ≥ 84 ✓). `pytest -k "b7_u3 or b1_u or b2_u or b2_i1 or a1_u or a3_u" -q` ⇒ **107 passed**. ruff check + format --check على `src tests alembic scripts` ⇒ `All checks passed!` / `151 files already formatted`.
- **A-1..A-4:**
  - **A-1:** `src/modules/publishing/scheduler.py:81` ثابت `SCHEDULER_REQUIRES_ENCRYPTION_KEY = "SCHEDULER_ENABLED requires TOKEN_ENCRYPTION_KEY"`؛ `PostgresScheduler.__init__` يرفض `encryption is None` (`scheduler.py:120`)؛ `src/main.py::_start_scheduler` (`main.py:84`) يرفع `RuntimeError(SCHEDULER_REQUIRES_ENCRYPTION_KEY)` عند `main.py:95` **قبل** بناء أي شيء. اختبارات: `tests/test_main.py:186 test_a1_u1_scheduler_enabled_without_encryption_key_refuses_boot`، `tests/test_main.py:210 test_a1_u1_postgres_scheduler_rejects_encryption_none`، `tests/test_main.py:227 test_a1_u2_scheduler_enabled_with_encryption_key_creates_task`.
  - **A-2:** `pyproject.toml:120` `concurrency = ["thread", "greenlet"]` تحت `[tool.coverage.run]` (الأرقام قبل/بعد أعلاه؛ البوابة 95 باقية).
  - **A-3:** `src/core/config.py:132` `publish_max_attempts: int = Field(default=3, ge=1, le=10)`؛ `src/modules/content/router.py:118` يمرّر `publish_max_attempts=settings.publish_max_attempts` إلى `ContentService`؛ `src/modules/content/service.py:130/137` يحفظه، و`_job_for(..., *, max_attempts)` يكتبه صراحةً في `PublishJob` عند `service.py:343` (schedule) و`service.py:447` (retry_publish). المجدول لا يقرأ الإعداد. اختبارات: `tests/modules/content/test_router.py:1323 test_a3_u1_publish_max_attempts_setting_is_written_into_new_job`، `:1347 test_a3_u2_changing_setting_after_creation_does_not_touch_existing_job`، `:1399 test_a3_u3_publish_max_attempts_zero_is_rejected_by_settings`.
  - **A-4:** `tests/modules/content/test_router.py:1411 test_b7_u3_protected_route_count_and_openapi_operations` يستورد `PROTECTED, PROTECTED_ROUTE_COUNT` من `tests/test_auth_guard.py` (الثابت `tests/test_auth_guard.py:39`؛ التأكيد `== 34` في `test_auth_guard.py:54` و`test_router.py:1417` بعد B-2).
- **B-1:**
  - **الحزمة** `src/modules/publishing/channels/`: `__init__.py` (`get_registry()` بـ`lru_cache(maxsize=1)`)، `definition.py` (`ChannelDefinition`, `ChannelCapabilities`, `ActivationStatus{ACTIVE,NOT_CONFIGURED}`, `ActivationRequirement`, `RequirementOwner{PLATFORM_OWNER,BRAND_OWNER}`, `RequirementStatus{DONE,PENDING,NOT_STARTED}`, `MediaRule`, `MediaRuleError(reason)`, `PublisherDeps{settings,http_client}`؛ خصائص `limits`, `is_active`, `validate_media(media_refs)`, `incomplete_requirements()`, `build_publisher(settings, http_client)`)، `registry.py` (`ChannelRegistry(Mapping)` مع `get()/all()/activatable()/active()`؛ `UnknownChannelError(KeyError)`؛ `missing_publish_scopes(account, definition)` منقولة من `content/service.py`)، `meta_facebook.py` (`FACEBOOK_PAGE`, `META_ACTIVATION_REQUIREMENTS`, `build_meta_publisher(deps)` ⇒ `MetaPublisher(MetaGraphClient(deps.settings, deps.http_client))` باستيراد كسول)، `meta_instagram.py` (`INSTAGRAM` يشارك `build_meta_publisher` ونفس `PostgresPageTokenProvider` عبر المجدول)، `linkedin.py` (`LINKEDIN`)، `x.py` (`X`)، `tiktok.py` (`TIKTOK`)، `structural.py` (`StructuralPublisher`).
  - **`Channel`** في `src/modules/content/domain/models.py:22` موسَّع إلى `FACEBOOK_PAGE, INSTAGRAM, LINKEDIN, X, TIKTOK`؛ `ChannelLiteral = Literal[tuple(c.value for c in Channel)]` في `src/modules/content/schemas.py` (مشتق لا مكرَّر — B1-U2).
  - **ما حُذف من `content/`:** `CHANNEL_LIMITS` (كان `domain/models.py:28`)، `REQUIRED_PUBLISH_SCOPES` و`IMAGE_SUFFIXES` و`missing_publish_scopes` و`instagram_image_url` (كانت `service.py:91–125`)، `_CHANNEL_LABEL` (كان `prompts.py:21`). البديل: `service.py::channel_definition(channel)` ⇒ `get_registry().get(channel)` تستخدمه `enforce_channel_limit`، `/generate` (`max_tokens` والاقتطاع)، و`_validate_target` (`missing_publish_scopes(owner, definition)` + `definition.validate_media(refs)` ⇒ `InstagramRequiresImageError` عند `MediaRuleError` — نفس كود 422 `instagram_requires_image`)؛ `prompts.py::system_prompt` يقرأ `definition.display_name` و`definition.limits` (نص القالب لم يعد يذكر «إنستغرام 2200 / فيسبوك 5000» حرفياً — الحد يأتي من `{limit}`).
  - **`PublisherResolver`:** `src/modules/publishing/scheduler.py:84` `PublisherResolver = Callable[[Channel], Publisher]`؛ معامل `PostgresScheduler.__init__` الثاني أصبح `publisher_resolver`؛ `_process` يحلّ `self._resolve_publisher(Channel(job.channel))` (`scheduler.py:193`). `src/main.py:72 build_publisher_resolver(settings, http_client)` يبني ناشراً واحداً لكل تعريف من `get_registry().all()` ويعيد `publishers.__getitem__`؛ `_start_scheduler` يستخدمه بدل `MetaPublisher(MetaGraphClient(...))` (أُزيل استيرادا `MetaPublisher`/`MetaGraphClient` من `main.py`).
  - **`ErrorClass.CHANNEL_NOT_ACTIVATED = "channel_not_activated"`** في `src/modules/publishing/domain/models.py` — ليس في `RETRYABLE_ERROR_CLASSES` (اختبار `test_channel_not_activated_is_final_not_retryable`). **`ChannelNotActivatedError`** (`src/modules/content/exceptions.py`، 422 `channel_not_activated`، حقول `channel`, `requirements` = `incomplete_requirements()` فقط) تُرفع أول `_validate_target` (قبل `TargetPageNotSelectedError`) ⇒ تغطي `schedule` و`publish/retry`؛ `router.py::to_http` يضيف `channel`/`requirements` إلى `detail`.
  - **0007:** `alembic/versions/0007_channels.py` — `upgrade`: إسقاط/إعادة `ck_content_items_channel` بالقيم الخمس فقط؛ `publish_jobs.channel` **بلا CHECK في 0006** (VARCHAR(16) عادي) ⇒ لا شيء يُوسَّع ولا يُضاف. `downgrade`: `UPDATE content_items SET status='archived', scheduled_for=NULL, published_at=NULL WHERE channel NOT IN (القديمة)` ثم `UPDATE … SET current_revision_id = NULL` ثم `DELETE FROM content_items WHERE channel NOT IN (القديمة)` (المراجعات/الأحداث/المهام تتبع بـCASCADE) ثم تضييق CHECK — انظر «انحرافات».
  - **الاختبارات الجديدة:** `tests/modules/publishing/test_channels.py` — `:39 test_registry_has_exactly_five_channels_and_meta_only_active` (B1-U1)، `:67 test_channel_literal_derived_from_enum` (B1-U2)، `:73 test_capabilities_frozen_and_documented` (B1-U3)، `:168 test_validate_media_matches_sprint07_behaviour` (B1-U4، 15 حالة)، `:181 test_no_content_module_knows_channel_constants` (B1-U5، grep برمجي على `src/modules/content/**` للأسماء الثلاثة و`"https://"` + على `src/**` للأسماء الثلاثة)؛ `tests/integration/test_postgres_persistence.py:2706 test_b1_m1_migration_0007_roundtrip_archives_linkedin_draft_on_pg` (B1-M1: `head ⇒ 0006 ⇒ head` بصف `linkedin` في `draft` مع مراجعة و`current_revision_id` مضبوط؛ التخفيض لا يفشل؛ صفوف Meta تبقى؛ `x` يُرفض تحت 0006 ويُقبل عند head؛ لا CHECK على `publish_jobs.channel`).
  - **اختبارات SPRINT-06/07 المعدَّلة (كلها لأسباب ميكانيكية، لا تغيير في السلوك المختبَر):**
    1. `tests/modules/publishing/test_scheduler_unit.py:102` — `self.publisher` ⇒ `lambda _channel: self.publisher` (توقيع `PostgresScheduler` صار يأخذ `PublisherResolver`).
    2. `tests/integration/test_scheduler_pg.py:192` (`_scheduler`) — `publisher` ⇒ `lambda _channel: publisher` (السبب نفسه).
    3. `tests/test_main.py:220` (`test_a1_u1_postgres_scheduler_rejects_encryption_none` — من Part A) — `FakePublisher()` ⇒ `lambda _channel: FakePublisher()` (السبب نفسه).
    4. `tests/modules/content/test_router.py:133 test_b3_u1_create_validates_body_and_channel` — مثال القناة المرفوضة `"tiktok"` ⇒ `"myspace"`؛ `tiktok` قناة مسجَّلة الآن والمسودة بها 201 حسب التوجيه (B1-د)، ويبقى التحقق من القيمة المجهولة ⇒ 422.
    5. `tests/integration/test_postgres_persistence.py` — ستة تأكيدات `version_num == "0006"` بعد `upgrade head` (الأسطر 520، 925، 1946، 2016، 2437، 2566) ⇒ `"0007"` (رأس السلسلة تغيّر).
- **B-2:**
  - **المحوّلات:** `src/modules/publishing/channels/structural.py::StructuralPublisher(channel).publish()` ⇒ `PublishOutcome.failure(ErrorClass.CHANNEL_NOT_ACTIVATED)` فوراً؛ `del revision, page_token_provider, last_creation_id` (لا يلمسها)؛ `logger.warning("publish: channel not activated channel=%s job=%s item=%s", ...)` واحد بلا نص المنشور. مربوطة في `linkedin.py`/`x.py`/`tiktok.py` عبر `_structural(deps)` كـ`publisher_factory`.
  - **`/channels`:** `src/modules/publishing/channels/router.py` — `router = APIRouter(prefix="/channels", tags=["channels"])`؛ `GET ""` ⇒ `ChannelListPublic{channels: list[ChannelPublic]}`؛ `GET "/{channel}"` ⇒ `ChannelPublic` أو 404 `{"error": "unknown_channel"}`؛ كلاهما بـ`CurrentUser` (بلا دور علامة). `ChannelPublic{channel, display_name, provider, activation_status, capabilities, required_scopes, activation_requirements: list[ActivationRequirementPublic{key,title,owner,status,note}]}`؛ لا حقل سرّي ولا بيانات علامة (B2-U2 يمشي على كل المفاتيح: لا مفتاح يحوي `token|secret|key` عدا `activation_requirements.key` التسمية). مُركَّب في `src/main.py:136` `api_v1.include_router(channels_router)`.
  - **422 عند `schedule`:** انظر B-1 (`ChannelNotActivatedError` قبل أي فحص اتصال). `POST …/items` بـ`linkedin`/`x` ⇒ 201؛ `submit`/`approve` ⇒ 200؛ `schedule` ⇒ 422 `channel_not_activated{channel, requirements}` بالمتطلبات غير المكتملة فقط؛ لا مهمة تُنشأ.
  - **`PROTECTED_ROUTE_COUNT`:** 32 ⇒ **34** (`tests/test_auth_guard.py:54`، `tests/modules/content/test_router.py:1417`).
  - **`banned-api`:** `pyproject.toml:[tool.ruff.lint.flake8-tidy-imports.banned-api]` += `"linkedin"`, `"tweepy"`, `"tiktok"`, `"facebook_business"`, `"requests"` (الأخيرة `msg = "Use src.core.http shared client"`)؛ `per-file-ignores` بلا تغيير. `tests/modules/ai_gateway/test_contract.py` حُدِّث: ثابت `BANNED_PLATFORM_MODULES` و`test_b0_u1_pyproject_bans_the_same_modules` يتحقق من المجموعة الموسَّعة ومن رسالة AIGateway على الخمسة الأصلية فقط.
  - **الاختبارات:** `tests/modules/publishing/test_channels.py:210 test_structural_publisher_never_calls_token_provider_or_network` (B2-U1 — مزوّد رموز يرمي `AssertionError` إن استُدعي؛ تحذير واحد؛ + grep برمجي: لا `import httpx` في أي ملف من `channels/` ولا `https://` خارج `meta_*`)، `:252 test_banned_api_extended` (B2-U5)؛ `tests/modules/content/test_router.py:1427 test_channels_endpoint_shape_and_no_secrets` (B2-U2)، `:1501 test_schedule_on_not_configured_channel_is_422_before_connection_checks` (B2-U3 — علامة بلا اتصال + عنصر `x` معتمد ⇒ 422 لا 409/`target_page_not_selected`؛ `developer_account` ضمن المتطلبات؛ 281 حرفاً على `x` ⇒ `body_too_long{limit:280}` من السجل)، `:1548 test_meta_channels_schedule_path_unchanged` (B2-U4 — السيناريوهات الثلاثة + 409 `reauth_required{missing_scopes:["pages_manage_posts"]}`)؛ `tests/integration/test_scheduler_pg.py:522 test_b2_i1_publisher_resolver_routes_meta_and_structural_jobs_on_pg` (B2-I1 — مهمة `facebook_page` تُنشر عبر `FakePublisher`؛ مهمة `linkedin` مُدرجة يدوياً ⇒ `failed`/`channel_not_activated`، محاولة `permanent_failure`، العنصر `publish_failed`، حدث نظام `publish_failed`، `counters["publish_failed"] == 1`، ناشر Meta لم يُستدعَ لها، ولا إعادة محاولة في دورة لاحقة).
- **القبول §3 (نواتج الـgreps حرفياً):**
  ```
  $ grep -rn "CHANNEL_LIMITS\|REQUIRED_PUBLISH_SCOPES\|instagram_image_url" src
  (فارغ — exit 1)
  $ grep -rn "import httpx\|https://" src/modules/publishing/channels/ | grep -v meta_
  (فارغ — exit 1)
  $ grep -rln "import httpx" src | sort
  src/core/http.py
  src/modules/ai_gateway/dependencies.py
  src/modules/ai_gateway/facades/openai_compatible.py
  src/modules/social_connect/meta_client.py
  $ ls alembic/versions/
  0001_initial_social_connections.py  0002_page_token_nullable.py  0003_tenancy.py  0004_auth.py  0005_content.py  0006_publishing.py  0007_channels.py
  $ pytest -k "b7_u3 or b1_u or b2_u or b2_i1 or a1_u or a3_u" -q
  107 passed, 723 deselected
  ```
  آلة الحالات: `tests/modules/content/test_lifecycle.py:106` ما زال `len(EXPECTED) == 56`. `git diff e73e9cc -- pyproject.toml` = `concurrency` + خمسة أسطر `banned-api` (+ تعليقان) فقط؛ لا تبعية جديدة. لا ملف محظور في الـdiff (`git diff --name-only e73e9cc..HEAD` = 30 ملفاً كلها تحت `src/ tests/ alembic/ pyproject.toml` + `CURRENT_DIRECTIVE.md`/`WORK_STATE.md` في كوميت docs).
- **انحرافات (بالسطر والسبب):**
  1. **تخفيض 0007 يحذف الصفوف لا يؤرشفها فقط** (`alembic/versions/0007_channels.py::downgrade`): التوجيه نصّ على `UPDATE … SET status='archived'` قبل التضييق؛ لكن صفاً يحتفظ بـ`channel='linkedin'` يكسر CHECK المضيَّق على PG حقيقي مهما كانت حالته. لذلك: `UPDATE` للأرشفة (حفاظاً على نية التوجيه في أي تدقيق) ثم `UPDATE … current_revision_id = NULL` لفكّ الـFK الدائري (`fk_content_items_current_revision_id` `ON DELETE SET NULL` — نمط 0005/0006) ثم `DELETE` فالـCASCADE. B1-M1 يثبت أن التخفيض لا يفشل وأن صفوف Meta لا تُمسّ. **لا تُفقد بيانات في الاتجاه الأمامي**؛ التخفيض هرم كامل لعالم 0006 الذي لا يعرف هذه القنوات أصلاً.
  2. **توسيع `banned-api` نُقل من Part A إلى Part B-2** (هو مذكور في §2-0-د كقاعدة حاكمة وفي B2-U5 كاختبار): إضافته في Part A كسرت `tests/modules/ai_gateway/test_contract.py::test_b0_u1_pyproject_bans_the_same_modules` (كان يتحقق من **مساواة** المجموعة ورسالة واحدة للكل)، وقاعدة Part A «لا تغيير سلوكي خارج المذكور» منعت تعديل اختبار SPRINT-07 هناك. في B-2 عُدِّل الاختبار (`BANNED_PLATFORM_MODULES`) مع B2-U5.
  3. **`https_image` في `MediaRule`** يُبنى بـ`"https:" + "//"` في `definition.py::_is_https_image` كي لا يحوي ملف في `channels/` خارج `meta_*` الحرفية `https://` (grep §3-4)؛ السلوك مطابق لـ`instagram_image_url` السابقة (B1-U4).
  4. **`ChannelNotActivatedError` تُرفع داخل `_validate_target`** لا في `schedule` وحدها: `publish/retry` (B7) يمرّ بالدالة نفسها، فقناة غير مفعَّلة تُرفض فيه أيضاً بـ422 — متسق مع «لا نداء لمنصة غير مفعَّلة».
  5. **نصوص `note`/`title` في مصفوفة التفعيل** صيغت بلا كلمتي `secret`/`token` (مثل «app credentials live only in environment variables») حتى يتحقق B2-U2 من غياب الكلمتين في الاستجابة كاملة لا في المفاتيح فقط.
  6. **بيئة الساندبوكس:** أربع إعادات ضبط إضافية خلال هذا السبرنت (الإجمالي 19)؛ كل الشغل نجا عبر `wip:` + دفع بعد كل ملف ثم سُحق إلى الكوميتات الثلاثة (`git restore --source=<wip> …` لكل جزء؛ شجرة الكوميت الأعلى مطابقة بالـhash لآخر `wip`).
- **أسئلة:**
  1. `registry.py::ChannelRegistry.active()` أُضيفت فوق الثلاث المطلوبة (`get/all/activatable`) لاستخدام الاختبارات؛ تُحذف إن أردت الالتزام الحرفي.
  2. بعد تفعيل قناة ثالثة لاحقاً: هل يُقلب `activation_status` من التعريف وحده (كما في M5) أم يُقرأ من إعداد؟ لم أغيّر شيئاً — فقط للتخطيط.

---

## 7. ما ينتظر المالك

1. 🔑 **تدوير توكن GitHub — الطلب الثامن.** التوكن ظهر في المحادثة مجدداً؛ يُعدّ مكشوفاً. بعد التدوير يُمرَّر للمخطط مرة واحدة في بداية الجلسة فقط.
2. 🛡️ حماية `main` بالفحوص الخمسة + منع الدفع المباشر (ما زالت غير مفعَّلة؛ الدمج يتم عبر API بلا قيود).
3. **M4 — App Review من Meta** لنطاقي `pages_manage_posts` و`instagram_content_publish`: النشر الفعلي جاهز تقنياً ويعمل لمدراء/مختبري التطبيق فقط حتى تكتمل المراجعة. **ابدأ الآن** (أسابيع). مصفوفة التفعيل في M5 ستُظهر هذا البند `pending`.
4. **M5 — قرار بعد هذا السبرنت لا قبله:** أي من LinkedIn/X/TikTok تُفعَّل أولاً؟ المصفوفة من `GET /channels` ستُظهر ما يلزم لكل واحدة (حساب مطوّر مدفوع لـX؛ مراجعة Marketing API لـLinkedIn؛ تدقيق Content Posting لـTikTok). لا تُنشئ حسابات الآن.
5. **قبل M6:** حساب WhatsApp Business (Cloud API) ورقم اختبار.
6. **قبل M7:** مزوّد الذكاء الاصطناعي الحقيقي الأول (OpenAI-compatible؛ الآن `AI_PROVIDER=fake`).
7. **للعلم (قرارات المخطط على أسئلة المنفذ في SPRINT-07):** إعادة «تحقّقتُ يدوياً» بعد نتيجة مبهمة **مؤجَّلة** (#14)؛ `max_attempts` عمود المهمة + إعداد عند الإنشاء (A-3 هنا)؛ `SCHEDULER_ENABLED` على **نسخة واحدة** في بيئة التشغيل الأولى (#15) — سيُوثَّق في M9.
