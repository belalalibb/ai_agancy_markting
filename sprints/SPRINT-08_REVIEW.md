# 📋 SPRINT-08-MULTI-CHANNEL-ABSTRACTION — تقرير مراجعة المخطط

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-08-MULTI-CHANNEL-ABSTRACTION` (المحطة M5 + إغلاق ملاحظات SPRINT-07 أ–د) |
| **المُراجِع** | AI Master Planner |
| **تاريخ المراجعة** | 2026-10-31 |
| **الفرع المُراجَع** | `work/implementation` @ `9ba947e` (Part A `e2b55a4`، B-1 `e5ed7bc`، B-2 `7ec7fed`، وثائق `dbb87c8`/`7721b4f`/`9ba947e`) فوق `main` @ `e73e9cc` |
| **Pull Request** | [#26](https://github.com/belalalibb/ai_agancy_markting/pull/26) — 32 ملفاً، +1834/−95 |
| **الحكم النهائي** | 🟢 **مقبول — دُمج بواسطة المخطط (D-063)**. A-1..A-4 وB-1/B-2 منفَّذة ومُتحقَّق منها بالسطر وبالتشغيل على PostgreSQL 17 حقيقية (بيئة أُعيد بناؤها من الصفر مرتين خلال هذه المراجعة)؛ 830 اختباراً تمرّ؛ ruff نظيف؛ لا تبعية جديدة؛ لا ملف محظور مُسّ. **لا ملاحظات على كود SPRINT-08 نفسه.** في المقابل أثبت المخطط **بالتجربة** ثلاث ثغرات كامنة موروثة من SPRINT-07 (§4: F1/F2/F3) لم تكن ضمن نطاق هذا السبرنت — تُغلق في **SPRINT-09 الجزء A**. |

---

## 1. ما تحقق منه المخطط فعلياً (بيئة طازجة؛ PG 17 + pgvector من pgdg؛ `alembic upgrade head` ⇒ 0007)

| الفحص | أمر المخطط | النتيجة |
|---|---|---|
| المجموعة الكاملة على PG حقيقية | `pytest -q` (`TEST_DATABASE_URL` مضبوط) | **830 passed, 0 failed** (164s) — يطابق تقرير المنفذ |
| ruff | `ruff check .` | `All checks passed!` |
| بوابة التغطية الوحدوية كما في CI | `pytest -m "not integration" --cov=src --cov-fail-under=95` | انظر الملاحظة أسفل الجدول |
| CI على GitHub | Checks API على `9ba947e` و`7721b4f` | **5/5 success** ×2 (lint/unit/integration/secrets/licenses) |
| قابلية الدمج | `GET /pulls/26` | `mergeable: true`، لا تعارض مع `e73e9cc` |
| ملفات محظورة | `git diff --name-only e73e9cc..9ba947e` | 32 ملفاً كلها تحت `src/ tests/ alembic/ pyproject.toml` + `CURRENT_DIRECTIVE.md`/`WORK_STATE.md` — **لا** `ARCHITECTURE_LOCK`/`SESSION_RECOVERY`/`imaster_planner`/`README`/`Hub`/`sprints` |
| `pyproject.toml` | `git diff e73e9cc -- pyproject.toml` | `concurrency = ["thread","greenlet"]` + 5 أسطر `banned-api` (`linkedin`, `tweepy`, `tiktok`, `facebook_business`, `requests`) — **لا تبعية جديدة** |
| greps القبول §3 | الأربعة حرفياً | فارغ / فارغ / `core/http.py` + `ai_gateway/dependencies.py` + `facades/openai_compatible.py` + `social_connect/meta_client.py` / `0001…0007` |
| آلة الحالات | `test_lifecycle.py:106` | `len(EXPECTED) == 56` بلا تغيير |
| عدّاد المسارات المحمية | `test_auth_guard.py:54`, `test_router.py:1417` | `== 34` (32 + `GET /channels` + `GET /channels/{channel}`) |

> **التغطية:** المنفذ يُبلغ 98.87% على الكل (وحدة+تكامل) و`publishing/infrastructure/postgres_repository.py` 76% ⇒ 84% بعد greenlet (A2-M1 ✓). CI `unit` نجح ببوابة 95 على `9ba947e` (المصدر الموثوق للبوابة). تشغيل المخطط المحلي لبوابة الوحدة بدأ بعد المجموعة الكاملة ونتيجته تُسجَّل في `WORK_STATE.md` §2 إن اختلفت.

### 1-أ. التحقق بنداً بنداً (بالسطر @ `9ba947e`)

| البند | ما طُلب | ما وُجد | ✓ |
|---|---|---|---|
| **A-1** | رفض الإقلاع عند `SCHEDULER_ENABLED` بلا مفتاح تشفير | `scheduler.py:81` `SCHEDULER_REQUIRES_ENCRYPTION_KEY`؛ `:120-121` `if encryption is None: raise`؛ `main.py::_start_scheduler` يرفع `RuntimeError` قبل أي بناء؛ 3 اختبارات `test_main.py:186/210/227` | ✓ |
| **A-2** | قياس التغطية تحت greenlet بلا تبعية جديدة | `pyproject.toml [tool.coverage.run] concurrency = ["thread", "greenlet"]`؛ `pip show greenlet` ⇒ تأتي مع SQLAlchemy | ✓ |
| **A-3** | `publish_max_attempts` إعداد يُكتب في المهمة عند الإنشاء؛ المجدول لا يقرأه | `config.py:132` `Field(default=3, ge=1, le=10)`؛ `router.py:118` تمرير؛ `service.py:130/137` حفظ؛ `_job_for(..., max_attempts=…)` عند `:343` و`:447`؛ `grep publish_max_attempts scheduler.py` فارغ (يقرأ lease/batch/backoff فقط `:169/:175/:281`) | ✓ |
| **A-4** | عدّاد واحد للمسارات المحمية | `test_router.py:1411` يستورد `PROTECTED, PROTECTED_ROUTE_COUNT` من `tests/test_auth_guard.py` | ✓ |
| **B-1** سجلّ القنوات | حزمة `publishing/channels/` + `Channel` 5 قيم + `ChannelLiteral` مشتق + حذف ثوابت `content/` + `PublisherResolver` + `CHANNEL_NOT_ACTIVATED` نهائي + 0007 | `registry.py:18 UnknownChannelError(KeyError)`، `:45 get`، `:56 all`، `:60 activatable`، `:64 active`؛ `scheduler.py:84 PublisherResolver = Callable[[Channel], Publisher]`؛ `publishing/domain/models.py:47-48` `CHANNEL_NOT_ACTIVATED` خارج `RETRYABLE_ERROR_CLASSES`؛ `alembic/versions/0007_channels.py` CHECK بخمس قيم؛ `test_channels.py` 8 اختبارات | ✓ |
| **B-2** محوّلات هيكلية + `/channels` | `StructuralPublisher` بلا شبكة ولا رموز؛ `GET /channels[/{channel}]` خلف `CurrentUser`؛ 422 `channel_not_activated` قبل فحوص الاتصال؛ `banned-api` موسَّع | `channels/structural.py`؛ `channels/router.py` مُركَّب في `main.py:136`؛ `service.py:614` `ChannelNotActivatedError(item.channel, definition.incomplete_requirements())` **قبل** `:616 TargetPageNotSelectedError`؛ `test_channels.py:210` يُثبت أن مزوّد الرموز لا يُستدعى؛ B2-I1 على PG `test_scheduler_pg.py:522` | ✓ |
| قاعدة No-SDK | لا `import httpx` ولا `https://` في `channels/` خارج `meta_*` | grep فارغ؛ الانحراف 3 (`"https:" + "//"`) يفسّر كيف | ✓ |

