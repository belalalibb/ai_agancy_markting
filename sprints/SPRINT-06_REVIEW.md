# 📋 SPRINT-06-CONTENT-CORE-AND-APPROVAL — تقرير مراجعة المخطط

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-06-CONTENT-CORE-AND-APPROVAL` (المحطة M3) |
| **المُراجِع** | AI Master Planner |
| **تاريخ المراجعة** | 2026-10-30 |
| **الفرع المُراجَع** | `work/implementation` @ `b9221fb` (Part A `0131458`، B-1 `b95649a`، B-2 `6a724ce`، وثائق `b9221fb`) فوق `main` @ `e3db639` |
| **Pull Request** | [#19](https://github.com/belalalibb/ai_agancy_markting/pull/19) |
| **الحكم النهائي** | 🟢 **مقبول — دُمج بواسطة المخطط (D-063)**. كل بنود A-1/A-2/A-3 وB1–B5 منفَّذة ومُتحقَّق منها بالسطر والتجربة على PostgreSQL 17 حقيقية؛ هجرة واحدة (0005)، لا تبعية جديدة، لا ملف محظور مُسّ. **ملاحظات المالك الأربع ثبتت كلها** (§4) — ليست عوائق دمج (لا تسرّب سرّ، لا كسر عزل مستأجر، لا فقدان بيانات) لكنها عيوب حقيقية تُغلق **إلزامياً في SPRINT-07 الجزء A** قبل أي بناء فوق وحدة المحتوى. |

---

## 1. ما تحقق منه المخطط فعلياً (بيئة طازجة؛ PostgreSQL 17 + pgvector أُعيد تثبيتهما)

| الفحص | نتيجة المخطط | ادعاء المنفذ | تطابق |
|---|---|---|---|
| CI على `b9221fb` (Checks API) | `unit` / `integration` / `lint` / `secrets` / `licenses` كلها `success` (×2: push + PR) | «تُتحقَّق بعد الدفع» | ✅ |
| `ruff check` + `ruff format --check` على `src tests alembic scripts` | نظيفان (117 ملفاً) | ✓ | ✅ |
| الحزمة كاملة على PG حقيقية + تغطية | **645 passed، 0 فشل، 99.12%** (3318 عبارة، 18 غير مغطاة) في 184 ث | 645 / 99.12% | ✅ |
| فجوات التغطية | `content/infrastructure/postgres_repository.py` 84% (أسطر 53-54, 62, 87, 110, 119, 140, 151, 168, 178 — فروع «لا صف»)؛ `content/service.py` 96% (185, 244→246, 264, 374) | لم تُذكر | ⚠️ مقبولة؛ تُغلق جزئياً مع إصلاحات §4 |
| greps القبول | `if debug and debug.scopes` ⇒ 0؛ `reauthorize_with_additional_scopes` ⇒ 0؛ `alembic/versions/` = 0001–0005؛ `git diff e3db639 -- pyproject.toml` فارغ | ✓ | ✅ |
| الاختبارات بالأسماء | A1-U1..U4، A1-I1، A2-U1، A3-U1، B1-M1، B2-U1 (**42 خلية `test_b2_u1_cell[<state>--<action>]`**)، B2-U2..U4، B2-I1، B3-U1..U7، B3-I1، B4-U1..U3، B5-U1..U4، B5-I1 — **29/29 موجودة وناجحة** | ✓ | ✅ |
| **تجربة سباق المراجعات (O3)** | سكربت مستقل بـ`asyncio.Barrier(2)` وجلستين على PG: `next_revision_no` ⇒ كلاهما `1` ⇒ `add_revision` ⇒ **`[IntegrityError: uq_content_revisions_item_no, ok]` في 3/3 محاولات** | — | 🔴 ثبتت O3 (§4) |
| **تجربة المخططات (O1/O2/O4)** | `ContentItemCreate(channel="instagram", body="x"*2201)` ⇒ مقبول؛ `ContentRevisionPublic.provenance.keys()` ⇒ `completion_tokens, generated_at, kind, latency_ms, model, prompt_hash, prompt_tokens, provider`؛ `ContentItemDetail.model_dump(exclude_none=True)` يُسقط `current_body, current_revision_id, generation_log, scheduled_for, target_page_id` | — | 🔴 ثبتت O1/O2/O4 (§4) |

### 1-أ. التحقق بنداً بنداً (بالسطر @ `b9221fb`)

| # | البند | ما قرأه المخطط | الحكم |
|---|---|---|---|
| **A-1** | `[]` المؤكَّدة تُكتب؛ `scopes_verified`؛ resync على `reauth_required` ⇒ 409 بلا Graph | `social_connect/service.py:390` `list(debug.scopes) if debug is not None else None`؛ `:182/212` `scopes_verified = debug is not None`؛ `:351` `raise ReauthRequiredError()` قبل فكّ التوكن؛ عمود في `0005_content.py` مع backfill؛ `ConnectedAccountPublic.scopes_verified: bool` | ✅ |
| **A-2** | `required_action=reconnect` موحَّد؛ 502 ثابت | `router.py::_required_action`؛ `_to_http(exc)` بلا `operational_read`؛ `test_a2_u1_required_action_reconnect_is_uniform_across_meta_routes` على 4 مسارات | ✅ |
| **A-3** | `attachments{…}` + اختبار عقد v26.0 | `meta_client.py::POST_FIELDS`؛ `PostAttachmentPublic` (`extra="ignore"`، تسطيح `media.image.src`)؛ `test_a3_u1_post_fields_contract_against_v26_reference` | ✅ |
| **B1** | هجرة 0005 بـCHECKs وFK دائري | `0005_content.py:140` `UniqueConstraint(item_id, revision_no)`؛ `ck_content_revisions_body_length` (5000)؛ `ck_content_items_scheduled_for_iff_scheduled`؛ `ck_content_items_status`؛ `ck_approval_events_action`؛ FK `current_revision_id` يُضاف بعد الجدولين ويُسقَط أولاً | ✅ |
| **B2** | آلة حالات 42 خلية؛ موافقة المالك فقط | `domain/lifecycle.py:55 TRANSITIONS` + `:105` حارس `6×7==42`؛ `OWNER_ONLY={approve,reject}`؛ `test_lifecycle.py:26 EXPECTED` صريحة | ✅ |
| **B3** | 12 مساراً؛ CAS `rowcount==1` ⇒ 409 | `infrastructure/postgres_repository.py:96-110` `UPDATE … WHERE id AND status=:expected`؛ B3-I1 على PG `[200,409]` حتمياً | ✅ (مع O1/O4 أدناه) |
| **B4** | `AIGateway` Protocol + Fake + HTTP OpenAI-compatible؛ Bearer فقط | `ai_gateway/providers.py`: `POST {base}/chat/completions`، Bearer في الترويسة، مهلة 30s، إعادة واحدة على 5xx/timeout، 429 ⇒ `AIRateLimitedError`؛ `sk-[A-Za-z0-9]{8,}` في `core/logging.py`؛ B4-U1 يلتقط الطلب: المفتاح ليس في الرابط ولا `params` ولا `caplog` | ✅ |
| **B5** | `/generate` بـ`provenance`؛ حدّ 20/ساعة | `content/service.py:345-375`: قصّ `result.text` إلى `CHANNEL_LIMITS[item.channel]`؛ `provenance` كامل؛ CAS `rejected→draft`؛ سجل INFO بلا نص؛ `counters`؛ `limiter.hit("ai_generate_brand", brand_id, 20, 3600)` | ✅ (مع O2/O3 أدناه) |

---

## 2. تقييم تقرير المنفذ (§6 @ `b9221fb`)

دقيق ومطابق للكود في كل ما تحققتُ منه. الانحرافات العشرة مقبولة؛ تعليقات:
- **الانحراف 4** (`generation_log` يُحذف للـVIEWER عبر `response_model_exclude_none` + `None`؛ «المراجعات نفسها بما فيها `provenance.kind` تبقى مرئية للجميع»): الوصف صادق لكنه **يكشف العيب O1/O4 بنفسه** — `provenance` كاملاً (لا `kind` وحده) يصل للـVIEWER، و`exclude_none` أداة فجّة. يُصلح في SPRINT-07 A-1/A-4.
- **الانحراف 5** (`archive` للـMANAGER على كل عناصره لا مسوداته فقط): تفسير أوسع لكنه متسق ومحكوم بـ`created_by_tenant_id = actor`؛ **يُعتمد** ويُثبَّت في جدول الرؤية.
- **الانحراف 7** (`/generate` لا يمسّ الهدف؛ `schedule` يقبل `target_page_id`): صحيح — الهدف قرار نشر لا قرار صياغة.
- **الانحراف 10** (ساندبوكس ×6؛ خسارة واحدة أُعيد بناؤها): الدفع بعد كل نقطة ما زال القاعدة المنقذة.

---

## 3. أجوبة أسئلة المنفذ الثلاثة

1. **كشف `counters` في `/metrics` الآن؟** — **لا؛ يبقى البند المؤجَّل #10 حتى M9.** السبب لم يتغيّر: لا Prometheus في المكدس ولا بيئة تشغيل حقيقية؛ العدّادات تؤدي دورها في الاختبارات والسجلات. **إضافة صغيرة مطلوبة في SPRINT-07:** الحقول الخاصة التي تُسحب من `provenance` العام (O1) تُجمَّع لاحقاً في «سجل تكلفة النماذج» (M7)، فلا تُسقط من قاعدة البيانات — تُخفى من الاستجابة فقط.
2. **`unschedule` ⇒ `approved` أم `draft`؟** — **`approved` (كما نُفِّذ) يُعتمد.** المبدأ: **الموافقة تخصّ المراجعة (النص) لا الوقت**؛ إلغاء الجدولة لا يغيّر النص، فسحب الموافقة بلا سبب يُنهك المالك بموافقات مكررة ويخالف D-055 الذي يطلب موافقة بشرية على **الفعل الخارجي** (النشر) لا على كل تعديل إداري. الضمانة القائمة كافية: أي تعديل للنص بعد `draft` ينشئ مراجعة جديدة ويعيد الحالة إلى `draft` مع حدث `withdrawn` تلقائي (B2-U2)، و`schedule` يعيد التحقق من الهدف والاتصال كل مرة (B2-U4). يُسجَّل بنداً مؤجَّلاً (#11) بشرط إعادة فتح واحد: ظهور متطلّب تنظيمي/عميل يربط الموافقة بالموعد.
3. **حدّ الجيل 20/ساعة في الذاكرة لكل عملية حتى Redis (M9)؟** — **مقبول**، بالقيود نفسها المسجَّلة للبند المؤجَّل #7: نسخة واحدة من التطبيق حتى M9؛ الحدّ ثابت باسم (`GENERATION_LIMIT_PER_HOUR`) وقابل للنقل خلف `RateLimiter` Protocol بلا لمس الخدمة. **شرط:** لا يُعتمد عليه كحماية تكلفة وحيدة بعد أول مزوّد حقيقي — SPRINT-07 يضيف حدّاً **بالرموز لكل علامة/يوم** محسوباً من `provenance` (انظر §5).

---

## 4. تحقق المخطط من ملاحظات المالك الأربع (بالملف والسطر والتجربة — لا على الثقة)

### O1 — `generation_log` يُخفى عن VIEWER لكن `provenance` في `revisions[]` يكشف المحتوى نفسه — **ثبتت كلياً** 🔴
- `src/modules/content/schemas.py:72-84` — `ContentRevisionPublic.provenance: dict[str, Any]` و`from_domain` = `revision.model_dump(exclude={"item_id"})` ⇒ القاموس يمرّ **كاملاً**.
- `src/modules/content/service.py:345-375` — القاموس المكتوب يحوي `provider, model, prompt_hash, generated_at, prompt_tokens, completion_tokens, latency_ms`.
- `src/modules/content/router.py:230-245` — `generation_log` **مشتق** من `provenance` نفسه (يحذف `kind` ويضيف `revision_no`) ثم يُجعل `None` للـVIEWER؛ لكن `revisions=[ContentRevisionPublic.from_domain(r) …]` في السطر التالي يُعاد لكل الأدوار.
- **تجربة:** `ContentRevisionPublic.from_domain(rev).provenance.keys()` ⇒ `['completion_tokens','generated_at','kind','latency_ms','model','prompt_hash','prompt_tokens','provider']`.
- **الأثر:** إخفاء وهمي؛ جدول الرؤية §2-0 (D-064) مخروق للحقول التشغيلية/التكلفية (`provider/model/tokens/latency`) التي صنّفها التوجيه OWNER/MANAGER فقط. ليس تسرّب سرّ (لا مفتاح ولا نص برومبت) لكنه خرق عقد رؤية.
- **الإصلاح (SPRINT-07 A-1):** فصل `provenance` إلى **عام** `{kind, generated_at, prompt_hash?}` يُعاد في `ContentRevisionPublic`، و**خاص** (`provider, model, prompt_tokens, completion_tokens, latency_ms`) يُعاد فقط في `generation_log` لـOWNER/MANAGER. التخزين لا يتغيّر (قاموس JSONB واحد)؛ الفصل في طبقة المخططات بـ`PUBLIC_PROVENANCE_KEYS` ثابت + اختبار حارس يفشل إن ظهر مفتاح خاص في استجابة VIEWER.

### O2 — `ContentItemCreate`/`ContentBodyUpdate` تقبلان 5000 حرفاً لـ`instagram` رغم حدّ 2200 — **ثبتت كلياً** 🔴
- `src/modules/content/schemas.py:27-38` — `body: str = Field(min_length=1, max_length=5000)` في المخططين بلا نظر إلى `channel`.
- `src/modules/content/domain/models.py:53` — `ContentRevision.body` كذلك 5000.
- `src/modules/content/prompts.py:18` — `CHANNEL_LIMITS = {instagram: 2200, facebook_page: 5000}` يُستخدم في `/generate` فقط (`service.py:347` قصّ).
- **تجربة:** `ContentItemCreate(channel="instagram", body="x"*2201)` ⇒ مقبول (2201).
- **الأثر:** مسودة إنستغرام بـ2201–5000 حرفاً تمرّ كل الدورة حتى `scheduled` ثم تفشل في النشر (M4) — فشل متأخر مكلف بدل 422 مبكر. `ContentBodyUpdate` أسوأ لأنه لا يعرف القناة أصلاً.
- **الإصلاح (SPRINT-07 A-2):** `CHANNEL_LIMITS` ينتقل إلى `domain/models.py` (مصدر واحد)؛ التحقق في **الخدمة** (`create_item`/`update_body`/`generate`) عبر دالة واحدة `enforce_channel_limit(channel, body)` ⇒ `BodyTooLongError` ⇒ 422 `{"error":"body_too_long","channel":…,"limit":…,"length":…}`؛ المخططات تُبقي 5000 سقفاً عاماً فقط. (لا يُنقل إلى validator في `ContentItemCreate` لأن `ContentBodyUpdate` لا يحمل القناة.)

### O3 — `next_revision_no = max+1` بلا حماية تزامن ⇒ `IntegrityError` 500 — **ثبتت كلياً بالتجربة** 🔴
- `src/modules/content/infrastructure/postgres_repository.py:113-119` — `select(func.max(revision_no)).where(item_id)` ⇒ `int(current or 0)+1`؛ `:121-140 add_revision` ⇒ `session.add` + `flush` بلا معالجة `IntegrityError`؛ `grep IntegrityError src/modules/content/` ⇒ **0**.
- `alembic/versions/0005_content.py:140` — `UniqueConstraint("item_id","revision_no", name="uq_content_revisions_item_no")`.
- `src/modules/content/service.py:364` — `revision_no=await self._repo.next_revision_no(item.id)` ثم `add_revision` (نفس النمط في `update_body` و`generate`).
- **تجربة على PG 17 (3/3):** جلستان، `asyncio.Barrier(2)` بين `next_revision_no` و`add_revision` ⇒ `[('A', 1, 'IntegrityError:uq_content_revisions_item_no'), ('B', 1, 'ok')]`. الخطأ غير مُلتقَط في الخدمة ولا الموجّه ⇒ 500 (لا معالج عام لـ`IntegrityError` في `src/main.py`).
- **الأثر:** تحرير + توليد متزامنان على العنصر نفسه (مستخدمان أو تبويبان) ⇒ 500 للخاسر. CAS الحالي يحمي **حالة** العنصر (B3-I1) لا **ترقيم** مراجعاته.
- **الإصلاح (SPRINT-07 A-3):** قفل صف العنصر `SELECT … FOR UPDATE` في `get_item_for_update(item_id)` داخل معاملة إنشاء المراجعة (العنصر هو مجمّع المراجعات؛ القفل على مستواه هو التسلسل الطبيعي ويُلغي الحاجة لـ`max+1` منفصلاً: `revision_no` يُحسب من الصف المقفول). بديل مرفوض: التقاط `IntegrityError` وإعادة المحاولة — يعقّد الخدمة ويترك نافذة سباق في CAS الحالة الذي يلي الإدراج. اختبار تكامل A3-I1 يعيد تجربة المخطط ويتوقع `[201, 201]` بأرقام `2, 3` (أو `[201, 409]` إن تعارض CAS الحالة — مقبول كلاهما لا 500).

### O4 — N+1 في `list_items` + `response_model_exclude_none` يُسقط حقولاً مشروعة — **ثبتت كلياً** 🔴
- `src/modules/content/router.py:152` — `_public()` ⇒ `await service.current_body(item)`؛ `service.py:134-136` ⇒ `list_revisions(item.id, limit=1)` ⇒ **استعلام لكل عنصر**؛ `router.py:206` `items=[await _public(service, i) for i in items]` ⇒ صفحة من 50 عنصراً = 51 استعلاماً.
- `src/modules/content/router.py:216` — `response_model_exclude_none=True` على `get_item` لإخفاء `generation_log` للـVIEWER.
- **تجربة:** `ContentItemDetail(...).model_dump(exclude_none=True)` يُسقط `current_body, current_revision_id, generation_log, scheduled_for, target_page_id` ⇒ عميل الواجهة لا يميّز «حقل غير موجود» من «قيمة `None`» في مسودة بلا هدف/موعد؛ مخالفة صريحة لدرس KNOWN_ISSUES 9 (فارغ ≠ مجهول) على مستوى العقد.
- **الإصلاح (SPRINT-07 A-4):** (أ) استعلام واحد: `list_items` يُرجع `current_body` عبر `LEFT JOIN content_revisions ON id = content_items.current_revision_id` (Protocol يعيد `list[tuple[ContentItem, str | None]]` أو حقل `current_body` محسوباً في الصف)؛ `current_body(item)` تُحذف من الخدمة. (ب) حذف `response_model_exclude_none`؛ VIEWER يستقبل `generation_log: []`؟ **لا** — ذلك يخلط «لا توليد» بـ«غير مصرَّح»؛ الصحيح `ContentItemDetail` بلا الحقل أصلاً + نموذج `ContentItemDetailWithUsage(ContentItemDetail)` يحمل `generation_log: list[...]` ويُعاد لـOWNER/MANAGER (`response_model=Union[...]` أو `JSONResponse` صريحة). اختبار حارس: استجابة VIEWER تحوي `target_page_id: null` و`scheduled_for: null` ولا تحوي مفتاح `generation_log`.

**الخلاصة:** الملاحظات الأربع صحيحة بالملف والسطر والتجربة؛ لا واحدة منها تمنع الدمج (لا سرّ، لا عزل مستأجر، لا فقدان بيانات)، وكلها تُغلق في SPRINT-07 الجزء A **قبل** بناء الجدولة فوق الوحدة.

---

## 5. ملاحظات المخطط الإضافية (ليست عوائق)

1. **حماية التكلفة بعد أول مزوّد حقيقي:** حدّ 20 طلباً/ساعة لا يحدّ الرموز (طلب بـ`max_tokens=2000` ×20 = 40k رمز/ساعة/علامة). SPRINT-07 A-5: ميزانية يومية بالرموز لكل علامة من مجموع `prompt_tokens+completion_tokens` في `provenance` (استعلام واحد على JSONB؛ لا جدول جديد) ⇒ 429 `generation_budget_exceeded`. هذا أيضاً أول «سجل تكلفة» يُستفاد منه في M7.
2. **`prompt_hash` بلا نسخة برومبت:** `sha256(system+user)[:16]` يتغيّر مع أي تعديل في `prompts.py` فيستحيل مقارنة جودة النسخ لاحقاً. SPRINT-07 يضيف `prompt_version: "content_draft@1"` إلى `provenance` (حقل عام) — تمهيد لحلقة التعلّم (§6 الرأي المعماري).
3. **`archive` من `scheduled`:** مسموح في المصفوفة؛ عند M4 يجب أن يُلغي مهمة الجدولة أيضاً — يُسجَّل في توجيه M4 لا الآن.

---

## 6. ملاحظة حوكمة

التوكن ظهر في المحادثة للمرة السابعة — يبقى طلب التدوير قائماً. `main` ما زال بلا حماية فروع؛ الدمج يتم بالمراجعة المستقلة فقط (D-063).
