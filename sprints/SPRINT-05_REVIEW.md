# 📋 SPRINT-05-META-OPERATIONAL-READS — تقرير مراجعة المخطط

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-05-META-OPERATIONAL-READS` |
| **المُراجِع** | AI Master Planner |
| **تاريخ المراجعة** | 2026-10-30 |
| **الفرع المُراجَع** | `work/implementation` @ `fea00e9` (Part A `faac70a`، Part B `1b6e3d1`، وثائق `6a4c363`/`fea00e9`) فوق `main` @ `c602381` |
| **Pull Request** | [#16](https://github.com/belalalibb/ai_agancy_markting/pull/16) — 19 ملفاً، +2013/−37 |
| **الحكم النهائي** | 🟢 **مقبول — دُمج بواسطة المخطط (D-063)** ⇒ `main` @ `d026a2e`. كل بنود A-0/A-1/B1–B5 منفَّذة ومُتحقَّق منها بالسطر والتجربة؛ لا هجرة، لا تبعية جديدة، لا ملف محظور مُسّ. ملاحظتان من المالك ثبتتا (§4: O2 جزئياً، O3 كلياً) وملاحظة لم تثبت (O1) — تُغلق الثابتتان في **SPRINT-06 الجزء A**. |

---

## 1. ما تحقق منه المخطط فعلياً (بيئة طازجة؛ PostgreSQL 17 + pgvector أُعيد تثبيتهما)

| الفحص | نتيجة المخطط | ادعاء المنفذ | تطابق |
|---|---|---|---|
| CI على `fea00e9` (Checks API) | الفحوص الخمسة `success` | ✓ | ✅ |
| `ruff check` + `ruff format --check` (93 ملفاً) | نظيفان | ✓ | ✅ |
| الحزمة كاملة على PG حقيقية + تغطية | **523 passed، 0 فشل، 99.62%** (2506 عبارة، 3 غير مغطاة: `exceptions.py:118`، `postgres_repository.py:162`، `service.py:596` — كما أعلن المنفذ) في 120 ث | 523 / 99% | ✅ |
| **التقاط الطلبات الفعلية لـ B3/B4** (`respx` + مسجّل جذر على DEBUG) | `GET /123/posts?fields=…&limit=10&after=CUR1&appsecret_proof=<HMAC صحيح لتوكن الصفحة>` + `Authorization: Bearer <page token>`؛ `GET /999/media?…` كذلك؛ `limit=200` قُصّ إلى 100؛ **توكن الصفحة غير موجود في أي رابط ولا في السجل**؛ `paging.next` (يحمل `access_token`) لم يظهر في السجل ولم يُعَد؛ `_cursor()` أعاد `{'next_cursor': 'CUR2', 'has_more': True}` فقط | ✓ | ✅ |
| greps القبول | `"method": "GET"` في `meta_client.py:208` داخل `data=`؛ `_URL_SECRET_PARAMS` يحوي `input_token`/`method` (`:59`)؛ لا `params=` يحمل توكناً/سراً؛ `paging.next` في `schemas.py`/`router.py` ⇒ مطابقة واحدة **في docstring** (`router.py:21`) لا كود | ✓ | ✅ |
| الاختبارات بالأسماء | A0-U1..U4، A1-I1/I2، B1-U1، B2-U1..U4، B2-I1/I2، B3-U1..U7، B3-I1، B4-U1..U4، B4-I1 — **26/26 موجودة وناجحة** | ✓ | ✅ |
| هجرات / تبعيات / ملفات محظورة | `alembic/versions/` = 0001–0004؛ `pyproject.toml` بلا تغيير؛ لا ملف محظور | ✓ | ✅ |

### 1-أ. التحقق بنداً بنداً (بالسطر @ `fea00e9`)

| # | البند | ما قرأه المخطط | الحكم |
|---|---|---|---|
| **A-0** | `method=GET` + إنهاء الابتلاع الصامت | `meta_client.py:208` `data={"input_token": …, "method": "GET"}` + `headers=_bearer(app_token)`؛ `service.py:510-518` WARNING لـ`MetaNetworkError/MetaTransientError/MetaRateLimitError`؛ `:519-528` `counters.increment("meta_debug_token_failures")` + `logger.error(...)` لأي `MetaGraphError` آخر؛ `src/core/metrics.py` عدّاد آمن للخيوط بلا تبعية | ✅ |
| **A-1** | هوية مختلفة = اتصال جديد؛ نفس الهوية من مستأجر آخر = نقل ملكية مع حفظ الاختيار | `test_postgres_persistence.py:1531` (A1-I1) و`:1578` (A1-I2) على PG | ✅ |
| **B1** | `token_health` محسوب | `schemas.py:29 compute_token_health` بالأولوية المطلوبة؛ `from_domain(…, now=…)` قابل للحقن؛ `last_synced_at` مُعرَّض | ✅ |
| **B2** | `resync` مالك الاتصال فقط؛ دمج يحفظ الاختيار ويستبدل التوكن؛ `removed_selected_page_ids` | `service.py:315-398`: ترتيب الحراسات (404 ⇒ 403 ⇒ 409 `REVOKED`) **قبل** أي نداء Graph؛ `del user_token` في `finally`؛ `_apply_selection_policy` يعيد استخدام B6؛ `update_token_metadata` (`postgres_repository.py:137`) لا يمسّ التوكن ولا الحالة | ✅ (مع O2 أدناه) |
| **B3/B4** | قراءة الصفحات المختارة فقط، Bearer+proof، لا `paging.next` | `service.py:572 _selected_page` (404 موحَّد لكل الأدوار)؛ `:466-481 _read_with_page_token` حراسات الحالة قبل فكّ التشفير، 190 ⇒ `mark_out_of_band`؛ `:586 _cursor` | ✅ |
| **B5** | التوكن لا يتجاوز نطاق الاستدعاء؛ التنقية | `del page_token` في `finally`؛ `SecretRedactingFilter` باقٍ على الجذر؛ التجربة أعلاه أكدت غياب التوكن من السجل | ✅ |

---

## 2. تقييم تقرير المنفذ (§6 @ `fea00e9`)

دقيق ومطابق للكود في كل ما تحققتُ منه. الانحرافات السبعة مقبولة؛ تعليقان:
- **الانحراف 1** (`_to_http(operational_read=True)` يعطي `reconnect` لـ`MetaPermissionError` في B3/B4 بينما المسارات القديمة تُبقي `reauthorize_with_additional_scopes`): مقبول مؤقتاً — يُوحَّد في SPRINT-06 (انظر §3 س1).
- **الانحراف 2** (صياغة `graph code %s` لتفادي حجب `code=` بمرشّح التنقيح): ملاحظة ذكية وصحيحة؛ تُسجَّل درساً في `KNOWN_ISSUES`.

---

## 3. أجوبة أسئلة المنفذ الثلاثة

1. **توحيد `required_action` لـ`MetaPermissionError` إلى `reconnect` في كل المسارات؟** — **نعم، في SPRINT-06 الجزء A-2.** `reauthorize_with_additional_scopes` كان دقيقاً حين كان الخطأ ينشأ من نقص نطاقات أثناء الربط؛ اليوم كل مسارات Meta تعيد الإجراء نفسه عملياً (إعادة OAuth)، وتعدد القيم يعقّد الواجهة بلا فائدة. القيمة الموحَّدة `reconnect`؛ تُحدَّث اختبارات `test_router.py:402, 1005`.
2. **B2 عند تدهور `debug_token` (WARNING): حفظ القيم القديمة وتحديث `last_synced_at` — مقبول أم رفض المزامنة؟** — **مقبول مع تعديل واحد يفرضه O3 (§4):** التدهور الشبكي/العابر يُبقي القيم القديمة (المزامنة حققت هدفها الأهم: تجديد توكنات الصفحات)؛ لكن نجاح `debug_token` بنطاقات **فارغة** يجب أن يُكتب فعلاً كـ`[]` لا أن يُعامَل كـ«مجهول». التمييز يُنفَّذ في A-1 من SPRINT-06.
3. **`counters` داخل العملية — `/metrics`؟** — **ليس الآن.** يُسجَّل بنداً مؤجَّلاً (#10): تصدير Prometheus يأتي مع أول بيئة تشغيل حقيقية (SPRINT-09 جاهزية التشغيل)، لا قبلها. العدّاد الحالي يكفي للاختبارات والسجلات.

---

## 4. تحقق المخطط من ملاحظات المالك الثلاث (بالملف والسطر والتجربة — لا على الثقة)

### O1 — `full_picture` / `status_type` على `/{page_id}/posts` في v26.0 — **لم تثبت** ✅ الحقول صالحة
- قرأتُ مرجع **Graph API Reference v26.0: Post** الرسمي (بعرض JavaScript). جدول الحقول القابلة للقراءة يحوي حرفياً: `full_picture` — *"If the photo's largest dimension exceeds 720 pixels, it is resized, with the largest dimension set to 720."*؛ `status_type` — *"Description of the type of a status update."*؛ وكذلك `message`, `created_time`, `permalink_url`, `is_published`. **لا علامة إهمال على أيٍّ منها.**
- ما أوقفته Meta فعلاً في **v3.3** على `/page/posts` هو: `caption`, `description`, `link`, `name`, `object_id`, `picture`, `source`, `type` (ملاحظة مرجع Post: *"The following fields for /page/feed, /page/posts … were deprecated in v3.3"*) لصالح `attachments{…}`. **لا شيء منها في `POST_FIELDS`** (`meta_client.py:63`). الملاحظة خلط مفهوم مع هذه الحزمة.
- **إجراء وقائي صغير (SPRINT-06 A-3):** `status_type` حقل تراثي «وصفي» قد يعود فارغاً للمنشورات الحديثة؛ يُضاف `attachments{media_type,type,url,media{image{src}}}` كمصدر أغنى للوسائط مع إبقاء `full_picture`، وتُثبَّت بقية الحقول في اختبار عقد يقارنها بقائمة مسموح بها مشتقة من المرجع.

### O2 — `resync()` على اتصال `REAUTH_REQUIRED` — **ثبتت جزئياً** ⚠️
- **التجربة** (`service.resync` عبر `FakeStore` و`respx`؛ `/debug_token` يعيد `is_valid=true`، `/me/accounts` يعيد الصفحة بتوكن جديد): النتيجة `OK status=reauth_required … removed=[]` — أي أن المزامنة **تنجح بـ200 وتجدد توكنات الصفحات وتبقي الحالة `reauth_required`**، فتظل `/posts` و`/media` محجوبة بـ409 رغم أن Meta أكد صلاحية التوكن للتو. (`service.py:331-339` لا تفحص `REAUTH_REQUIRED`؛ docstring `:331-332` يصرّح «لا تُنعَش إلا بـOAuth».)
- **التقييم:** السلوك **متعمَّد ومُعلَن** لكنه **غير متسق** من زاوية المنتج: إمّا أن `REAUTH_REQUIRED` يعني «التوكن ميت» فلا معنى لمحاولة المزامنة به (⇒ 409 قبل Graph، متسق مع B3/B4)، أو أن المزامنة الناجحة دليل حياة فتعيد `ACTIVE`. الحالة الحالية (نجاح + بقاء الحجب) تربك العميل.
- **القرار (SPRINT-06 A-1):** الخيار المحافظ المتسق مع H3 وD-055: **`resync` على `REAUTH_REQUIRED` ⇒ 409 `reauth_required` بلا أي نداء Graph** (كما B3/B4). التعافي الوحيد = إعادة OAuth. السبب: علامة `reauth_required` تُكتب عند **دليل قاطع** (190 من Meta)؛ عكسها بناءً على `debug_token` وحده يفتح نافذة لتوكن أُبطل جزئياً (صفحات) ولا يزال `is_valid` على مستوى المستخدم.

### O3 — `debug is None` مقابل `debug.scopes == []` — **ثبتت كلياً** 🔴
- **الكود** `service.py:379`: `granted_scopes=list(debug.scopes) if debug and debug.scopes else None` — القيمة `None` (= «لا تحدّث») تُنتَج في **حالتين مختلفتين جوهرياً**: فشل `debug_token` (مجهول — الإبقاء صحيح) **ونجاحه مع `scopes=[]`** (حقيقة مؤكدة: المستخدم سحب كل الصلاحيات — يجب كتابتها).
- **التجربة:** اتصال بـ`granted_scopes=['pages_show_list','pages_read_engagement']` ⇒ `debug_token` يعيد `is_valid=true, scopes=[]` ⇒ بعد `resync`: `scopes=['pages_show_list','pages_read_engagement']` **لم تتغير**. وفي حالة كود 100 (⇒ `None`) النتيجة نفسها. السلوكان متطابقان رغم اختلاف المعنى — ثبتت الملاحظة حرفياً.
- **الأثر:** `token_health` و`granted_scopes` المعروضان يكذبان بعد سحب الصلاحيات حتى يفشل نداء فعلي. ليس P0 (الفشل سيظهر عند أول قراءة) لكنه يخالف D-055 (لا نزعم ما لا نعرفه).
- **القرار (SPRINT-06 A-1):** `granted_scopes = list(debug.scopes) if debug is not None else None` (الفارغة تُكتب)؛ اختبار يفرّق الحالتين صراحةً. ويمتد التصحيح إلى `complete_connection` (`service.py:158`: `granted_scopes = list(debug.scopes) if debug else []` — هنا الخلط معكوس: الفشل يُكتب `[]` كأنه حقيقة؛ يصبح `None`… لكن العمود `NOT NULL` ⇒ يبقى `[]` مع تسجيل `scopes_known=False` في الاستجابة عبر حقل محسوب `scopes_verified: bool` = `debug is not None`). التفصيل في التوجيه.

---

## 5. القرار

🟢 **PR #16 مقبول ودُمج بواسطة المخطط** (`d026a2e`). المتابعات (في PR المخطط هذا):
- `KNOWN_ISSUES.md`: درس «القيمة الفارغة ≠ المجهولة» (O3) ودرس «مرشّح التنقيح يحجب `code=`» (انحراف 2).
- `DEFERRED_DECISIONS.md` #10: تصدير المقاييس.
- `SECURITY_BASELINE.md` §2: صف `debug_token` ⇒ ✅؛ صف جديد للقراءات التشغيلية.
- `DATA_AND_INTEGRATIONS.md`: نقاط Graph الجديدة.
- `WORK_STATE.md`: خريطة المحطات حتى النسخة التشغيلية الأولى (طلب المالك) + `SPRINT-06` في `CURRENT_DIRECTIVE.md`.

## 6. ملاحظات للمنفذ (لا تؤثر على القبول)

1. الانقطاعات الأربعة وضياع عمل غير مدفوع مرتين (انحراف 7): الالتزام بـ«ادفع بعد كل ملف» (كما يفعل المخطط) لا بعد كل بند — يُدرج صراحةً في §5 من التوجيه القادم.
2. `tests/fakes.py:FakeStatusMarker(store)` تتطلب `FakeStore` مشتركاً مع الـrepo — سلوك صحيح لكنه غير موثَّق في docstring؛ أضف سطراً.