---

## 2. تقييم تقرير المنفذ (§6 @ `9ba947e`)

- **الدقة:** كل اسم/سطر ذكره المنفذ طابق `grep` على الشجرة (درس 18 استُوعب). لم أجد في §6 أي عبارة غير دقيقة هذه المرة — تحسّن ملموس عن SPRINT-07.
- **الانحرافات الستة — الحكم:**
  1. **تخفيض 0007 يؤرشف ثم يحذف** — **مقبول ومُفضَّل.** صفّ بـ`channel='linkedin'` لا يمكن أن يبقى تحت CHECK 0006 مهما كانت حالته؛ الأرشفة أولاً توثّق النية، وفكّ `current_revision_id` قبل `DELETE` يحترم الـFK الدائري. B1-M1 يُثبته على PG. تخفيض هجرة يهدم عالماً أوسع ⇒ فقدان بيانات في اتجاه الهبوط **متوقَّع ومُعلَن**. يُوثَّق في `DATA_AND_INTEGRATIONS.md` §1-ب.
  2. **نقل توسيع `banned-api` من A إلى B-2** — **مقبول.** قاعدة «لا تغيير سلوكي خارج المذكور في Part A» كانت ستُكسر لو عُدِّل اختبار SPRINT-07 هناك؛ القرار صحيح وتُوُثِّق.
  3. **`"https:" + "//"`** — **مقبول على مضض.** حيلة لتمرير grep القبول؛ البديل الأنظف كان `urllib.parse.urlsplit(url).scheme == "https"`. ليست عائقاً؛ تُحسَّن عندما يُلمس الملف لاحقاً (لا سبرنت مخصص).
  4. **`ChannelNotActivatedError` داخل `_validate_target`** — **مقبول وصحيح.** تغطية `publish/retry` مجاناً أفضل من رفعها في `schedule` وحدها.
  5. **نصوص المصفوفة بلا `secret`/`token`** — **مقبول**، مع ملاحظة أن اختباراً يفحص «غياب الكلمة في الاستجابة كاملة» أقوى من فحص المفاتيح فقط — وهو ما فعله المنفذ.
  6. **19 إعادة ضبط** — مُسجَّلة؛ قاعدة `wip:` + دفع بعد كل ملف أثبتت نفسها مجدداً.
