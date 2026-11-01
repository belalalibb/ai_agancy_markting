# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-09 رُوجع مستقلاً على PostgreSQL 17 و**قُبل ودُمج** بواسطة المخطط (`main` @ `e1e2fca`؛ التقرير في [`sprints/SPRINT-09_REVIEW.md`](./sprints/SPRINT-09_REVIEW.md)). هذا التوجيه = **SPRINT-10**: الجزء A يُغلق خمس ثغرات **مُثبَتة على PG** في كود M6 (N1–N5) + جوابَي المنفذ؛ الجزء B يفتح **M7 — Brand Brain**. الجزء A إلزامي قبل أي سطر من B.

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-10-BRAND-BRAIN` |
| **النوع** | Part A: إغلاق N1–N5 (D-072 «ثبّت ثم أبلغ» · إعادة الربط/التضارب · نطاق العلامة + رتابة الحالات · تماثل الميزانية · تصلّب الـwebhook + فهرس المسودة المعلّقة) + A-6 (wamid خاص، `language_detected`) · Part B: **M7** — `brand_profiles` + `brand_evidence` بثلاث درجات (D-074) + مراقبة ≤ 3 منافسين من مصادر عامة + توصيات/خطة محتوى كمسودات تحت الموافقة + المزوّد الحقيقي الأول عبر `openai_compatible` + سجل تكلفة |
| **الحالة** | `ISSUED` — جاهز للتنفيذ فور دمج هذا التوجيه في `main` (المخطط يدمجه بنفسه — D-063) |
| **يبدأ من** | `main` @ `d3d52b4` (دمج PR #30 — هذا التوجيه). أول أمر: `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` |
| **الفرع المستهدف** | `work/implementation` ⇒ PR إلى `main` بعنوان `SPRINT-10: Brand Brain (M7) + M6 transactional/isolation fixes (N1–N5)` |
| **الكوميتات** | **أربعة**: Part A (N1–N5 + A-6 + هجرة 0010) ⇒ B-1 (ملف العلامة + الأدلة + هجرة 0011 + `ai_gateway/budget.py` المشترك يُستكمل) ⇒ B-2 (مصادر عامة + مراقبة المنافسين + المزوّد الحقيقي + سجل التكلفة) ⇒ B-3 (التشخيص + التوصيات ⇒ مسودات تحت الموافقة). كل كوميت يمرّ الحزمة كاملة وحده على PG 17 |
| **تبعيات جديدة** | **لا شيء** في التشغيل. `httpx` عبر `core/http.py` فقط (قراءة الصفحات العامة في B-2 تمرّ بعميل واحد جديد `brand_brain/public_fetch.py` يُضاف إلى استثناءات `TID251` **ويُقيَّد** بـ`robots.txt` وحدّ حجم وقائمة مضيفين مسموحة). **ممنوع:** `beautifulsoup4`/`scrapy`/`playwright`/`selenium`/`instaloader`/أي SDK منصة (أضفها إلى `banned-api`). تحليل HTML بـ`html.parser` من المكتبة القياسية فقط |
| **الحجم المتوقع** | Part A ~450 سطراً كود + ~700 اختبارات · Part B ~1600 سطر كود + ~1600 اختبارات |
| **تاريخ الإصدار** | 2026-10-31 |

---

## 0. لماذا؟ (ويضمّ أجوبة المخطط على استشارة المالك الاستراتيجية S1–S3)

### 0-أ. لماذا الجزء A أولاً

1. **N1 — الكتابة قبل الرفع تُمحى (مُثبَت على PG، `sprints/SPRINT-09_REVIEW.md` §4-N1):** `get_db_session` يُرجِع عند أي استثناء. `approve` على نافذة مغلقة ⇒ 422 **والمسودة تعود `suggested` والإشارة تختفي**؛ 190 من Meta ⇒ 409 **والحساب يبقى `active`** فيعيد ضرب Meta بتوكن ميت؛ 503/429 ⇒ **لا صفّ `failed`**. تقرير المنفذ قال العكس لأن الـFake repos عمياء عن المعاملات (درس 22). قرار **D-072**.
2. **N2:** بعد `DELETE /account` لا يمكن `POST /account` (409 على صف `disabled`) ⇒ تدوير التوكن مستحيل؛ تضارب `phone_number_id` ⇒ `IntegrityError` خام ⇒ 500.
3. **N3 — عزل:** حمولة موقَّعة بسرّ العلامة A عدّلت صفّ العلامة B (`status_updates: 1`)؛ `delivered → sent` مقبول.
4. **N4:** مسودة واتساب بـ900k رمز ⇒ `suggest-reply` 429 لكن `/generate` **200**.
5. **N5:** `json.loads` + استعلامات DB **قبل** التوقيع، لا سقف للجسم، لا فهرس فريد للمسودة المعلّقة (سباق قبل `gateway.complete()`).

### 0-ب. S1 — Brand Brain والذكاء التنافسي (رأي المخطط الهندسي/التجاري — اعتُمد كـ**D-074**)

- **التشخيص صحيح:** العربية + الجدولة + الكابتشن ليست خندقاً (Onshur/Simbel/Go Social/Postilik تقدّمها — أُضيفت إلى `COMPETITOR_AND_PRODUCT_RESEARCH.md` §1 و§3). الخندق = **توصية قابلة للتدقيق** + **حلقة مغلقة تتعلّم من الموافقات**.
- **التصميم:** (1) `brand_profiles` يكتبه المالك ويُثريه النموذج **بموافقة** (منتجات، جمهور، نبرة، أهداف، منافسون ≤ 3)؛ (2) `brand_evidence`: كل حقيقة تحمل `evidence_kind ∈ {measured, inferred, estimated}` + `source_kind` + `source_ref` + `observed_at` + `confidence` — **measured** من Graph/Cloud API الخاص بالعميل فقط، **inferred** استنتاج نموذج على بيانات العميل، **estimated** من مصادر عامة لمنافس مُعلَن؛ (3) **المصادر القانونية فقط:** صفحة فيسبوك عامة عبر Graph `GET /{page_id}?fields=name,about,category,fan_count,followers_count,website` بتوكن **تطبيقنا** (حقول عامة لا تتطلب إذن الصفحة)، صفحة ويب عامة للمنافس مع احترام `robots.txt` + `User-Agent` معرِّف + سقف 512 KiB + مضيف من قائمة أدخلها المالك، وبيانات يُدخلها المالك يدوياً؛ **ممنوع** كشط إنستغرام/تيك توك/واتساب، تسجيل الدخول نيابة، شراء بيانات؛ (4) **المخرجات ليست «تقريراً»** بل كائنات موجودة: `content_items` مسودة (خطة محتوى) و`wa_reply_drafts`-style قوالب ردود — تمرّ بالموافقة البشرية نفسها (D-055)؛ (5) `agency-agents` (MIT) ⇒ **EXTRACT**: قوائم التقييم تُحوَّل إلى قوالب برومبت نصية في `brand_brain/prompts/` بـ`prompt_version`، لا وكلاء مستقلين.
- **تجارياً:** الوكالة تبيع «لماذا» لعميلها؛ درجة الدليل تحميها قانونياً وتُقنع العميل. هذا ما لا تبيعه أدوات الجدولة.

### 0-ج. S2 — دمج الـ10% النظيفة من `general_ai_core` (رأي المخطط)

- **الخريطة:** البوابة الحالية (`contract.py` 12 فئة D-067 + `credential_mode ∈ {platform, user_key}` + `facades/openai_compatible.py` + No-Bypass D-068) **هي بالفعل** الطبقة الوسطى من العقد الثلاثي. ما يُستخرج: (أ) **تعداد العمليات الثماني** ⇒ يصبح `Purpose` الحالي (`content_draft`, `reply_suggestion`) + `brand_diagnosis`, `competitor_gap`, `content_plan`, `tone_profile` في SPRINT-10، والعمليتان الأخيرتان (embedding/moderation) عند الحاجة — **كـLiteral لا كطبقة جديدة**؛ (ب) **BYOK في الذاكرة مع fallback تلقائي:** يُرفض جزئياً — المفتاح في الذاكرة لكل طلب (`X-AI-Key` header ⇒ `credential_mode=user_key`) **مقبول** بشرط ألا يُسجَّل ولا يُخزَّن ولا يُطبع (`repr=False`)؛ أما **الـfallback التلقائي من مفتاح المستخدم إلى مفتاح المنصة فممنوع** (يُحمّل المنصة تكلفة لم يوافق عليها المالك ويخفي فشل مفتاح العميل) ⇒ عند فشل `user_key` نعيد الفئة نفسها بـ`credential_mode` في الاستجابة؛ (ج) **بوابات التعلّم الداخلي المنقّى:** تُدمج كتوسيع لـ`wa_learning_signals` نحو `learning_signals` عامة (`purpose`, `signal`, `features`) — **لا نص** (D-069)؛ (د) **12 فئة خطأ** مطابقة لـD-067 — لا تغيير.
- **الحدود الأمنية:** كل ذلك داخل `src/modules/ai_gateway/` فقط؛ لا ملف من `general_ai_core` يُنسخ حرفياً؛ يُعاد كتابته مع اختبارات. **ليس في SPRINT-10** إلا (أ) و(ج) بالقدر الذي يحتاجه M7؛ (ب) في SPRINT-11.

### 0-د. S3 — تعارضات Hub + طبقة الباقات + ترتيب المحطات (رأي المخطط)

- **صُحِّحت الوثائق الثلاث** في هذا الـPR: `MVP_DEFINITION.md` §1-5 (الدفع خارج الـMVP ⇒ مؤجَّل #18؛ الباقات = D-073)، `COMPONENTS_AND_RESPONSIBILITIES.md` E4/E11/E12 (الحالة الفعلية في هذا المستودع — المجلدات القديمة ليست فيه)، `COMPETITOR_AND_PRODUCT_RESEARCH.md` (+4 منافسين عرب + §3 الخندق).
- **(أ) أين تُوضع طبقة الباقات/الحدود/التفعيل؟** وحدة `entitlements` رقيقة في النواة (**D-073**): `brand_entitlements` (خطة + حدود رقمية) + `entitlement_events` بفاعل بشري؛ تفعيل يدوي من مالك المنصة في V1؛ **مصدر الحقيقة الوحيد للحدود** = `ai_gateway/budget.py` (يُنشأ الآن في A-4 لإغلاق N4 ويقرأ من الإعدادات؛ في SPRINT-11 يقرأ من `brand_entitlements`) — فلا نكتب قارئ ميزانية مرتين. الفيروسية/الولاء = `referred_by_brand_id` + رصيد كحدث مُدقَّق، لا كوبونات. بوابة الدفع مؤجَّلة #18.
- **(ب) الترتيب:** الإبقاء على `M7 ⇒ M8 ⇒ M9 ⇒ M10` **مع** إدراج **M9.5** = شريحة واجهة رقيقة (تسجيل دخول + بوابة الموافقة + صندوق الوارد) + تفعيل الباقات قبل بنود M9 غير الحرجة (تقوية الحاويات/سجل التدقيق) — لأن D-055 بلا واجهة = لا عميل تجريبي. القرار النهائي للمالك عند إصدار SPRINT-11 (مؤجَّل #19).

---

## 1. النطاق

**داخل:** A-1..A-6؛ وحدة `src/modules/brand_brain/` (ملف العلامة، الأدلة، المصادر العامة، المنافسون، التشخيص، التوصيات ⇒ مسودات)؛ تشغيل `openai_compatible` فعلياً ضد مزوّد حقيقي **في بيئة المالك** (الاختبارات تبقى `respx`/`fake`)؛ `ai_cost_log`؛ توسيع `Purpose`؛ `ai_gateway/budget.py` مشترك؛ هجرتان 0010 (A) و0011 (B-1).
**خارج (ممنوع):** أي كشط لمنصات التواصل؛ تنزيل وسائط؛ تضمينات/pgvector (البنية موجودة لكن لا تُستخدم بعد — M8)؛ لوحات/تحليلات (M8)؛ تنفيذ آلي لأي توصية (كل مخرج مسودة تحت الموافقة)؛ `brand_entitlements` (SPRINT-11)؛ الواجهة الأمامية؛ تعديل أي ملف محظور (`ARCHITECTURE_LOCK.md`, `SESSION_RECOVERY.md`, `imaster_planner.md`, `README.md` الجذري, `AI_Marketing_Platform_Hub/**`, `sprints/**`).

---

## 2. المواصفات

### 2-0. القواعد الحاكمة (إلزامية — تُضاف إلى قواعد SPRINT-09 ولا تلغيها)

- **أ. D-072 «ثبّت ثم أبلغ»:** أي أثر يجب أن ينجو من فشل لاحق (قرار بشري، تعطيل حساب، سجلّ فشل، سجلّ تكلفة) إمّا يُلتزم في **معاملة مستقلة** عبر `core/database.py::independent_session()` (مُستخرَجة من نمط `PostgresStatusMarker`) **أو** يُعاد كقيمة (outcome) ويُحوَّل إلى HTTP **بعد** `commit` جلسة الطلب. **لا `raise` بعد كتابة يُراد لها البقاء داخل الجلسة نفسها.** كل مسار «كتابة + خطأ» يحتاج اختبار PG يفحص الصف **بعد** الاستجابة.
- **ب. نطاق المستأجر في كل كتابة يحرّكها حدث خارجي:** `brand_id` الحساب الذي **تحقق توقيعه** يُمرَّر إلى المستودع ويُقيَّد به الـ`WHERE`.
- **ج. المصادر العامة القانونية فقط (D-074):** `public_fetch.py` يرفض أي مضيف خارج `brand_competitors.allowed_hosts` المدخلة من المالك، يقرأ `robots.txt` أولاً (يحترم `Disallow` لـ`User-Agent: *` ولوكيلنا)، `User-Agent: AIGrowthOS-BrandBrain/1.0 (+contact)`, سقف 512 KiB، مهلة 10s، GET فقط، لا كوكيز، لا JS، لا متابعة تحويلات إلى مضيف آخر. قائمة مضيفين **ممنوعة ثابتة** في الكود: `instagram.com`, `facebook.com` (إلا عبر Graph), `tiktok.com`, `whatsapp.com`, `linkedin.com`, `x.com`, `twitter.com`, `snapchat.com`, `youtube.com`.
- **د. كل مخرج ذكاء = مسودة تحت الموافقة (D-055):** التوصيات تُكتب كـ`content_items(status=draft)` أو `brand_recommendations(status=proposed)`؛ **لا** انتقال آلي إلى `pending_approval`؛ لا إرسال/نشر.
- **هـ. المزوّد الحقيقي:** يبقى `AI_PROVIDER=fake` في CI/الاختبارات؛ `openai_compatible` يُختبر بـ`respx` بأجسام حقيقية الشكل؛ تشغيله الحي في بيئة المالك فقط. **لا fallback تلقائي** من `user_key` إلى `platform`.
- **و. جدول الرؤية (D-066) يمتد:** `brand_evidence.confidence`/`source_ref` لـOWNER/MANAGER؛ VIEWER يرى `evidence_kind` + الملخص فقط؛ `ai_cost_log` لـOWNER فقط.

### Part A — إلزامي قبل أي ميزة (كوميت واحد) — إغلاق N1–N5 + A-6

| # | البند | التغيير المطلوب (بالملف) | الاختبار الحاسم (PG إلزامي) |
|---|---|---|---|
| **A-1** | **N1 — D-072** | (1) `src/core/database.py`: `independent_session()` (async contextmanager؛ جلسة من المصنع نفسه؛ `commit` عند الخروج الطبيعي) — ويُعاد توجيه `PostgresStatusMarker` إليه بلا تغيير سلوك. (2) `suggestions.py::approve`: القرار + الإشارة + العدّاد تُلتزم **قبل** `send_text` في معاملة مستقلة (`decide_and_record()` على مستودع بجلسة مستقلة)؛ ثم الإرسال؛ `WindowClosedError` ⇒ 422 **والمسودة تبقى `approved`/`edited`**؛ الاستجابة 422 تحمل `draft_status`. (3) `router.py::_handle_send_error` ⇒ `inbox.disable_account_after_auth_failure` يكتب عبر `independent_session()`. (4) `inbox.py::_deliver`: صفّ `failed`/`ambiguous` يُكتب عبر جلسة مستقلة **قبل** رفع الخطأ (صفّ `queued` يُنشأ ويُحدَّث داخلها). | **A1-I1:** approve على نافذة مغلقة على PG ⇒ 422 + `SELECT status FROM wa_reply_drafts` = `approved` + `count(wa_learning_signals)=1`؛ إعادة approve ⇒ 409 `draft_not_pending`. **A1-I2:** Graph 401/190 ⇒ 409 + `whatsapp_accounts.status='disabled'` + المحاولة التالية 409 **بلا نداء Graph** (`len(route.calls)` ثابت). **A1-I3:** `ConnectError` ⇒ 503 + صفّ `wa_messages(delivery_status='failed', error_code=...)`؛ 429 ⇒ صفّ `failed` بـ`error_code=130429`؛ `ReadTimeout` بعد الإرسال ⇒ 202 + صفّ `ambiguous`. |
| **A-2** | **N2** | `service.py::connect_account`: إن وُجد صفّ **`disabled`** للعلامة ⇒ **يُحدَّث في مكانه** (توكن/سرّ/تجزئة جديدة + `status=active` + `updated_at`) ويُعاد 200 لا 201؛ صفّ `active` ⇒ 409 `whatsapp_account_exists` كما هو. مسار جديد `POST /account/rotate` (OWNER، `CurrentUserFresh`) يستبدل `system_token`/`app_secret` لحساب `active` بعد `describe_phone_number` ⇒ 200. تضارب `phone_number_id` مع علامة أخرى: فحص مسبق `get_account_by_phone_number_id` **ثم** التقاط `IntegrityError` على `uq_whatsapp_accounts_phone_number_id` ⇒ 409 `whatsapp_phone_number_in_use` (بلا اسم القيد في الاستجابة). | **A2-I1:** DELETE ⇒ POST (نفس العلامة) ⇒ 200 و`status='active'` والصفّ **نفسه** (`id` ثابت). **A2-I2:** علامتان متزامنتان تربطان `phone_number_id` واحداً على PG ⇒ `[201, 409]` حتمياً. **A2-U1:** rotate بتوكن مرفوض ⇒ 422 ولا تغيير. |
| **A-3** | **N3** | `WhatsAppRepository.update_status_by_wamid(wamid, status, *, brand_id, error_code)` — `WHERE wamid=… AND direction='outbound' AND brand_id=:brand_id AND rank(delivery_status) < rank(:new)` (رتب: `queued=1 < sent=2 < delivered=3 < read=4`؛ `failed`/`ambiguous` ⇒ يُقبل فقط إن كانت الحالية `queued`/`ambiguous`؛ `failed` نهائي). `webhook.py::_handle_status(raw, outcome, *, account)` يمرّر `account.brand_id`. تحديث مرفوض ⇒ يُعدّ في `outcome.ignored_status_updates` (حقل جديد في الاستجابة) + عدّاد `wa_status_ignored`. | **A3-I1:** حمولة موقَّعة بسرّ العلامة A لـ`wamid` العلامة B ⇒ 200 و`status_updates: 0`, `ignored_status_updates: 1` والصفّ **لم يتغير**. **A3-I2:** `read` ثم `sent` ⇒ يبقى `read`. **A3-U1:** جدول الرتب كامل (مصفوفة انتقالات). |
| **A-4** | **N4** | ملف جديد `src/modules/ai_gateway/budget.py`: `class DailyBudget` بدالة واحدة `check(brand_id, *, usage_reader: BudgetUsageReader) -> BudgetVerdict` و`Protocol BudgetUsageReader{combined_usage_since, oldest_generation_since}` — التنفيذ PG **واحد** (`UNION ALL` الموجود يُنقل إلى `ai_gateway/infrastructure/pg_budget.py` ويُحذف من `whatsapp/postgres_repository.py` و`content/postgres_repository.py` الاستعلامان الفرديان). `content/service.py::_check_token_budget` و`whatsapp/suggestions.py` يستدعيان `DailyBudget.check` نفسه؛ `Retry-After` من `oldest_generation_since` المشترك. B-3 (Brand Brain) يستخدمه أيضاً. | **A4-I1:** مسودة واتساب بـ`budget+1` رمز ⇒ `/generate` **429** و`suggest-reply` 429 بنفس `Retry-After` (±1s). **A4-I2:** العكس (مراجعة محتوى) يحجب واتساب. **A4-U1:** `grep -rn "ai_token_usage" src` ⇒ `ai_gateway/` فقط. |
| **A-5** | **N5** | (1) `webhook.py::receive_webhook`: الترتيب الجديد — `Content-Length`/قراءة بسقف **1 MiB** (تجاوز ⇒ 413 `payload_too_large` قبل القراءة الكاملة؛ `request.stream()` بعدّاد) ⇒ رأس `X-Hub-Signature-256` موجود ومطابق لـ`^sha256=[0-9a-f]{64}$` وإلا 401 **قبل** أي `json.loads` أو DB ⇒ استخراج `phone_number_id` بـ**محلّل ضيّق** (يقرأ `entry[*].changes[*].value.metadata.phone_number_id` فقط من `json.loads` — مقبول الآن لأن الجسم ≤ 1 MiB وموقَّع شكلاً) ⇒ تحميل الحسابات ⇒ تحقق HMAC ⇒ معالجة. (2) هجرة **0010**: فهرس فريد جزئي `uq_wa_reply_drafts_one_pending ON wa_reply_drafts(conversation_id) WHERE status='suggested'` (+ تنظيف مسبق: الأقدم تُعلَّم `expired`)؛ `suggestions.py::suggest`: بعد `gateway.complete()` الإدراج داخل `try/except IntegrityError` ⇒ 409 `draft_pending` (والرموز المستهلكة تُسجَّل في `ai_cost_log` لاحقاً B-2 — الآن تُعدّ فقط). | **A5-U1:** جسم 1 MiB + 1 بايت ⇒ 413 و**صفر** استعلام (مستودع يعدّ النداءات). **A5-U2:** رأس مفقود/`garbage`/`sha256=zz…` ⇒ 401 وصفر `json.loads` (monkeypatch يعدّ). **A5-I1:** طلبا `suggest-reply` متزامنان على PG (محرّكان) ⇒ `[201, 409]` وصفّ `suggested` **واحد**. **A5-M1:** 0010 ذهاب/إياب على PG مع صفّين معلّقين موجودين ⇒ الأقدم `expired`. |
| **A-6** | جوابا SPRINT-09 | (1) `MessagePublic.wamid` يظهر لـOWNER/MANAGER فقط (`private=` كما في `ReplyDraftPublic`)؛ VIEWER لا يراه. (2) `SuggestReplyRequest.language="auto"` ⇒ `signals.language(last_inbound.body)`؛ `mixed`/`other` ⇒ `ar`؛ القيمة الصريحة تتقدّم؛ الناتج في provenance **الخاص** `language_detected`. | **A6-U1:** جسد VIEWER لا يحوي `wamid`؛ OWNER يحويه. **A6-U2:** رسالة إنجليزية + `auto` ⇒ برومبت النظام بالإنجليزية و`language_detected="en"` خاص لا عام. |

**تعديل مطلوب على اختبارات SPRINT-09:** كل اختبار كان يؤكد سلوك N1 القديم (مثل «422 والمسودة تبقى approved» في الوحدة) يُنقل إلى PG؛ `PROTECTED_ROUTE_COUNT` 46 ⇒ **47** (`POST /account/rotate`)؛ أجسام استجابة الـwebhook تضيف `ignored_status_updates`.

### Part B-1 — ملف العلامة + الأدلة + هجرة 0011 (كوميت واحد)

**وحدة جديدة `src/modules/brand_brain/`** (`domain/`, `infrastructure/`, `router.py`, `service.py`, `schemas.py`, `prompts/`).

**هجرة 0011** (قيود مسمّاة؛ ذهاب/إياب على PG):
- `brand_profiles`: `brand_id` PK→brands (CASCADE)، `products` JSONB (قائمة `{name, description, price_hint, url}` ≤ 20)، `audience` JSONB (`{segments[], regions[], languages[], age_bands[]}`)، `tone` JSONB (`{voice, formality, emoji_policy, banned_phrases[]}`)، `goals` JSONB (≤ 5 `{kind ∈ {awareness, leads, sales, retention}, target, horizon_days}`)، `version` ≥ 1، `updated_by_user_id`، `updated_at`. **كل تحديث يرفع `version`** (CAS على `version`).
- `brand_profile_revisions`: سجلّ نسخ الملف (`brand_id`, `version`, `snapshot` JSONB, `changed_by_user_id`, `source ∈ {human, ai_suggested}`, `created_at`) — UNIQUE (`brand_id`, `version`).
- `brand_competitors`: `id`, `brand_id`, `name`, `kind ∈ {direct, indirect}`, `facebook_page_id` NULL، `website_host` NULL (مضيف فقط، لا مسار)، `allowed_hosts` JSONB (≤ 3)، `notes`, `added_by_user_id`, `created_at` — UNIQUE (`brand_id`, `name`)؛ **CHECK عددي عبر اختبار PG**: ≤ 3 لكل علامة (يُفرض في الخدمة + اختبار سباق ⇒ `[201,201,201,409]`).
- `brand_evidence`: `id`, `brand_id`, `subject_kind ∈ {brand, competitor}`, `subject_id` (brand_id أو competitor id), `evidence_kind ∈ {measured, inferred, estimated}`, `metric` VARCHAR(64) (قاموس مغلق في `domain/metrics.py`: `followers_count`, `posting_frequency_7d`, `avg_reply_latency_s`, `top_content_type`, `wa_conversations_7d`, `wa_approval_rate_30d`, `tone_match_score`, …), `value` JSONB, `confidence` NUMERIC(3,2) ∈ [0,1], `source_kind ∈ {graph_api, cloud_api, internal_db, public_web, owner_input, model_inference}`, `source_ref` TEXT (مسار/رابط/معرّف — **لا توكن**), `observed_at`, `created_at` — CHECK: `evidence_kind='measured' ⇒ source_kind IN ('graph_api','cloud_api','internal_db')`؛ `evidence_kind='estimated' ⇒ source_kind IN ('public_web','owner_input','graph_api')`؛ `evidence_kind='inferred' ⇒ source_kind='model_inference'`. فهرس (`brand_id`, `subject_kind`, `subject_id`, `metric`, `observed_at DESC`).
- `ai_cost_log`: `id`, `brand_id`, `purpose`, `provider`, `model`, `credential_mode`, `prompt_tokens`, `completion_tokens`, `estimated_cost_usd` NUMERIC(10,6) NULL (من جدول أسعار ثابت في الإعدادات `AI_PRICE_TABLE_JSON`؛ NULL إن غاب)، `latency_ms`, `prompt_version`, `outcome ∈ {ok, error}`, `error_category` NULL, `created_at` — **لا نص برومبت/مخرجات**. يُكتب عبر `independent_session()` (D-072) من داخل `ai_gateway` لكل نداء (نجاح أو فشل).

**مسارات B-1** تحت `/api/v1/brands/{brand_id}/brain`: `GET /profile` (كل الأدوار؛ VIEWER بلا `banned_phrases`)، `PUT /profile` (OWNER/MANAGER؛ `If-Match: version` ⇒ 412 عند التعارض)، `GET /profile/revisions`, `GET/POST /competitors`, `DELETE /competitors/{id}`, `GET /evidence?subject=&metric=&kind=` (keyset `observed_at DESC, id DESC`)، `GET /cost` (OWNER فقط؛ تجميع يومي 30 يوماً).

**الأدلة المقاسة الداخلية (B-1، بلا نموذج):** `EvidenceCollector.collect_internal(brand_id)` يحسب من قواعدنا: `wa_conversations_7d`, `wa_approval_rate_30d` (من `wa_learning_signals`), `content_approval_rate_30d`, `posting_frequency_7d` (من `publish_jobs succeeded`), `avg_reply_latency_s` (من `wa_messages`) ⇒ صفوف `measured/internal_db`. يُستدعى عبر `POST /evidence/refresh` (OWNER/MANAGER؛ 60/ساعة/علامة عبر `limiter`).

### Part B-2 — المصادر العامة + المنافسون + المزوّد الحقيقي (كوميت واحد)

- **`brand_brain/public_fetch.py`** (الاستثناء الوحيد الجديد لـ`TID251`، فوق `core/http.py`): `PublicPageFetcher.fetch(url) -> PublicPage{host, status, title, meta_description, text_excerpt(≤ 4000 حرف), fetched_at}`؛ يطبّق 2-0-ج حرفياً؛ `robots.txt` يُخزَّن مؤقتاً في الذاكرة 1h؛ أي رفض ⇒ `PublicFetchRefused(reason ∈ {host_not_allowed, robots_disallow, too_large, timeout, non_html, blocked_platform})` ويُسجَّل كدليل `estimated` بـ`value={"unavailable": reason}`.
- **منافس على فيسبوك (Graph، توكن التطبيق `app_id|app_secret` كما في `/debug_token`):** `GET /{facebook_page_id}?fields=name,about,category,fan_count,followers_count,website,link` ⇒ أدلة `estimated/graph_api` (`followers_count`, `category`, …). أخطاء Graph ⇒ `classify_graph_error` الموجود؛ 100/غير متاح ⇒ دليل `unavailable`. **لا `/posts` لصفحات لا نملك توكنها.**
- **`POST /competitors/{id}/refresh`** (OWNER/MANAGER؛ 10/ساعة/علامة) ⇒ يجمع Graph + صفحة الويب ⇒ أدلة `estimated` + يرفع `competitor_refreshed_at`.
- **المزوّد الحقيقي:** `Purpose` += `brand_diagnosis`, `competitor_gap`, `content_plan`, `tone_profile`. `openai_compatible` يُضاف إليه `response_format={"type":"json_object"}` عند `req.json_mode=True` + محلّل صارم (`pydantic` على المخرج؛ فشل التحليل ⇒ `ErrorCategory.invalid_output` الموجودة ضمن الـ12). **`X-AI-Key` اختياري** على مسارات Brand Brain ⇒ `credential_mode=user_key` (المفتاح في الذاكرة فقط، `repr=False`، لا سجل، لا تخزين؛ فشل ⇒ الفئة نفسها + `credential_mode` في الاستجابة؛ **لا fallback**). `ai_cost_log` يُكتب لكل نداء من `AIGateway` (مُغلِّف `CostLoggingGateway`).
- **`prompts/`**: `brand_diagnosis_v1`, `competitor_gap_v1`, `content_plan_v1`, `tone_profile_v1` — كلها تُعلن `prompt_version` و`prompt_hash`؛ تُبنى من `agency-agents` كقوائم فحص نصية (اذكر الملف المصدر في تعليق).

### Part B-3 — التشخيص + التوصيات ⇒ مسودات تحت الموافقة (كوميت واحد)

- **هجرة 0012 (صغيرة):** `brand_recommendations`: `id`, `brand_id`, `kind ∈ {content_plan_item, tone_adjustment, reply_template, gap_action}`, `title`, `rationale` TEXT (≤ 1200)، `evidence_ids` JSONB (≥ 1 — **كل توصية تستند إلى دليل واحد على الأقل**؛ CHECK `jsonb_array_length >= 1`), `evidence_kinds_summary` JSONB (`{measured: n, inferred: n, estimated: n}`), `status ∈ {proposed, accepted, dismissed, materialized}`, `decided_by_user_id`/`decided_at` (CHECK: قرار ⇒ فاعل), `materialized_item_id` → content_items NULL, `provenance` JSONB (عام/خاص D-066), `created_at`.
- **`POST /brain/diagnose`** (OWNER/MANAGER؛ 5/ساعة/علامة؛ ميزانية `DailyBudget.check`) ⇒ يجمع آخر الأدلة (≤ 60 صفاً، كل الدرجات) + الملف ⇒ `brand_diagnosis_v1` ⇒ `DiagnosisPublic{summary, strengths[], gaps[], evidence_used[{id, kind}]}` **ويخزّنه كتوصيات `proposed`** (≤ 10). كل جملة في `rationale` تُلحق بوسم الدرجة `[مُقاس]/[مُستنتج]/[مُقدَّر]` **من الخادم** (لا من النموذج): الخدمة تربط كل `evidence_id` مذكور بدرجته؛ توصية بلا `evidence_ids` صالحة ⇒ تُسقط وتُعدّ `recommendations_dropped`.
- **`POST /brain/content-plan`** (الشروط نفسها) ⇒ `content_plan_v1` ⇒ ≤ 7 عناصر ⇒ **`content_items(status=draft, channel, body, provenance.kind="ai_generated", provenance.recommendation_id)`** عبر `ContentService.create_draft` الموجود — تبقى `draft`؛ **لا** `pending_approval`.
- **`POST /brain/recommendations/{id}/accept|dismiss`** (OWNER/MANAGER؛ `CurrentUserFresh`) ⇒ قرار بفاعل؛ `accept` لـ`content_plan_item` ⇒ `materialized` + `materialized_item_id`. `reply_template` ⇒ يُخزَّن في `brand_profiles.tone.reply_templates[]` (≤ 10) **بعد القبول فقط**.
- **إشارة تعلّم عامة:** `learning_signals` (جدول جديد في 0012: `brand_id`, `purpose`, `subject_id`, `signal ∈ {accepted, dismissed, materialized, approved_unchanged, approved_after_edit, rejected, expired}`, `features` JSONB, `prompt_version`, `model`, `credential_mode`, `created_at`) — `wa_learning_signals` **تبقى** كما هي (لا نقل في هذا السبرنت؛ النقل مؤجَّل)؛ Brand Brain يكتب في الجدول العام فقط. **لا نص** (D-069).

---

## 3. معايير القبول (المخطط يتحقق منها حرفياً على PG 17)

1. `pytest -q` كاملة على PG 17 ⇒ 0 فشل؛ `--cov-fail-under=95` يمرّ على CI `unit`؛ ruff نظيف.
2. **A1-I1/I2/I3** و**A3-I1/I2** و**A4-I1/I2** و**A5-I1/M1** و**A2-I1/I2** تمرّ **على PG** وتفحص الصفوف بعد الاستجابة (لا Fake).
3. `grep -rn "ai_token_usage" src` ⇒ ملفات `src/modules/ai_gateway/` فقط؛ `grep -rn "import httpx" src` ⇒ الخمسة السابقة + `brand_brain/public_fetch.py` **فقط**.
4. `grep -rn "instagram.com\|tiktok.com" src/modules/brand_brain/public_fetch.py` ⇒ في قائمة **المنع** فقط؛ اختبار يثبت الرفض `blocked_platform` **قبل** أي نداء شبكي (`respx` يؤكد 0 نداء).
5. `robots.txt` بـ`Disallow: /` ⇒ `robots_disallow` و0 نداء للصفحة؛ جسم 512 KiB + 1 ⇒ `too_large` (قراءة متدفقة تتوقف).
6. **لا توكن/مفتاح في `ai_cost_log` أو `brand_evidence.source_ref`** — حارس اختباري يفحص كل الأعمدة النصية بنمط `EAA|sk-|Bearer`.
7. `X-AI-Key` لا يظهر في أي سجل (التقاط `caplog`) ولا في `ai_cost_log` ولا في الاستجابة؛ فشل `user_key` **لا** يستدعي مفتاح المنصة (`respx`: 0 نداء بالـBearer المنصة).
8. كل توصية في `brand_recommendations` لها `evidence_ids` ≥ 1 (CHECK على PG) ووسوم الدرجة في `rationale` **مُولَّدة خادمياً** (اختبار: النموذج يعيد وسماً مزيفاً `[مُقاس]` لدليل `estimated` ⇒ الخادم يستبدله).
9. `content-plan` ينشئ `content_items` بحالة `draft` **فقط** (اختبار يعدّ `approval_events` = 0).
10. `PROTECTED_ROUTE_COUNT == 47 + (مسارات brain)` — اذكر العدد النهائي حرفياً في §6 مع القائمة.
11. آلة حالات المحتوى بلا تغيير: `len(EXPECTED) == 56`.
12. هجرات 0010/0011/0012 ذهاب/إياب على PG (`head ⇒ 0009 ⇒ head`) بلا أثر على الجداول الأخرى.
13. **لا ملف محظور** في `git diff --name-only <base>..HEAD`.
14. تقرير §6 يذكر **بالسطر** كل بند من A-1..A-6 وB-1..B-3، وأي انحراف بسببه.

---

## 4. أوامر

```bash
# البداية
git checkout work/implementation && git fetch origin && git merge --ff-only origin/main
# PG محلية (SESSION_RECOVERY.md §2-ب) ثم:
export DATABASE_URL=postgresql+asyncpg://aigos@localhost:5433/ai_growth_os_test
export TEST_DATABASE_URL=$DATABASE_URL
export AUTH_JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
alembic upgrade head && alembic downgrade 0009 && alembic upgrade head
ruff check . && ruff format --check .
pytest -q --cov=src --cov-fail-under=95 -p no:cacheprovider
# greps القبول
grep -rn "ai_token_usage" src | grep -v "src/modules/ai_gateway/" ; echo "^ يجب أن يكون فارغاً"
grep -rln "import httpx" src | sort
grep -rn "auto_reply\|AUTO_REPLY" src ; echo "^ فارغ"
git diff --name-only origin/main..HEAD | grep -E "^(ARCHITECTURE_LOCK|SESSION_RECOVERY|imaster_planner|README)\.md$|^AI_Marketing_Platform_Hub/|^sprints/" ; echo "^ فارغ"
```

---

## 5. التزامات المنفذ

1. **أربعة كوميتات** بالترتيب (A ⇒ B-1 ⇒ B-2 ⇒ B-3)؛ كل واحد يمرّ الحزمة كاملة على PG وحده. ادفع `wip:` بعد كل ملف ثم اسحق إلى الأربعة (كما فعلت في SPRINT-09).
2. **Part A يُدفع ويُبلَّغ أولاً** (تعليق في الـPR «Part A ready» مع SHA) قبل بدء B — المخطط قد يراجعه مبكراً.
3. لا تعديل لأي ملف محظور؛ `CURRENT_DIRECTIVE.md` §6 و`WORK_STATE.md` §1 فقط (`SPRINT_10_DELIVERED_AWAITING_REVIEW`).
4. لا تبعية جديدة؛ أضف `beautifulsoup4`, `scrapy`, `playwright`, `selenium`, `instaloader`, `lxml` إلى `banned-api`.
5. أي غموض ⇒ قرار موثَّق في §6 «انحرافات» بالسطر والسبب — لا سؤال معلّق يوقف التنفيذ.
6. لا سرّ في أي ملف أو سجل أو تعليق؛ `AI_PROVIDER=fake` في CI.

---

## 6. تقرير المنفذ (يملؤه المنفذ)

- **حالة التنفيذ:** `[x] مكتمل` / `[ ] جزئي`
- **Commit SHA Part A:** `5e269d3` — **B-1:** `d075c79` — **B-2:** `fc54051` — **B-3:** `e219e8e` (+ كوميت التوثيق فوقها)
- **PR:** `#32` — **CI:** `5/5` (lint, unit, integration, secrets, licenses — على `b4163c8` في تشغيلَي push وpull_request)
- **الاختبارات (PG 17 UTF8 محلياً، كل شجرة وحدها):** Part A `5e269d3` ⇒ مرّ سابقاً (أُعلن «Part A ready»)؛ B-1 ⇒ **1098 passed / 0 failed / 98%**؛ B-2 ⇒ **1170 passed / 0 failed / 98%**؛ B-3 (الرأس) ⇒ **1207 passed / 0 failed / 98%** — منها تكامل PG **89** (`tests/integration`) وBrand Brain وحدة **114** (`tests/modules/brand_brain`) — **التغطية:** 98% (≥ 95).
- **A-1..A-6 (بالسطر):**
  - **A-1 (N1/D-072):** `src/core/database.py::independent_session()` (async contextmanager، `commit` عند الخروج الطبيعي) ويستخدمه `social_connect/infrastructure/status_marker.py::PostgresStatusMarker` بلا تغيير سلوك. `whatsapp/domain/repository.py::IndependentWrites(Protocol).run(fn)` + `whatsapp/infrastructure/postgres_repository.py::PgIndependentWrites`؛ `suggestions.py::approve` يلتزم القرار + الإشارة + العدّاد **قبل** `send_text` في معاملة مستقلة؛ `WindowClosedError` ⇒ 422 والمسودة تبقى `approved`/`edited`؛ تعطيل الحساب (190) وصفوف `failed`/`ambiguous` في معاملات مستقلة. اختبارات PG: `test_a1_i1_approve_on_closed_window_keeps_decision_and_signal_on_pg`, `test_a1_i2_graph_190_disables_account_persistently_and_next_send_skips_graph_on_pg`, `test_a1_i3_failure_rows_survive_the_error_response_on_pg`.
  - **A-2 (N2):** `whatsapp/service.py::connect_account` يعيد `ConnectOutcome(reconnected=True)` عند صفّ `disabled` (يُحدَّث في مكانه ⇒ 200)؛ `active` ⇒ 409 `whatsapp_account_exists`؛ فحص مسبق `get_account_by_phone_number_id` ثم التقاط `IntegrityError` ⇒ 409. مسار جديد `POST /account/rotate` (`router.py::rotate_account_credentials`, OWNER, `CurrentUserFresh`) ⇒ `service.py::rotate_credentials` بعد `describe_phone_number`. PG: `test_a2_i1_delete_then_post_reconnects_the_same_row_on_pg`, `test_a2_i2_two_brands_connecting_one_phone_number_concurrently_is_201_409_on_pg`.
  - **A-3 (N3):** `whatsapp/domain/models.py::DELIVERY_RANK` + `delivery_transition_allowed(current, new)`؛ `WhatsAppRepository.update_status_by_wamid(wamid, status, *, brand_id, error_code)` بـ`WHERE … AND direction='outbound' AND brand_id=:brand_id` ورتابة الرتب؛ `webhook.py::_handle_status(raw, outcome, *, account)` يمرّر `account.brand_id`؛ `WebhookOutcome.ignored_status_updates` في الاستجابة. PG: `test_a3_i1_status_signed_by_brand_a_cannot_touch_brand_b_row_on_pg`, `test_a3_i2_read_then_sent_stays_read_on_pg`.
  - **A-4 (N4):** `src/modules/ai_gateway/budget.py` — `BudgetUsageReader(Protocol){combined_usage_since, oldest_generation_since}`, `NullBudgetUsageReader`, `BudgetVerdict`, `DailyBudget(limit).check(brand_id, *, usage_reader)`؛ التنفيذ PG الوحيد `ai_gateway/infrastructure/pg_budget.py::PgBudgetUsageReader` (`UNION ALL` نُقل إليه وحُذف الاستعلامان الفرديان من `whatsapp`/`content`). `content/service.py::_check_token_budget` و`whatsapp/suggestions.py` يستدعيان `DailyBudget`. PG: `test_a4_i1_whatsapp_draft_tokens_block_content_generate_with_same_retry_after_on_pg`, `test_a4_i2_content_revision_tokens_block_whatsapp_suggest_on_pg`.
  - **A-5 (N5):** `webhook.py::MAX_BODY_BYTES = 1 MiB` (قراءة متدفقة بعدّاد ⇒ 413 `payload_too_large`) ⇒ `SIGNATURE_HEADER`/`SIGNATURE_PATTERN = ^sha256=[0-9a-f]{64}$` وإلا 401 قبل أي `json.loads`/DB ⇒ محلّل ضيّق لـ`phone_number_id` ⇒ `hmac.compare_digest`. هجرة `0010_wa_one_pending_draft.py` (فهرس جزئي `uq_wa_reply_drafts_one_pending`؛ تنتهي صلاحية المسودات الأقدم). PG: `test_a5_i1_concurrent_suggest_reply_is_201_409_with_one_pending_row_on_pg`, `test_a5_m1_migration_0010_roundtrip_expires_older_pending_drafts_on_pg`.
  - **A-6:** `whatsapp/schemas.py::MessagePublic.wamid` بـ`private=` (OWNER/MANAGER فقط)؛ `suggestions.py::resolve_language(language, body)` (`auto` ⇒ `signals.language()`؛ `mixed/other ⇒ ar`) ويُثبَّت في provenance الخاص `language_detected`. وحدة: `test_a6_u1_wamid_is_private_viewer_never_sees_it`, `test_a6_u2_resolve_language`, `test_a6_u2_auto_english_inbound_gives_english_prompt_and_private_language_detected`.
- **B-1 / B-2 / B-3 (بالسطر):**
  - **B-1:** وحدة `src/modules/brand_brain/` (`domain/{models,metrics,repository,hosts}.py`, `infrastructure/{orm,postgres_repository,internal_metrics}.py`, `service.py`, `router.py`, `schemas.py`, `exceptions.py`, `prompts/`). هجرة `0011_brand_brain.py`: `brand_profiles` (JSONB `products/audience/tone/goals`، `version ≥ 1`، CAS على `version`)، `brand_profile_revisions` (UNIQUE `uq_brand_profile_revisions_brand_version`, `source ∈ {human, ai_suggested}`)، `brand_competitors` (UNIQUE `(brand_id, name)`؛ ≤ 3 يُفرض في الخدمة بقفل صفّ العلامة)، `brand_evidence` (`evidence_kind ∈ {measured, inferred, estimated}`, `metric` من قاموس `domain/metrics.py`, `confidence NUMERIC(3,2)`, `source_kind`, `source_ref`)، `ai_cost_log` (بلا نص؛ `estimated_cost_usd` NULL إن غاب جدول الأسعار). مسارات: `GET/PUT /profile` (`If-Match` ⇒ 412)، `GET /profile/revisions`, `GET/POST /competitors`, `DELETE /competitors/{id}`, `GET /evidence?subject=&metric=&kind=` (keyset `observed_at DESC, id DESC`)، `POST /evidence/refresh` (60/ساعة؛ `EvidenceCollector.collect_internal` ⇒ `wa_conversations_7d`, `wa_approval_rate_30d`, `content_approval_rate_30d`, `posting_frequency_7d`, `avg_reply_latency_s` كـ`measured/internal_db`)، `GET /cost` (OWNER؛ 30 يوماً). VIEWER بلا `banned_phrases`/`confidence`/`source_ref` (D-066). اختبارات: `tests/modules/brand_brain/test_b1_routes.py` (19) + PG `test_b1_i1_profile_if_match_cas_and_revisions_on_pg`, `test_b1_i2_competitor_limit_race_is_201_201_201_409_on_pg`, `test_b1_m1_migration_0011_roundtrip_on_pg`.
  - **B-2:** `brand_brain/public_fetch.py` (`PublicPageFetcher.fetch(url, *, allowed_hosts) -> PublicPage{host,status,title,meta_description,text_excerpt≤4000,fetched_at}`؛ `PublicFetchRefused(reason ∈ {host_not_allowed, robots_disallow, too_large, timeout, non_html, blocked_platform})`؛ `robots.txt` مخزَّن 1h في `_RobotsCache`؛ UA `AIGrowthOS-BrandBrain/1.0 (+contact)`؛ 512 KiB؛ 10s؛ GET فقط؛ لا كوكيز/تحويلات لمضيف آخر)؛ قائمة المنع الثابتة في `domain/hosts.py::is_blocked_platform`. `social_connect/meta_client.py::get_public_page(page_id)` بتوكن التطبيق `app_id|app_secret` ⇒ `MetaPublicPageInfo` ⇒ `service.py::CompetitorRefresher` ⇒ أدلة `estimated/graph_api` + `estimated/public_web` (أو `{"unavailable": reason}`)؛ `POST /competitors/{id}/refresh` (10/ساعة) يرفع `competitor_refreshed_at`. `ai_gateway/gateway.py::Purpose` += `brand_diagnosis, competitor_gap, content_plan, tone_profile`؛ `CompletionRequest.json_mode` ⇒ `response_format={"type":"json_object"}` في `facades/openai_compatible.py`؛ `ai_gateway/cost_logging.py::CostLoggingGateway` + `PriceTable` (من `AI_PRICE_TABLE_JSON`) يكتب `ai_cost_log` عبر `independent_session()` لكل نداء؛ `X-AI-Key` اختياري ⇒ `credential_mode=user_key` (في الذاكرة فقط، `repr=False`، لا fallback إلى المنصة — `router.py::_NoPlatformGateway`). `prompts/{brand_diagnosis,competitor_gap,content_plan,tone_profile}_v1.py` تعلن `prompt_version` و`prompt_hash` مع `source_note` لملفات `agency-agents`. اختبارات: `test_b2_public_fetch.py` (15), `test_b2_competitor_refresh.py` (8), `test_b2_gateway.py` (15) + PG `test_b2_i1_competitor_refresh_*`, `test_b2_i2_cost_logging_gateway_*` (مع حارس `_SECRET_SHAPES` على كل الأعمدة النصية).
  - **B-3:** هجرة `0012_brand_recommendations.py`: `brand_recommendations` (CHECKs مسمّاة `ck_brand_recommendations_{kind,status,rationale_len,evidence_ids(jsonb_array_length ≥ 1),kinds_summary,provenance,decision_actor,materialized_iff}`) و`learning_signals` (`ck_learning_signals_{signal,credential_mode,features}`؛ بلا نص). `advisor.py::BrandAdvisor`: `diagnose()` (≤ 60 دليل `EVIDENCE_WINDOW_ROWS` + الملف ⇒ `brand_diagnosis_v1` ⇒ `DiagnosisPublic{summary,strengths[],gaps[],evidence_used[{id,kind}],recommendations,recommendations_dropped}` + ≤ 10 توصيات `proposed`؛ وسوم `[مُقاس]/[مُستنتج]/[مُقدَّر]` من الخادم عبر `grade_tag/tag_text` بعد `strip_fake_tags`؛ توصية بلا دليل صالح تُسقَط وتُعدّ)، `content_plan()` (≤ 7 ⇒ `ContentService.create_item` ثم `update_body` بـ`provenance.kind="ai_generated"` + `provenance.recommendation_id`؛ تبقى `draft`)، `accept()/dismiss()` (`CurrentUserFresh`؛ CAS على `status`؛ `reply_template` ⇒ `tone.reply_templates[]` ≤ 10 بعد القبول فقط عبر `_store_reply_template` + مراجعة `ai_suggested`)، `_signal()` ⇒ `learning_signals` (features رقمية فقط). مسارات: `POST /diagnose`, `POST /content-plan` (OWNER/MANAGER؛ 5/ساعة؛ `DailyBudget.check`), `GET /recommendations?status=`, `POST /recommendations/{id}/accept|dismiss`. اختبارات: `test_b3_advisor.py` (20 دالة/23 حالة) + PG `test_b3_i1_diagnose_*`, `test_b3_i2_content_plan_creates_drafts_only_on_pg` (`approval_events = 0`), `test_b3_i3_accept_dismiss_*`, `test_b3_m1_migration_0012_roundtrip_on_pg`.
- **اختبارات SPRINT-09 المعدَّلة (السبب لكل واحد):**
  - `tests/test_auth_guard.py` و`tests/modules/content/test_router.py`: `PROTECTED_ROUTE_COUNT` 46 ⇒ 47 (A-2 `rotate`) ⇒ **62** (+15 brain).
  - `tests/modules/whatsapp/test_b1_webhook.py`: أجسام الـwebhook تضيف `ignored_status_updates` (A-3)؛ `sent ⇒ failed` لم يعد مسموحاً (رتب A-3).
  - `tests/modules/whatsapp/test_b3_suggestions.py`: الاستجابة تحمل `draft_status="approved"` بعد 422 (A-1 D-072)؛ اختبار «422 والمسودة approved» نُقل إلى PG (`test_a1_i1_*`).
  - `tests/modules/whatsapp/test_b2_inbox.py`: حساب `disabled` ⇒ 409 `reauth_required` بلا نداء Graph (A-1).
  - `tests/modules/whatsapp/test_b1_client.py`: `connect_account` يعيد `ConnectOutcome` (A-2).
  - `tests/modules/whatsapp/conftest.py` و`tests/conftest.py` و`tests/fakes.py`: قارئ ميزانية مشترك واحد `FakeBudgetUsageReader` (A-4) + `FakeIndependentWrites` (A-1) + mixins للـfake repo (B-1/B-3).
  - `tests/modules/social_connect/test_router.py`: مسار `rotate` ضمن تعداد المسارات (A-2).
  - `tests/modules/ai_gateway/test_contract.py`: `BANNED_SCRAPING_MODULES` + استثناء `TID251` الوحيد لـ`brand_brain/public_fetch.py` (B-2/D-074).
  - `tests/integration/conftest.py`: `TABLES` += جداول 0011/0012 (ترتيب الـTRUNCATE).
  - `tests/integration/test_postgres_persistence.py`: تأكيدات `alembic_version` عند `head` 0009 ⇒ 0010 ⇒ 0011 ⇒ **0012**؛ اختبار السباق B1-I2 يربط `app.state.http_client` لأن `get_brain_service` صار يحلّ `HttpClient` (B-2).
  - جديد (ليس تعديلاً): `tests/modules/whatsapp/test_a_sprint10_part_a.py` (12), `tests/modules/whatsapp/test_postgres_repository.py` (17 — تغطية وحدة لمحوّلات PG كي يمرّ `--cov-fail-under=95` في وظيفة `unit` بلا PG).
- **القبول §3 (نواتج الـgreps حرفياً + `PROTECTED_ROUTE_COUNT` النهائي وقائمته):**
  - `grep -rn "ai_token_usage" src | grep -v "src/modules/ai_gateway/"` ⇒ **(فارغ)**؛ الملف الوحيد: `src/modules/ai_gateway/infrastructure/pg_budget.py`.
  - `grep -rln "import httpx" src | sort` ⇒ `src/core/http.py`, `src/modules/ai_gateway/dependencies.py`, `src/modules/ai_gateway/facades/openai_compatible.py`, `src/modules/brand_brain/public_fetch.py`, `src/modules/social_connect/meta_client.py`, `src/modules/whatsapp/client.py` (الخمسة + `public_fetch.py` فقط).
  - `grep -rn "instagram.com\|tiktok.com" src` ⇒ `src/modules/brand_brain/domain/hosts.py` فقط (قائمة المنع؛ `public_fetch.py` يستوردها — انظر الانحرافات)؛ اختبار `blocked_platform` قبل أي نداء (`respx` 0 نداء) في `test_b2_public_fetch.py`.
  - `grep -rn "auto_reply\|AUTO_REPLY" src` ⇒ **(فارغ)**.
  - `git diff --name-only origin/main..HEAD | grep -E "^(ARCHITECTURE_LOCK|SESSION_RECOVERY|imaster_planner|README)\.md$|^AI_Marketing_Platform_Hub/|^sprints/"` ⇒ **(فارغ)**.
  - `alembic upgrade head && alembic downgrade 0009 && alembic upgrade head` ⇒ نجح؛ `alembic current` = `0012 (head)`.
  - `len(EXPECTED) == 56` — `tests/modules/content/test_lifecycle.py:106` بلا تغيير.
  - **`PROTECTED_ROUTE_COUNT == 62`** = 47 (SPRINT-09 + A-2 `POST /account/rotate`) + **15** مسار brain تحت `/api/v1/brands/{brand_id}/brain`: `GET /profile`, `PUT /profile`, `GET /profile/revisions`, `GET /competitors`, `POST /competitors`, `DELETE /competitors/{competitor_id}`, `POST /competitors/{competitor_id}/refresh`, `GET /evidence`, `POST /evidence/refresh`, `GET /cost`, `POST /diagnose`, `POST /content-plan`, `GET /recommendations`, `POST /recommendations/{recommendation_id}/accept`, `POST /recommendations/{recommendation_id}/dismiss`.
  - حارس الأسرار (§3-6/7): `_SECRET_SHAPES` (`EAA|sk-|Bearer`) على كل الأعمدة النصية في `ai_cost_log`/`brand_evidence` في `test_b2_i1/i2`؛ `caplog` لا يحوي `X-AI-Key` و`respx` يؤكد 0 نداء بمفتاح المنصة عند فشل `user_key` (`test_b2_gateway.py`).
- **تقرير التشغيل الحي للمزوّد (بلا أي سرّ):** في بيئة المالك فقط (CI يبقى `AI_PROVIDER=fake`): `AI_PROVIDER=openai_compatible`, `AI_PROVIDER_BASE_URL=https://<host>/v1` (يُنادى `{base_url}/chat/completions`)، `AI_PROVIDER_API_KEY=<مفتاح>` (يسافر في `Authorization: Bearer` فقط؛ `SecretStr`)، `AI_DEFAULT_MODEL=<model>`، `AI_HTTP_TIMEOUT_SECONDS=30`، اختيارياً `AI_PRICE_TABLE_JSON='{"<model>":{"prompt_per_1k":0.00015,"completion_per_1k":0.0006}}'` (غيابه ⇒ `estimated_cost_usd` NULL والنداء يُسجَّل). بديل لكل طلب: رأس `X-AI-Key: <مفتاح المستخدم>` على مسارات Brand Brain ⇒ `credential_mode=user_key` بلا لمس مفتاح المنصة. الاختبار الحي: (1) `PUT /brain/profile` بملف حقيقي؛ (2) `POST /brain/evidence/refresh` ثم (اختياري) `POST /brain/competitors` + `/refresh`؛ (3) `POST /brain/diagnose` ⇒ توقع `summary/strengths/gaps` وتوصيات `proposed` بوسوم الدرجة؛ (4) `POST /brain/content-plan` ⇒ مسودات في `GET /content/items?status=draft`؛ (5) `GET /brain/cost` (OWNER) يعرض لكل يوم `calls, errors, prompt_tokens, completion_tokens, estimated_cost_usd` + الإجماليات `total_calls/prompt_tokens/completion_tokens/total_estimated_cost_usd` — أي فشل (مفتاح خاطئ/نموذج غير متاح/مخرج غير JSON) يظهر كـ`errors` مع `error_category` في `ai_cost_log` ولا نص برومبت/مخرج يُخزَّن. لم يُنفَّذ تشغيل حي من بيئة المنفذ (لا مفتاح؛ `respx` بأجسام حقيقية الشكل في `test_b2_gateway.py`).
- **انحرافات (بالسطر والسبب):**
  1. **§2 B-2 «`ErrorCategory.invalid_output` الموجودة ضمن الـ12»:** لا فئة بهذا الاسم في عقد D-067 (الـ12 ثابتة ومُختبَرة في `test_contract.py`)؛ فشل تحليل JSON في الواجهة ⇒ `NON_RETRYABLE_ERROR` مع `provider_code="invalid_output"`/`"not_object"`، وفي Brand Brain ⇒ `InvalidModelOutputError` ⇒ 502 `ai_invalid_output` مع `category: invalid_output`. لم أوسّع الـ12 حفاظاً على العقد.
  2. **§3-4 «grep في `public_fetch.py`»:** قائمة المنع وُضعت في `domain/hosts.py` (تُستخدم أيضاً في التحقق من `website_host`/`allowed_hosts` عند `POST /competitors`)؛ `public_fetch.py` يستوردها، لذا grep على `src` كاملاً يعطي ملفاً واحداً هو `hosts.py`.
  3. **عدد مسارات brain = 15** (§2 ذكر 14 ضمنياً): أضفت `GET /recommendations?status=` لأن `accept/dismiss` بلا طريقة لقراءة المعرّفات غير قابل للاستخدام.
  4. **`content-plan`:** الصفوف تُخزَّن `materialized` + `materialized_item_id` منذ الإنشاء (المسودة أُنشئت فعلاً) بدل `proposed` ثم `accept`؛ `accept` يبقى للتوصيات `content_plan_item` القادمة من `diagnose` (ينشئ المسودة عندها). محدّد `content-plan` 5/ساعة بدلو مستقل `brain:content-plan`.
  5. **`ContentService.create_draft` غير موجود** — استُخدم `create_item` ثم `update_body(provenance=…)` الموجودان.
  6. **`reply_template` accept** على علامة بلا `brand_profiles` ⇒ يُنشأ الملف v1 بدل 404.
  7. **PG يجب أن تكون UTF8** (`initdb -E UTF8`؛ `CREATE DATABASE … ENCODING 'UTF8' TEMPLATE template0`) — عنقود SQL_ASCII يفشل إدخال JSONB عربي (`UntranslatableCharacterError`)؛ لا تغيير في الكود.
  8. **B-1 تأكيد الحزمة الكاملة** تمّ بعد إنجاز B-3 (بيئة الصندوق أُعيد بناؤها ≈ 51 مرة)؛ النتيجة أعلاه على شجرة B-1 وحدها.
  9. `tests/modules/whatsapp/test_postgres_repository.py` أُضيف في Part A كي تبقى تغطية وظيفة `unit` (بلا PG) ≥ 95 بعد نقل منطق إلى محوّلات PG.
- **أسئلة:**
  1. هل يُعتمد `GET /recommendations` (المسار الـ15) أم يُدمج في `DiagnosisPublic` فقط؟
  2. هل تريد فئة `invalid_output` صريحة ضمن عقد D-067 (ترفع الـ12 إلى 13) في SPRINT-11؟
  3. `AI_PRICE_TABLE_JSON` غير موجود في `.env.template` (ملف غير محظور لكن لم يُطلب) — أُضيفه مع SPRINT-11؟

---

## 7. ما ينتظر المالك

1. 🔑 **تدوير توكن GitHub — الطلب العاشر.** التوكن ظهر في المحادثة مجدداً؛ يُعدّ مكشوفاً. بعد التدوير يُمرَّر للمخطط مرة واحدة في بداية الجلسة فقط.
2. 🛡️ حماية `main` بالفحوص الخمسة + منع الدفع المباشر (ما زالت غير مفعَّلة).
3. **M4 — App Review من Meta** لنطاقي `pages_manage_posts` و`instagram_content_publish`: **ابدأ الآن**. **بعد M6:** أضف `whatsapp_business_messaging`.
4. **M6 — الربط الحي:** WABA + رقم + توكن نظام + `app_secret` (الدليل خطوة بخطوة في `sprints/SPRINT-09_REVIEW.md` §2 ← §6-12 من توجيه SPRINT-09). **انتظر دمج Part A من SPRINT-10** قبل أول ربط حي (N1/N2/N3 تمسّ التشغيل).
5. **M7 — الآن:** مزوّد OpenAI-compatible حقيقي (مفتاح + `AI_PROVIDER_BASE_URL`) في بيئتك فقط؛ CI يبقى `fake`. وجدول أسعار اختياري `AI_PRICE_TABLE_JSON` لتقدير التكلفة.
6. **قرارك عند إصدار SPRINT-11 (مؤجَّل #19):** هل نُدرج **M9.5** (واجهة رقيقة + تفعيل الباقات D-073) قبل بنود M9 غير الحرجة؟ رأي المخطط: نعم.
7. **المنافسون:** أدخل لاحقاً لكل علامة تجريبية ≤ 3 منافسين (اسم + `facebook_page_id` العام إن وُجد + مضيف الموقع) — هذا ما يغذّي أدلة `estimated`.
8. **للعلم (قرارات المخطط على سؤالَي المنفذ في SPRINT-09):** `wamid` يُعرض لـOWNER/MANAGER فقط (A-6)؛ `language="auto"` يُشتق خادمياً ويُثبَّت كـ`language_detected` خاص (A-6).
