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

- **حالة التنفيذ:** `[ ] مكتمل` / `[ ] جزئي`
- **Commit SHA Part A:** `…` — **B-1:** `…` — **B-2:** `…` — **B-3:** `…`
- **PR:** `#…` — **CI:** `…/5`
- **الاختبارات:** وحدة … / تكامل … / الكل … — **التغطية:** …%
- **A-1..A-6 (بالسطر):**
- **B-1 / B-2 / B-3 (بالسطر):**
- **اختبارات SPRINT-09 المعدَّلة (السبب لكل واحد):**
- **القبول §3 (نواتج الـgreps حرفياً + `PROTECTED_ROUTE_COUNT` النهائي وقائمته):**
- **تقرير التشغيل الحي للمزوّد (بلا أي سرّ):** المتغيرات المطلوبة، كيف يُختبر `openai_compatible` ضد مزوّد حقيقي من بيئة المالك، وماذا يظهر في `GET /brain/cost`.
- **انحرافات (بالسطر والسبب):**
- **أسئلة:**

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