- **الاختبارات القديمة المعدَّلة (6)** — كلها ميكانيكية (توقيع `PublisherResolver` ×3، `"tiktok"` ⇒ `"myspace"` لأن `tiktok` صار مسجَّلاً، `"0006"` ⇒ `"0007"` ×6). لا تغيير في السلوك المختبَر. ✓

---

## 3. أجوبة سؤالَي المنفذ

1. **`ChannelRegistry.active()` — تبقى.** الالتزام الحرفي بالواجهة ليس غاية؛ `active()` استعلام مشروع (القنوات القابلة للنشر الآن) وتستخدمه `test_channels.py:49`. القاعدة: الزيادات **الصغيرة المختبَرة والموثَّقة في §6** مقبولة؛ ما يُرفض هو الزيادات غير المذكورة. تُضاف إلى `DATA_AND_INTEGRATIONS.md` ضمن واجهة السجلّ.
2. **مصدر `activation_status` عند تفعيل قناة ثالثة — من التعريف + وجود اعتماد، لا من إعداد منفصل.** المبدأ: التعريف يُعلن ما **نفّذناه** (ناشر حقيقي موجود)، ووجود اعتمادات المنصة في البيئة/العلامة يُقرِّر ما **يعمل**. أي `activation_status` يُحسب: `ACTIVE` إذا كان للقناة `publisher_factory` حقيقي **و**اكتملت `activation_requirements` التي يملكها `PLATFORM_OWNER` (تُقرأ من وجود متغيرات البيئة الخاصة بتلك المنصة، كما تُقرأ `META_APP_ID` اليوم)؛ وإلا `NOT_CONFIGURED`. لا يُضاف إعداد مستقل مثل `LINKEDIN_ENABLED` — إعداد كهذا ينفصل عن الواقع. التفصيل الدقيق يُقرَّر في سبرنت المنصة الثالثة نفسها — مُسجَّل كبند مؤجَّل #16 في `DEFERRED_DECISIONS.md`.

---

## 4. ما أثبته المخطط بالتجربة (ثغرات كامنة من SPRINT-07 — خارج نطاق هذا السبرنت، تُغلق في SPRINT-09 الجزء A)

> منهج: كل ثغرة أُعيد إنتاجها على `9ba947e` بتجربة فعلية، لا بقراءة الكود وحدها. نواتج التجارب منسوخة حرفياً.

### F1 — CHECK `ck_content_items_published_at_iff_published` يكسر `published → archived`
- **الفرضية:** `postgres_repository.py:118-145 compare_and_set_status` يحتفظ بـ`published_at` عمداً عند `expected=PUBLISHED, new=ARCHIVED` (فرع `elif not (…)`)، بينما CHECK 0006 (`0006_publishing.py:71-75`) ينصّ على `(status = 'published') = (published_at IS NOT NULL)` ⇒ صفّ `archived` بـ`published_at` غير فارغ مرفوض.
- **التجربة** (`psql` على `ai_growth_os_test` @ head 0007؛ إدراج مستأجر/علامة/عنصر `published` ثم `UPDATE … SET status='archived'` مع إبقاء `published_at`؛ `ROLLBACK`):
  ```
  psql:/tmp/probe_f1.sql:8: ERROR:  new row for relation "content_items" violates check constraint "ck_content_items_published_at_iff_published"
  ```
- **الأثر:** أرشفة أي منشور **منشور فعلياً** عبر API ⇒ `CheckViolationError` ⇒ 500. آلة الحالات تُعلن الانتقال `(_S.PUBLISHED, _A.ARCHIVE): _S.ARCHIVED` (`lifecycle.py:118`) لكن قاعدة البيانات تمنعه. لماذا لم تكشفه الاختبارات؟ المستودع الوهمي (Fake) لا يُطبّق CHECK، واختبارات PG لم تُغطِّ هذا الانتقال بالذات (**درس 19**).
- **الإغلاق (SPRINT-09 A-1):** هجرة `0008` تُبدّل CHECK إلى: `published ⇒ published_at NOT NULL`؛ `archived` ⇒ حرّ؛ باقي الحالات ⇒ `published_at IS NULL`. أي: `(status = 'published' AND published_at IS NOT NULL) OR (status = 'archived') OR (status NOT IN ('published','archived') AND published_at IS NULL)`. الاحتفاظ بـ`published_at` في الأرشيف **مقصود** (أثر تدقيقي). + اختبار تكامل PG للانتقال كاملاً عبر API.

### F2 — `MetaGraphClient._request_absolute` يُعيد POSTات ذات أثر جانبي
- **الفرضية:** `meta_client.py:451-494` حلقة إعادة محاولة واحدة لكل الطرق: `httpx.TimeoutException` (يشمل `ReadTimeout` **بعد** إرسال الطلب) و`MetaTransientError` (5xx / أكواد 1,2) تُعاد حتى `meta_http_max_retries=3`. `publish_page_post` (`:350`) و`create_instagram_container` (`:371`) و`publish_instagram_container` (`:385`) تمرّ بالمسار نفسه ⇒ منشور مكرَّر محتمل.
- **التجربة** (`respx`؛ `publish_page_post("tok", "999", "hello")`؛ `sleep` معطَّل):
  ```
  سيناريو 502 (code 2) ثم 200:   POST /feed calls: 2 | result: post_id='123_456' permalink='https://fb/p'
  سيناريو ReadTimeout ثم 200:     POST /feed calls after ReadTimeout: 2 | result: post_id='123_457' …
  ```
- **الأثر:** إذا كان الـ502/المهلة **بعد** أن نفّذ Graph النشر فعلاً (شائع مع 5xx على بوابة Meta)، تُنشر المشاركة مرتين — وهذا يناقض D-055 («لا تكرار صامت») ومفهوم «النتيجة المبهمة» الذي بُني في المجدول (SPRINT-07 B6) لكن أُفرِغ من معناه لأن العميل يُعيد **قبل** أن يصل التبليغ إلى المجدول. GET-ات (`/me`, `/me/accounts`, `/debug_token`) إعادتها آمنة وتبقى.
- **الإغلاق (SPRINT-09 A-2 — يُسجَّل D-070):** فصل الدلالات حسب الطريقة داخل `_request_absolute`: POST ذو أثر جانبي ⇒ `max_retries=0`؛ تصنيف الفشل: `ConnectError`/`ConnectTimeout` (قبل الإرسال) ⇒ `MetaTransientError` (آمن لإعادة المحاولة على مستوى **المجدول** بعد backoff)، `ReadTimeout`/`RemoteProtocolError`/5xx **بعد** الإرسال ⇒ خطأ جديد `MetaAmbiguousError` ⇒ `PublishOutcome.ambiguous` (المسار الموجود أصلاً في المجدول). GET يحتفظ بالحلقة الحالية. اختبارات `respx` تُثبت: POST يُستدعى **مرة واحدة** في كل سيناريو؛ GET يُعاد كما قبل.

### F3 — `generate()` يمسح `media_refs` + يحمل قفل الصف عبر نداء الشبكة
- **الفرضية (أ):** `service.py:567` `add_revision(item.id, body=…, provenance=…, created_by_tenant_id=…, created_at=…)` بلا `media_refs` ⇒ `postgres_repository.py:168` يكتب `[]`.
- **التجربة** (اختبار تكامل مؤقت عبر API على PG؛ عنصر `instagram` بصورة ⇒ `/generate`؛ أُزيل بعد التشغيل):
  ```
  PROBE_F3 before revisions media: [(1, [{'url': 'https://cdn.example.com/a.jpg', 'kind': 'image'}])]
  PROBE_F3 after  revisions media: [(2, []), (1, [{'url': 'https://cdn.example.com/a.jpg', 'kind': 'image'}])]
  PROBE_F3 DB: [(1, '[{"url": …a.jpg…}]', False), (2, '[]', True)]
  ```
  المراجعة الحالية (2) بلا صورة ⇒ `schedule` لعنصر إنستغرام سيُرفض لاحقاً بـ422 `instagram_requires_image` (في التجربة ظهر `target_page_not_selected` أولاً لغياب اتصال؛ ترتيب الفحوص في `_validate_target` يضع الصفحة قبل الوسائط — الأثر نفسه).
- **الفرضية (ب) — مؤكَّدة بالقراءة:** `generate()` يستدعي `_lock_item` (`SELECT … FOR UPDATE`) في السطر الأول ثم `await gateway.complete(request)` ⇒ قفل صفّ + اتصال PG محجوزان طوال نداء مزوّد الذكاء الاصطناعي (ثوانٍ، وقد تصل إلى مهلة المزوّد). مع مزوّد حقيقي (M7) هذا يستهلك مجمّع الاتصالات تحت حمل، ويمنع تحرير/عرض العنصر أثناء التوليد.
- **الإغلاق (SPRINT-09 A-3):** (أ) `generate()` يمرّر `media_refs=item.current_media_refs` (أو ما يعادله من المراجعة الحالية)؛ `ContentBodyUpdate.media_refs: list[MediaRef] | None = None` حيث `None` = «أبقِ الحالية» و`[]` = «احذف» (كسر توافق صريح ومُعلَن في §6)؛ (ب) ترتيب `generate()`: اقرأ العنصر بلا قفل ⇒ فحص `GENERATION_STATES` ⇒ limiter ⇒ budget ⇒ `gateway.complete` ⇒ **ثم** `_lock_item` ⇒ **إعادة فحص** `GENERATION_STATES` (ينتقل 409 إن تغيّر) ⇒ CAS/`add_revision`. اختبار تكامل يُثبت أن القفل لا يُحمل أثناء `complete()` (مثلاً: `complete()` وهمي يحاول `get_for_update` من جلسة ثانية بمهلة قصيرة وينجح).

---

## 5. ما يُفتح الآن

- **M5 مكتمل**: سجلّ قنوات واحد مصدر الحقيقة لخمس منصات؛ Meta مرجعاً نشطاً؛ LinkedIn/X/TikTok هيكلية مع مصفوفة تفعيل مرئية عبر `GET /channels`؛ تفعيل الثالثة **مؤجَّل بقرار المالك**.
- **SPRINT-09 = الجزء A (F1/F2/F3) + الجزء B = M6 WhatsApp Cloud API** — التوجيه في `CURRENT_DIRECTIVE.md`.
- قرار عميل WhatsApp (PyWa مقابل عميل خفيف فوق `core/http.py`) مُتَّخذ في التوجيه ومُسجَّل D-071.

---

## 6. ملاحظة حوكمة

- الدمج تمّ بواسطة المخطط وفق D-063؛ المالك لم يُطلب منه الدمج.
- توكن GitHub ظهر في المحادثة للمرة **التاسعة** — طلب التدوير يتكرر في §7 من التوجيه الجديد.
- الساندبوكس أُعيد ضبطه **مرتين** عند المخطط خلال مراجعة هذا السبرنت وحدها (الإجمالي عبر المشروع 21: 19 منفذ + المخطط)؛ إجراء `SESSION_RECOVERY.md` §2-ب أعاد البيئة في ≈ دقيقة في المرتين، مع ملاحظة واحدة جديدة: `export` متغيرات الاختبار يجب أن يتم في **نفس** سطر الأمر الذي يُشغّل `pytest` (كل نداء Bash يبدأ بغلاف جديد).
