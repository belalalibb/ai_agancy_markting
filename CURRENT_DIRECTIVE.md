# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-08 رُوجع مستقلاً و**قُبل ودُمج** بواسطة المخطط (`main` @ `e414c5a`؛ التقرير في [`sprints/SPRINT-08_REVIEW.md`](sprints/SPRINT-08_REVIEW.md) — اقرأ §3 (جوابا سؤاليك) و**§4 كاملاً** (ثلاث ثغرات أُثبتت بالتجربة ويُغلقها Part A هنا). هذا التوجيه يُصدر **SPRINT-09 = الجزء A (F1/F2/F3) + المحطة M6 (WhatsApp Cloud API)**.

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-09-WHATSAPP-CONVERSATIONS` |
| **النوع** | Part A: إغلاق ثلاث ثغرات سلوكية موروثة من SPRINT-07 (F1 CHECK مقابل الأرشفة · F2 إعادة POST ذي أثر جانبي — **D-070** · F3 قفل عبر الشبكة + مسح `media_refs`) · Part B: **M6 — محادثات WhatsApp Cloud API**: Webhook موقَّع، تكرار `wamid`، نافذة 24 ساعة مقابل القوالب، صندوق وارد موحَّد معزول بالعلامة، **ردّ مقترح بالذكاء الاصطناعي خلف موافقة بشرية (D-055) + إشارة تعلّم (D-069)**. عميل خفيف فوق `core/http.py` (**D-071** — لا PyWa كتبعية). **كل الاختبارات تعمل في CI بلا WABA حية.** |
| **الحالة** | `ISSUED` — جاهز للتنفيذ فور دمج هذا التوجيه في `main` (المخطط يدمجه بنفسه — D-063) |
| **يبدأ من** | `main` @ `df4f434` (دمج PR #27 — هذا التوجيه). أول أمر: `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` |
| **الفرع المستهدف** | `work/implementation` ⇒ PR إلى `main` بعنوان `SPRINT-09: WhatsApp conversations (M6) + SPRINT-07 latent fixes (F1/F2/F3)` |
| **الكوميتات** | **أربعة**: Part A ⇒ B-1 (العميل + Webhook + التخزين + هجرة 0009) ⇒ B-2 (صندوق الوارد + الإرسال + النافذة/القوالب) ⇒ B-3 (ردّ مقترح + موافقة + إشارة تعلّم). كل كوميت يمرّ الحزمة كاملة وحده |
| **تبعيات جديدة** | **لا شيء.** `hmac`/`hashlib` من المكتبة القياسية للتوقيع؛ `httpx` عبر `core/http.py` فقط. **`pywa` ممنوعة** كاستيراد (أضفها إلى `banned-api`) — تُقرأ كمرجع فقط (D-071) |
| **الحجم المتوقع** | Part A ~250 سطراً · Part B ~1400 سطر كود + ~1500 سطر اختبارات |
| **تاريخ الإصدار** | 2026-10-31 |

---

## 0. لماذا؟

1. **F1 (مراجعة §4-F1 — مُثبَت بـ`psql`):** CHECK `ck_content_items_published_at_iff_published` (0006) يرفض `archived` مع `published_at`، و`compare_and_set_status` يحتفظ به عمداً ⇒ أرشفة أي منشور منشور فعلياً = `CheckViolationError` = 500. آلة الحالات تُعلن الانتقال وقاعدة البيانات تمنعه.
2. **F2 (مراجعة §4-F2 — مُثبَت بـ`respx`: POST `/feed` استُدعي مرتين على 502 وعلى `ReadTimeout`):** `_request_absolute` يُعيد كل الطرق. إن كان الفشل **بعد** أن نفّذ Graph النشر ⇒ منشور مكرَّر، أي خرق D-055 وإفراغ مفهوم «النتيجة المبهمة» من معناه. القرار **D-070**: إعادة المحاولة قرار دلالي — GET فقط؛ POST ذو أثر جانبي يُرسَل مرة ويُصنَّف قبل/بعد الإرسال. **يُطبَّق على عميل WhatsApp من اليوم الأول.**
3. **F3 (مراجعة §4-F3 — مُثبَت على PG: المراجعة 2 `media_refs='[]'`):** `generate()` يكتب مراجعة بلا `media_refs` فيمسح صورة إنستغرام؛ ويحمل `FOR UPDATE` + اتصال PG طوال `gateway.complete()`.
4. **M6:** أول قناة **ثنائية الاتجاه** في المنصة. القيمة للعميل الأول (وكالة/علامة عربية): كل محادثات واتساب لكل علامة في صندوق واحد، مع ردّ مقترح يوفّر الوقت **لكن لا يُرسَل شيء إلى عميل حقيقي دون موافقة بشر** (D-055) — وكل موافقة/تعديل/رفض على الردّ المقترح يُغذّي حلقة التعلّم (D-069) بإشارات مجرَّدة لا نصوصاً. الاعتماد (توكن النظام + `phone_number_id` + `app_secret` للتوقيع) **لكل علامة BYOK** مشفَّر بـ`TokenEncryptionService` (D-058)، فتعمل الدورة كاملة في CI بلا WABA، ويكفي حقن الاعتماد لتعمل حياً.

---

## 1. النطاق

**داخل:** A-1..A-3؛ وحدة `src/modules/whatsapp/` (عميل Cloud API خفيف، Webhook GET/POST، `conversations`/`messages`/`whatsapp_accounts`/`reply_drafts` + إشارة تعلّم، صندوق وارد، إرسال نص داخل النافذة، إرسال قالب خارجها، ردّ مقترح عبر `AIGateway` بغرض جديد `reply_suggestion`، موافقة/تعديل/رفض)؛ هجرتان: `0008_published_at_check` (Part A) و`0009_whatsapp` (B-1)؛ جدول رؤية؛ `banned-api` += `pywa`.
**خارج (ممنوع):** تنزيل/تخزين وسائط الرسائل (نخزّن `media_id` + النوع فقط؛ التنزيل M7+)؛ Flows/الرسائل التفاعلية (أزرار/قوائم)؛ إدارة القوالب (إنشاء/تعديل — نُرسل قالباً **بالاسم** فقط)؛ تسجيل WABA عبر Embedded Signup (الاعتماد يُدخل يدوياً)؛ ردّ **تلقائي** بلا موافقة (حتى خلف إعداد)؛ أي لمس لـ`ai_gateway/**` عدا إضافة `"reply_suggestion"` إلى `Purpose` وقائمة `banned-api`؛ تغيير آلة حالات المحتوى (تبقى 56 خلية)؛ تفعيل منصة ثالثة من مصفوفة M5 (مؤجَّل #17).

---

## 2. المواصفات

### 2-0. القواعد الحاكمة (D-055 / D-064 / D-068 / D-070 — إلزامية)

**(أ) لا رسالة إلى عميل حقيقي بلا فاعل بشري:** كل `POST …/messages` صادر يحمل `sent_by_user_id` غير فارغ؛ الردّ المقترح يُخزَّن في `reply_drafts` بحالة `suggested` ولا يلمس الشبكة؛ **لا** إعداد من نوع `WHATSAPP_AUTO_REPLY`. اختبار حارس: `grep -rn "auto_reply\|AUTO_REPLY" src` ⇒ فارغ.

**(ب) توقيع Webhook إلزامي ولا تجاوز:** `POST /webhooks/whatsapp` يرفض بـ**401** أي جسم بلا `X-Hub-Signature-256` صالح (`hmac.compare_digest(sha256(app_secret, raw_body))`)؛ لا إعداد لتعطيل التحقق. الجسم الخام يُقرأ **مرة واحدة** قبل أي تحليل JSON. التحقق يتم ضدّ `app_secret` **العلامة** المطابقة لـ`phone_number_id` في الحمولة — وإن لم يُعرف `phone_number_id` ⇒ 401 أيضاً (لا يُسرَّب أيّ تفصيل).

**(ج) D-064 في العميل:** توكن النظام في `Authorization: Bearer` فقط؛ لا سرّ في query string ولا في السجلات؛ `WhatsAppAccount.encrypted_system_token` بـ`repr=False`؛ التوكن يُفكّ لحظة النداء و`del` في `finally` (نمط `PostgresPageTokenProvider`). نص الرسائل **لا يُسجَّل** في أي `logger` (معرّفات وأطوال فقط).

**(د) D-070 في العميل من اليوم الأول:** `WhatsAppCloudClient` يُعيد **GET فقط**؛ `POST /{phone_number_id}/messages` يُرسَل مرة واحدة؛ فشل قبل الإرسال ⇒ `WhatsAppTransientError`، بعد الإرسال ⇒ `WhatsAppAmbiguousError`؛ المستدعي يسجّل `messages.delivery_status='ambiguous'` ولا يُعيد تلقائياً.

**(هـ) D-068 يمتد:** `banned-api` += `pywa` (رسالة: «Read-only reference — D-071; use src.modules.whatsapp.client»)؛ `per-file-ignores` += `"src/modules/whatsapp/client.py" = ["TID251"]` (الملف الوحيد الجديد المسموح له بـ`httpx`). grep قبول: `grep -rln "import httpx" src | sort` ⇒ الأربعة الحالية + `src/modules/whatsapp/client.py` **فقط**.

**(و) جدول الرؤية (D-064) لوحدة `whatsapp`:**

| الحقل | OWNER | MANAGER | VIEWER |
|---|---|---|---|
| `conversations.*` (رقم العميل `wa_id`، الاسم، آخر رسالة، `window_expires_at`) | ✅ | ✅ | ✅ |
| `messages.body` (نص وارد/صادر) | ✅ | ✅ | ✅ |
| `reply_drafts.suggested_body` + `status` | ✅ | ✅ | ✅ (قراءة فقط — لا موافقة) |
| `reply_drafts.provenance` الخاص (`provider`, `model`, `prompt_hash`, tokens, latency) | ✅ | ✅ | ❌ |
| `whatsapp_accounts.phone_number_id`, `display_phone`, `status` | ✅ | ✅ | ❌ |
| `whatsapp_accounts.encrypted_system_token`, `encrypted_app_secret` | ❌ **أبداً في أي استجابة** | ❌ | ❌ |
| إنشاء/حذف حساب واتساب للعلامة | ✅ | ❌ | ❌ |
| إرسال رسالة / الموافقة على ردّ مقترح / تعديله / رفضه | ✅ | ✅ | ❌ (403) |

---

### Part A — إلزامي قبل أي ميزة (كوميت واحد)

| # | البند | التنفيذ المطلوب | الاختبار |
|---|---|---|---|
| **A-1** F1 | CHECK `published_at` | هجرة `0008_published_at_check.py`: `drop_constraint("ck_content_items_published_at_iff_published")` ثم `create_check_constraint` بالاسم نفسه والشرط: `(status = 'published' AND published_at IS NOT NULL) OR (status = 'archived') OR (status NOT IN ('published','archived') AND published_at IS NULL)`. `downgrade`: `UPDATE content_items SET published_at = NULL WHERE status = 'archived'` ثم إعادة CHECK القديم. **لا تغيير في `compare_and_set_status`** — الاحتفاظ بـ`published_at` في الأرشيف **مقصود** (أثر تدقيقي) | **A1-I1** (PG): عنصر يصل `published` عبر المجدول بـ`FakePublisher` ثم `POST …/archive` ⇒ 200، الصف `archived` و`published_at` **غير فارغ**؛ **A1-I2** (PG): `scheduled` ⇒ `archive` ⇒ `published_at IS NULL`؛ **A1-M1**: `head ⇒ 0007 ⇒ head` بصف `archived` يحمل `published_at` — التخفيض يُصفّره ولا يفشل؛ إدراج `draft` بـ`published_at` ⇒ يُرفض عند head |
| **A-2** F2 / D-070 | فصل دلالات الإعادة في `MetaGraphClient` | `_request_absolute(method, …, *, idempotent: bool)` — GET ⇒ `idempotent=True` (الحلقة الحالية تبقى حرفياً)؛ POST ⇒ `idempotent=False`: **محاولة واحدة**. التصنيف: `httpx.ConnectError`/`httpx.ConnectTimeout` ⇒ `MetaTransientError` (لم يُرسل)؛ `httpx.ReadTimeout`/`httpx.WriteTimeout`/`httpx.RemoteProtocolError`/5xx/أكواد 1,2 ⇒ **`MetaAmbiguousError(MetaClientError)`** جديد. `MetaPublisher`: `MetaAmbiguousError` ⇒ `PublishOutcome.ambiguous(...)` (المسار الموجود من SPRINT-07 B6) — يُغلق `publish_failed` بمحاولة `ambiguous`، لا إعادة تلقائية. GET يبقى يُعيد `MetaTransientError` القابل للإعادة | **A2-U1** (`respx`): `publish_page_post` مع 502 ثم 200 ⇒ `MetaAmbiguousError` و**عدد نداءات POST == 1**؛ **A2-U2**: `ReadTimeout` ⇒ المثل؛ **A2-U3**: `ConnectError` ⇒ `MetaTransientError` ونداء واحد؛ **A2-U4**: `GET /me/accounts` مع 502 ثم 200 ⇒ ينجح بنداءين (السلوك القديم محفوظ)؛ **A2-U5**: `create_instagram_container` و`publish_instagram_container` كذلك نداء واحد؛ **A2-I1** (PG): المجدول مع ناشر يرمي `MetaAmbiguousError` ⇒ `publish_attempts.outcome='ambiguous'`، المهمة `failed`، العنصر `publish_failed`، **لا** دورة لاحقة تُعيدها |
| **A-3** F3 | `generate()` يحفظ الوسائط ولا يقفل عبر الشبكة | (أ) `add_revision(..., media_refs=<media_refs المراجعة الحالية>)` في `generate()`؛ `ContentBodyUpdate.media_refs: list[MediaRef] \| None = None` — `None` = أبقِ الحالية، `[]` = احذف (كسر توافق صريح: `update_body` يمرّر `None` ⇒ المستودع ينسخ من المراجعة الحالية). (ب) ترتيب `generate()`: `get_item` (بلا قفل) ⇒ فحص `GENERATION_STATES` ⇒ limiter ⇒ budget ⇒ `gateway.complete()` ⇒ **ثم** `_lock_item` ⇒ **إعادة فحص** `GENERATION_STATES` (تغيّر ⇒ 409 `invalid_transition`) ⇒ CAS/`add_revision` | **A3-U1**: عنصر IG بصورة ⇒ `/generate` ⇒ المراجعة الجديدة تحمل الصورة نفسها؛ **A3-U2**: `PUT body` بلا `media_refs` ⇒ تبقى؛ بـ`[]` ⇒ تُحذف؛ **A3-I1** (PG): بوابة وهمية تفتح جلسة ثانية وتنفّذ `SELECT … FOR UPDATE NOWAIT` على الصف أثناء `complete()` ⇒ **تنجح** (القفل غير محمول)؛ **A3-U3**: بوابة وهمية تُغيّر حالة العنصر إلى `pending_approval` أثناء `complete()` ⇒ 409 ولا مراجعة تُكتب |

> **قاعدة Part A:** لا تغيير سلوكي خارج ما ذُكر. **اختبار SPRINT-07 الذي يتحقق من «إعادة محاولة POST» (إن وُجد) يُعدَّل ويُوثَّق في §6 — هذا التغيير السلوكي مقصود (D-070).**

---

### Part B-1 — العميل + Webhook + التخزين + هجرة 0009 (كوميت واحد)

**B1-أ. الهجرة `0009_whatsapp.py`:**

| الجدول | الأعمدة | القيود |
|---|---|---|
| `whatsapp_accounts` | `id` UUID PK، `brand_id` → brands (CASCADE)، `phone_number_id` VARCHAR(32)، `waba_id` VARCHAR(32)، `display_phone` VARCHAR(32)، `encrypted_system_token` TEXT، `encrypted_app_secret` TEXT، `verify_token_hash` TEXT (SHA-256 لرمز التحقق GET)، `status` ∈ {active, disabled}، `created_by_user_id`، `created_at`/`updated_at` | **UNIQUE `phone_number_id`** (رقم واحد = علامة واحدة — مفتاح توجيه Webhook)؛ UNIQUE (`brand_id`) في V1 (حساب واحد للعلامة؛ يُرفع لاحقاً)؛ CHECK status |
| `wa_conversations` | `id`، `brand_id` → brands (CASCADE)، `account_id` → whatsapp_accounts (CASCADE)، `wa_id` VARCHAR(32) (رقم العميل E.164 بلا +)، `profile_name` VARCHAR(128) NULL، `last_inbound_at` TIMESTAMPTZ NULL، `last_outbound_at` NULL، `window_expires_at` NULL (= `last_inbound_at + 24h`)، `unread_count` INT ≥ 0، `status` ∈ {open, archived}، `created_at`/`updated_at` | UNIQUE (`account_id`, `wa_id`)؛ فهرس (`brand_id`, `status`, `last_inbound_at DESC`) للصندوق |
| `wa_messages` | `id`، `conversation_id` → wa_conversations (CASCADE)، `brand_id`، `direction` ∈ {inbound, outbound}، `wamid` VARCHAR(128) NULL (وارد: من الحمولة؛ صادر: من استجابة Graph)، `client_message_id` UUID NULL (صادر: مفتاح idempotency داخلي)، `kind` ∈ {text, template, image, audio, video, document, sticker, location, contacts, unsupported}، `body` TEXT NULL (≤ 4096)، `media_id` VARCHAR(128) NULL، `template_name` VARCHAR(128) NULL، `delivery_status` ∈ {received, queued, sent, delivered, read, failed, ambiguous}، `error_code` INT NULL، `sent_by_user_id` UUID NULL، `reply_draft_id` UUID NULL، `platform_timestamp` TIMESTAMPTZ NULL، `created_at` | **فهرس جزئي فريد `uq_wa_messages_wamid (wamid) WHERE wamid IS NOT NULL`** (تكرار Webhook)؛ UNIQUE `client_message_id` WHERE NOT NULL؛ CHECK: `direction='outbound' ⇒ sent_by_user_id IS NOT NULL` (**قاعدة D-055 في قاعدة البيانات**)؛ CHECK kind/status |
| `wa_reply_drafts` | `id`، `conversation_id` → (CASCADE)، `brand_id`، `in_reply_to_message_id` → wa_messages، `suggested_body` TEXT، `final_body` TEXT NULL، `status` ∈ {suggested, approved, edited, rejected, expired}، `provenance` JSONB (نمط D-066: عام `{kind:"ai_suggested", generated_at, prompt_version}` + خاص `{provider, model, prompt_hash, prompt_tokens, completion_tokens, latency_ms, credential_mode}`)، `decided_by_user_id` NULL، `decided_at` NULL، `rejection_reason` VARCHAR(32) NULL ∈ {off_tone, factually_wrong, too_long, inappropriate, other}، `created_at` | CHECK: `status IN ('approved','edited','rejected') ⇒ decided_by_user_id IS NOT NULL`؛ فهرس (`conversation_id`, `status`) |
| `wa_learning_signals` | `id`، `brand_id`، `reply_draft_id` → wa_reply_drafts (CASCADE)، `signal` ∈ {approved_unchanged, approved_after_edit, rejected, expired}، `edit_distance_ratio` NUMERIC(4,3) NULL (0–1، لـ`approved_after_edit`)، `rejection_reason` NULL، `prompt_version` VARCHAR(16)، `model` VARCHAR(64)، `credential_mode` VARCHAR(16)، `features` JSONB (سمات مجرَّدة: `suggested_len`, `final_len`, `language`, `has_question`, `has_cta` — **لا نص**)، `created_at` | UNIQUE `reply_draft_id` (إشارة واحدة لكل مسودة)؛ **D-069: لا عمود نص** — اختبار حارس على أسماء الأعمدة |

`downgrade`: إسقاط الجداول الخمسة بالترتيب العكسي. **لا تمسّ 0001–0008.**

**B1-ب. `src/modules/whatsapp/client.py` — `WhatsAppCloudClient(settings, http_client, *, sleep)`** (نمط `MetaGraphClient`؛ الـbase `https://graph.facebook.com/{meta_graph_version}`):
- `send_text(token, phone_number_id, to_wa_id, body) -> SentMessage{wamid}` — POST `/{phone_number_id}/messages` بجسم `{"messaging_product":"whatsapp","recipient_type":"individual","to":…,"type":"text","text":{"preview_url":false,"body":…}}`؛ **D-070: محاولة واحدة**.
- `send_template(token, phone_number_id, to_wa_id, name, language_code, components=None)` — POST بنوع `template`؛ محاولة واحدة.
- `mark_read(token, phone_number_id, wamid)` — POST `{"status":"read","message_id":…}`؛ محاولة واحدة (أثره الجانبي حميد لكن القاعدة واحدة).
- الأخطاء: `WhatsAppClientError` ⇒ `WhatsAppTransientError` (قبل الإرسال)، `WhatsAppAmbiguousError` (بعده)، `WhatsAppRateLimitedError` (كود 130429 / 80007 / HTTP 429 — **لا إعادة**)، `WhatsAppRecipientError` (131026 خارج النافذة/غير مسموح، 131047 انتهت النافذة، 131051 نوع غير مدعوم)، `WhatsAppAuthError` (190، 10 ⇒ الحساب `disabled` + `required_action=reauth`)، `WhatsAppPermanentError`. **لا نص Meta في أي نموذج استجابة** — الكود والـ`fbtrace_id` فقط.
- السجلات: `logger.info("wa send: brand=%s conv=%s kind=%s len=%d status=%s")` — **لا نص ولا رقم هاتف كاملاً** (آخر 4 أرقام مقنَّعة `…1234`).

**B1-ج. Webhook — `src/modules/whatsapp/webhook.py`** (مسارات **عامة** بلا JWT — تُضاف إلى `PUBLIC` في `tests/test_auth_guard.py` بتعليق السبب):
- `GET /api/v1/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=…&hub.challenge=…` ⇒ يبحث عن حساب `active` يطابق `sha256(verify_token)` ⇒ `200 text/plain challenge`؛ وإلا **403** (لا تفصيل).
- `POST /api/v1/webhooks/whatsapp`: اقرأ `await request.body()` **مرة** ⇒ تحقّق (ب) ⇒ حلّل `entry[].changes[].value` ⇒ لكل `messages[]`: `INSERT … ON CONFLICT (wamid) DO NOTHING` (تكرار ⇒ لا شيء، **200**)؛ upsert `wa_conversations` (`last_inbound_at`، `window_expires_at = ts + 24h`، `unread_count+1`، `profile_name` من `contacts[]`)؛ `kind` من `type` (غير معروف ⇒ `unsupported` بجسم فارغ)؛ لكل `statuses[]`: تحديث `delivery_status` للرسالة الصادرة بـ`wamid` المطابق (`sent/delivered/read/failed` + `errors[0].code`). **يُعيد 200 دائماً بعد التحقق** حتى على حمولة غير مفهومة (Meta تُعيد الإرسال على غير 200) — يُسجَّل `counters["wa_webhook_unparsed"]`. المعالجة **داخل الطلب** (لا طابور في V1؛ Meta تمنح 20 ثانية) — دالة `handle_webhook_payload(payload) -> WebhookOutcome` منفصلة قابلة للنقل إلى طابور لاحقاً.
- `counters`: `wa_inbound`, `wa_duplicate`, `wa_status_updates`, `wa_webhook_rejected`.

**B1-د. `src/modules/whatsapp/service.py::WhatsAppAccountService`:** `connect_account(brand_id, actor, *, phone_number_id, waba_id, display_phone, system_token, app_secret, verify_token)` (OWNER فقط؛ يشفّر الثلاثة؛ **يتحقق من التوكن بنداء واحد GET `/{phone_number_id}` `fields=display_phone_number,verified_name`** — GET قابل للإعادة؛ فشل ⇒ 422 `whatsapp_credentials_invalid` ولا صف يُكتب)، `disable_account`, `get_account` (بلا أسرار). مزوّد رموز `PostgresWhatsAppTokenProvider.resolve(account_id) -> ResolvedWhatsAppTarget{phone_number_id, token(repr=False), app_secret(repr=False)}`.

**اختبارات B-1:** **B1-U1** توقيع: جسم صحيح ⇒ 200؛ توقيع خاطئ/غائب/لـ`phone_number_id` مجهول ⇒ 401 ولا صف؛ تعديل بايت واحد بعد التوقيع ⇒ 401. **B1-U2** تكرار: الحمولة نفسها مرتين ⇒ رسالة واحدة و`counters["wa_duplicate"]==1`. **B1-U3** حمولة `statuses` تُحدّث `delivery_status` لرسالة صادرة وتتجاهل `wamid` مجهول. **B1-U4** `kind` غير معروف ⇒ `unsupported` بلا 500. **B1-U5** عزل: حمولتان لحسابين بعلامتين ⇒ كل محادثة تحت علامتها (استعلام العلامة الأخرى يُعيد 0). **B1-U6** عميل (`respx`): `send_text` 502 ⇒ `WhatsAppAmbiguousError` ونداء POST **واحد**؛ `ConnectError` ⇒ `Transient` ونداء واحد؛ 131047 ⇒ `RecipientError`؛ 190 ⇒ `AuthError`؛ `GET /{phone_number_id}` 502 ثم 200 ⇒ ينجح بنداءين. **B1-U7** `connect_account` بتوكن يُرجع 190 ⇒ 422 ولا صف؛ `repr(ResolvedWhatsAppTarget)` لا يحوي التوكن. **B1-U8** حارس D-069: أعمدة `wa_learning_signals` لا تحوي `body|text|content`. **B1-M1** (PG): `head ⇒ 0008 ⇒ head`؛ إدراج `outbound` بلا `sent_by_user_id` ⇒ يُرفض؛ `wamid` مكرَّر ⇒ يُرفض؛ `rejected` بلا `decided_by_user_id` ⇒ يُرفض. **B1-I1** (PG): Webhook حقيقي عبر `TestClient` بتوقيع محسوب بـ`app_secret` العلامة ⇒ صفوف `wa_conversations`/`wa_messages` كما هو متوقع؛ الحمولة نفسها ثانيةً ⇒ لا تغيير (`ON CONFLICT`).

> **ثوابت الحمولة** تُستخلص من وثائق Cloud API v26.0 (`messages`, `statuses`, `contacts`, `metadata.phone_number_id`) وتوضع كـfixtures في `tests/modules/whatsapp/payloads/*.json` — **ليست من PyWa**.

---

### Part B-2 — صندوق الوارد + الإرسال + النافذة/القوالب (كوميت واحد)

**مسارات (كلها خلف `CurrentUser` + دور العلامة؛ `/api/v1/brands/{brand_id}/whatsapp/…`):**

| المسار | الدور | السلوك |
|---|---|---|
| `POST /account` | OWNER | B1-د `connect_account` ⇒ 201 بلا أسرار |
| `GET /account` | OWNER/MANAGER | الحساب بلا أسرار؛ VIEWER ⇒ 403 |
| `DELETE /account` | OWNER | `status=disabled` (لا حذف — المحادثات تبقى) |
| `GET /conversations?status=open&cursor=&limit=` | الجميع | مرتَّب `last_inbound_at DESC`؛ cursor keyset؛ كل صف: `id, wa_id (مقنَّع لـVIEWER: `…1234`), profile_name, last_message_preview (≤ 80 حرفاً), unread_count, window_open: bool, window_expires_at, pending_draft: bool` |
| `GET /conversations/{id}/messages?cursor=&limit=` | الجميع | الرسائل تصاعدياً + `reply_drafts` المرتبطة؛ VIEWER يرى `suggested_body`/`status` بلا `provenance` الخاص |
| `POST /conversations/{id}/read` | OWNER/MANAGER | `unread_count=0` + `mark_read` لآخر وارد (فشل الشبكة لا يفشل الطلب — يُسجَّل) |
| `POST /conversations/{id}/messages` `{body}` | OWNER/MANAGER | **داخل النافذة فقط** (`window_expires_at > now`) وإلا **422 `whatsapp_window_closed`** مع `template_required: true`؛ يُدرج `outbound` بحالة `queued` بـ`client_message_id` **قبل** النداء (نمط `publish_attempts`) ⇒ `send_text` ⇒ `sent` + `wamid`؛ `Ambiguous` ⇒ `ambiguous` (**لا إعادة**؛ 202 `{delivery_status:"ambiguous"}`)؛ `Transient` ⇒ `failed` + 503 `whatsapp_transient` + `Retry-After: 30`؛ `RateLimited` ⇒ 429؛ `RecipientError` ⇒ 422 `whatsapp_recipient_rejected{code}`؛ `AuthError` ⇒ الحساب `disabled` + 409 `whatsapp_reauth_required` |
| `POST /conversations/{id}/template` `{name, language_code, components?}` | OWNER/MANAGER | يعمل داخل النافذة وخارجها؛ نفس مسار الحالات أعلاه؛ `kind=template` |
| `POST /conversations/{id}/archive` | OWNER/MANAGER | `status=archived`؛ رسالة واردة جديدة تُعيدها `open` تلقائياً (Webhook) |

**قواعد:** نص الرسالة ≤ 4096 حرفاً (`ContentValidationError` 422)؛ `wa_id` يُقنَّع لـVIEWER في كل استجابة (اختبار حارس على كل حقول VIEWER بالـregex `^\.{3}\d{4}$`)؛ `Idempotency-Key` اختياري في الهيدر ⇒ يُستخدم كـ`client_message_id` (تكرار ⇒ 200 بالرسالة القائمة، لا إرسال ثانٍ).

**اختبارات B-2:** **B2-U1** جدول الرؤية حرفياً لكل دور × كل مسار (403 حيث ❌؛ قناع `wa_id` لـVIEWER؛ لا `encrypted_*` في أي استجابة — مشي على كل المفاتيح). **B2-U2** نافذة: `last_inbound_at = now-23h` ⇒ إرسال نص 201؛ `now-25h` ⇒ 422 `whatsapp_window_closed` ولا نداء شبكي (`respx` بلا مسارات ⇒ أي نداء يفشل الاختبار)؛ القالب ينجح في الحالتين. **B2-U3** الحالات الست للعميل ⇒ أكواد HTTP وحالات `delivery_status` كما في الجدول؛ `Ambiguous` ⇒ **لا** نداء ثانٍ. **B2-U4** `Idempotency-Key` مكرَّر ⇒ رسالة واحدة ونداء واحد. **B2-U5** عزل: MANAGER على العلامة A يطلب محادثة من العلامة B ⇒ 404 (لا 403 — لا تسريب وجود). **B2-I1** (PG): الدورة كاملة — Webhook وارد ⇒ `GET /conversations` يُظهرها بـ`window_open=true` ⇒ إرسال ردّ ⇒ Webhook `statuses` `delivered` ⇒ `GET …/messages` يُظهر `delivered`؛ ثم `UPDATE last_inbound_at = now()-25h` ⇒ إرسال نص 422، قالب 201. **B2-I2** (PG): keyset pagination على 25 محادثة بـ`limit=10` ⇒ 3 صفحات بلا تكرار ولا فقد.

**`PROTECTED_ROUTE_COUNT`:** 34 ⇒ **34 + عدد المسارات المحمية الجديدة** (B-2: 8، B-3: 3 ⇒ **45**)؛ مسارا Webhook في `PUBLIC` بتعليق.

---

### Part B-3 — ردّ مقترح خلف موافقة + إشارة تعلّم (كوميت واحد)

- **`Purpose`** في `ai_gateway/gateway.py`: `Literal["content_draft", "reply_suggestion"]` (التغيير الوحيد في البوابة).
- **`src/modules/whatsapp/prompts.py`:** `reply_system_prompt(brand_display_name, language, tone)` + `reply_user_prompt(last_messages: list[tuple[direction, body]], max_turns=6)`؛ `REPLY_PROMPT_VERSION = "wa-reply-v1"`؛ القالب يُلزم: الردّ بلغة العميل، ≤ 600 حرف، لا وعود سعرية/قانونية، سؤال توضيحي واحد عند الغموض.
- **`POST /conversations/{id}/suggest-reply`** (OWNER/MANAGER): آخر رسالة **واردة** فقط (لا مسودة لمحادثة آخرها صادر ⇒ 409 `no_inbound_to_reply`)؛ مسودة `suggested` قائمة ⇒ 409 `draft_pending` (لا تكديس)؛ `limiter.hit("wa_suggest", brand_id, 60, 3600)` ⇒ 429؛ ميزانية الرموز اليومية **نفسها** `AI_DAILY_TOKEN_BUDGET_PER_BRAND` (تُجمع من `content_revisions.provenance` **و**`wa_reply_drafts.provenance` معاً — استعلام واحد UNION ALL)؛ `gateway.complete(CompletionRequest(purpose="reply_suggestion", …, max_tokens=400, temperature=0.5))` ⇒ صف `wa_reply_drafts(status=suggested)`؛ **لا نداء WhatsApp**. `counters["wa_suggestions"]`.
- **`POST /reply-drafts/{draft_id}/approve`** `{final_body?: str}` (OWNER/MANAGER): `final_body` غائب أو مطابق ⇒ `approved` + إشارة `approved_unchanged`؛ مختلف ⇒ `edited` + `approved_after_edit` مع `edit_distance_ratio = 1 - SequenceMatcher(None, a, b).ratio()` (مُقرَّب 3 منازل)؛ **ثم** الإرسال عبر مسار B-2 نفسه (النافذة/القالب/الحالات؛ `messages.reply_draft_id` يُربط) — إن كانت النافذة مغلقة ⇒ 422 `whatsapp_window_closed` **والمسودة تبقى `approved` بلا إرسال** (المستخدم يُرسل قالباً يدوياً). `features` تُحسب بدوال نقية في `signals.py` (`suggested_len`, `final_len`, `language ∈ {ar,en,mixed,other}` بنسبة الحروف العربية، `has_question` = يحوي `?`/`؟`، `has_cta` = قائمة أفعال عربية/إنجليزية ثابتة) — **لا نص يُخزَّن في الإشارة** (اختبار حارس يُسلسل الصف ويبحث عن `suggested_body`).
- **`POST /reply-drafts/{draft_id}/reject`** `{reason}` (OWNER/MANAGER) ⇒ `rejected` + إشارة `rejected`.
- **انتهاء:** مسودة `suggested` تصبح `expired` إذا وصلت رسالة واردة أحدث (في Webhook) أو أُرسل ردّ يدوي ⇒ إشارة `expired` (D-069: «تجاهُل» إشارة أيضاً).
- **رؤية `provenance`:** `public_provenance()` القائمة في `content/domain/models.py` **تُعاد استخدامها** (نُقلت إلى `core/` إن لزم بلا تغيير سلوك) — VIEWER لا يرى المفاتيح الخاصة.

**اختبارات B-3:** **B3-U1** `suggest-reply` ينشئ مسودة بـ`provenance.kind == "ai_suggested"` و`prompt_version == "wa-reply-v1"` و**صفر** نداءات WhatsApp. **B3-U2** الحالات: آخر رسالة صادرة ⇒ 409؛ مسودة قائمة ⇒ 409؛ 61 طلباً/ساعة ⇒ 429. **B3-U3** الإشارات الثلاث + `edit_distance_ratio` (مطابق ⇒ `approved_unchanged`، تغيير كلمة ⇒ `approved_after_edit` و`0 < ratio < 0.3`، تبديل كامل ⇒ `> 0.7`). **B3-U4** حارس D-069: سلسلة JSON لصف الإشارة لا تحوي أي 12 حرفاً متتالية من `suggested_body`/`final_body`. **B3-U5** الموافقة بنافذة مغلقة ⇒ 422، المسودة `approved`، لا رسالة، لا نداء. **B3-U6** VIEWER: يرى `suggested_body` ولا يرى `provenance.provider`؛ `approve` ⇒ 403. **B3-U7** وصول وارد أحدث ⇒ المسودة `expired` + إشارة. **B3-I1** (PG): الدورة — Webhook وارد ⇒ `suggest-reply` (`AI_PROVIDER=fake`) ⇒ `approve` بتعديل ⇒ رسالة `outbound` بـ`reply_draft_id` و`sent_by_user_id` و`delivery_status='sent'` (respx 200) ⇒ صف `wa_learning_signals` بـ`approved_after_edit` ⇒ الميزانية اليومية تحتسب رموز المسودة (استعلام UNION). **B3-I2** (PG): ميزانية 1 رمز ⇒ `suggest-reply` 429 `generation_budget_exceeded` (الكود نفسه من SPRINT-07 A-5).

---

## 3. معايير القبول

1. `ruff check src tests alembic scripts` + `ruff format --check …` نظيفان.
2. `pytest --cov=src --cov-fail-under=95` على PG حقيقية: **0 فشل**؛ العدد ≥ 830 + الجديدة (المتوقع ≥ 60)؛ CI 5/5 على الكوميت الأعلى.
3. `ls alembic/versions/` ⇒ 0001–**0009** (0008 Part A، 0009 B-1 فقط).
4. `grep -rln "import httpx" src | sort` ⇒ الأربعة + `src/modules/whatsapp/client.py` **فقط**؛ `grep -rn "pywa" src pyproject.toml` ⇒ سطر `banned-api` فقط.
5. `grep -rn "auto_reply\|AUTO_REPLY" src` ⇒ فارغ. `grep -rn "encrypted_system_token\|encrypted_app_secret" src/modules/whatsapp/schemas.py` ⇒ فارغ (لا نموذج استجابة يعرفهما).
6. **D-070 حرفياً:** `pytest -k "a2_u or b1_u6 or b2_u3" -q` ⇒ كلها تمرّ وكل اختبار منها يؤكد `len(route.calls) == 1` لأي POST.
7. الاختبارات بالأسماء كلها موجودة وناجحة: A1-I1..I2, A1-M1, A2-U1..U5, A2-I1, A3-U1..U3, A3-I1, B1-U1..U8, B1-M1, B1-I1, B2-U1..U5, B2-I1..I2, B3-U1..U7, B3-I1..I2.
8. `PROTECTED_ROUTE_COUNT == 45`؛ `tests/modules/content/test_lifecycle.py` ما زال **56 خلية**.
9. `pyproject.toml` تغييرات: `banned-api` += `pywa`، `per-file-ignores` += `client.py` — **لا تبعية**.
10. **لا متغير بيئة جديد إلزامي**: التطبيق يقلع ويمرّ كل الاختبارات بلا أي `WHATSAPP_*` (الاعتماد BYOK في `whatsapp_accounts`). الوحيد الاختياري: `WHATSAPP_WEBHOOK_PATH` (افتراضي `/api/v1/webhooks/whatsapp`).
11. أربعة كوميتات فوق `main`؛ `git diff <start> --name-only` خالٍ من الملفات المحظورة (`ARCHITECTURE_LOCK.md`, `SESSION_RECOVERY.md`, `imaster_planner.md`, `README.md`, `AI_Marketing_Platform_Hub/**`, `sprints/**`).
12. §6 مكتمل **بأسماء منسوخة من الكود** (درس 18) + **تقرير «جاهزية الربط الحي»**: قائمة الخطوات الدقيقة التي يفعلها المالك بعد الحصول على WABA (قيم `POST /account`، رابط Webhook ليُدخل في لوحة Meta، `verify_token`) — بلا أي سرّ.

---

## 4. أوامر

```bash
git checkout work/implementation && git fetch origin && git merge --ff-only origin/main
pip install -e ".[dev]"
export TEST_DATABASE_URL=postgresql+asyncpg://aigos@localhost:5433/ai_growth_os_test
export DATABASE_URL=$TEST_DATABASE_URL
export AUTH_JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
ruff check src tests alembic scripts && ruff format --check src tests alembic scripts
pytest --cov=src --cov-fail-under=95
ls alembic/versions/
grep -rln "import httpx" src | sort
grep -rn "pywa" src pyproject.toml
grep -rn "auto_reply\|AUTO_REPLY" src
pytest -k "a1_ or a2_ or a3_ or b1_u or b1_m or b1_i or b2_u or b2_i or b3_u or b3_i" -q
```

> **ملاحظة ساندبوكس (من المخطط):** إجراء إعادة البناء الكامل في `SESSION_RECOVERY.md` §2-ب. متغيرات `TEST_DATABASE_URL`/`AUTH_JWT_SECRET` تُصدَّر في **نفس سطر الأمر** الذي يُشغّل `pytest` (كل نداء Bash يبدأ بغلاف جديد).

---

## 5. التزامات المنفذ

1. **Part A** كوميت: `fix(content,social_connect): SPRINT-09 Part A — relax published_at CHECK for archived (0008, F1), no retry on side-effecting Graph POST + MetaAmbiguousError (D-070, F2), generate() keeps media_refs and locks after gateway (F3)` ⇒ ادفع.
2. **Part B-1** كوميت: `feat(whatsapp): SPRINT-09 Part B-1 — Cloud API client (D-070/D-071), signed webhook with wamid dedupe, accounts (BYOK, encrypted), migration 0009` ⇒ ادفع.
3. **Part B-2** كوميت: `feat(whatsapp): SPRINT-09 Part B-2 — unified inbox, send text within 24h window, templates outside, visibility table, idempotency` ⇒ ادفع.
4. **Part B-3** كوميت: `feat(whatsapp): SPRINT-09 Part B-3 — AI reply suggestion behind human approval (D-055) + learning signals (D-069)` ⇒ ادفع.
5. **ادفع بعد كل ملف** (`wip:` ثم سحق إلى الكوميتات الأربعة قبل التسليم).
6. املأ §6 بالسطر **وبأسماء منسوخة من الكود**؛ `WORK_STATE.md` (§1 ⇒ `SPRINT_09_DELIVERED_AWAITING_REVIEW`؛ سطر جديد أعلى §6). عند التسليم اكتب حرفياً: **«PR #N جاهز لمراجعة المخطط»**. لا تدمج (D-063).
7. أي بند متعذر ⇒ وثّق الانحراف بالسطر والسبب؛ **لا** تُضعف: التوقيع الإلزامي، «لا رسالة بلا فاعل بشري»، D-070 نداء واحد للـPOST، جدول الرؤية، «لا نص في الإشارات».
8. إن وجدت أن شكل حمولة Cloud API v26.0 يختلف عمّا افترضه هذا التوجيه (أسماء الحقول في `statuses`/`contacts`) ⇒ اتبع **الوثائق الرسمية** ووثّق الفرق في §6 مع رابط الصفحة — لا تُخمّن ولا تنسخ من PyWa.
9. إن تطلّب تنفيذ A-3 (ب) تغيير توقيع `AIGateway` أو `_lock_item` بما يمسّ اختبارات SPRINT-07 ⇒ **توقّف واسأل في §6**.

---

## 6. تقرير المنفذ (يملؤه المنفذ)

- **حالة التنفيذ:** `[x] مكتمل` / `[ ] جزئي`
- **Commit SHA Part A:** `0c0f3bd` — **B-1:** `bed94f3` — **B-2:** `33e3023` — **B-3:** `89e5730` — **docs:** (كوميت التوثيق الأعلى — SHA في وصف الـPR)
- **PR:** [#29](https://github.com/belalalibb/ai_agancy_markting/pull/29) (`work/implementation` ⇒ `main` @ `ec9f7f0`) — **CI:** يُثبَّت على الكوميت الأعلى في وصف الـPR
- **الاختبارات:** وحدة 885 / تكامل 65 / الكل **950 ناجحاً، 0 فشل** (PG 17 حقيقية، `pytest --cov=src --cov-fail-under=95 -p no:cacheprovider`) — **التغطية: 98.05%**. الجديدة في SPRINT-09: `pytest -k "a1_ or a2_ or a3_ or b1_u or b1_m or b1_i or b2_u or b2_i or b3_u or b3_i"` ⇒ 232 مُختاراً (يشمل أسماء SPRINT-06/07/08 المتطابقة البادئة). خطّ الأساس 830 ⇒ 950 (+120). كل كوميت من الأربعة يمرّ الحزمة كاملة وحده (شجرة A: 848/98.88%؛ B-1 وB-2 وB-3: سجلات `/tmp/full_B1.log`، `/tmp/full_B2.log`، `/tmp/full.log` — B-3 = 950/98.05%).
- **A-1..A-3 (بالسطر):**
  - **A-1 (F1):** `alembic/versions/0008_published_at_check.py` يُسقط ويعيد `ck_content_items_published_at_iff_published` بـ`(status = 'published' AND published_at IS NOT NULL) OR (status = 'archived') OR (status NOT IN ('published','archived') AND published_at IS NULL)`؛ المرآة في `src/modules/content/infrastructure/orm.py`. اختبارات: `test_a1_i1_published_item_archived_via_api_keeps_published_at_on_pg`، `test_a1_i2_scheduled_item_archived_via_api_has_null_published_at_on_pg`، `test_a1_m1_migration_0008_roundtrip_nulls_archived_published_at_on_pg`.
  - **A-2 (F2, D-070):** `src/modules/social_connect/meta_client.py`: `_request(..., idempotent=method.upper() == "GET")`، `_request_absolute(..., idempotent: bool = True)`، الجديدة `_request_once_side_effecting` (`ConnectError`/`ConnectTimeout` قبل الإرسال ⇒ `MetaTransientError`؛ أي `HTTPError` آخر أو 5xx أو الكودان 1/2 ⇒ `MetaAmbiguousError`). `src/modules/social_connect/exceptions.py::MetaAmbiguousError(MetaGraphError)`. `src/modules/publishing/domain/models.py::PublishOutcome.ambiguous(*, graph_error_code, graph_error_subcode, graph_trace_id, external_post_id)`. `src/modules/publishing/infrastructure/meta_publisher.py` يلتقط `MetaAmbiguousError` قبل `MetaGraphError`. `src/modules/social_connect/router.py::_to_http` ⇒ 502. اختبارات: `tests/modules/publishing/test_meta_publish_client.py` (A2-U1..U5 + `test_a2_ambiguous_error_classifies_outside_b4_map`)، `tests/integration/test_scheduler_pg.py::test_a2_i1_ambiguous_publisher_outcome_fails_job_without_later_retry`.
  - **A-3 (F3):** `src/modules/content/service.py::ContentService.generate()` أُعيد ترتيبه: `get_item` ⇒ فحص الحالة ⇒ `limiter.hit` ⇒ `_check_token_budget` ⇒ `gateway.complete` ⇒ **ثم** `_lock_item` ⇒ إعادة فحص الحالة ⇒ `add_revision(media_refs=None)`؛ `postgres_repository.py::add_revision` يفسّر `None` = أبقِ `_current_media_refs`؛ `schemas.py::ContentBodyUpdate.media_refs: list[MediaRef] | None = Field(default=None, max_length=1)`. اختبارات: `test_a3_u1_generate_keeps_media_refs_of_current_revision`، `test_a3_u2_put_body_without_media_refs_keeps_them_and_empty_list_removes_them`، `test_a3_u3_generate_recheck_after_gateway_returns_409_and_writes_no_revision`، `test_a3_i1_generate_holds_no_row_lock_during_gateway_call_on_pg` (`ProbingGateway` يفتح `SELECT … FOR UPDATE NOWAIT` من جلسة ثانية أثناء `complete()`).
- **B-1 / B-2 / B-3 (بالسطر):**
  - **B-1** (`bed94f3`): `alembic/versions/0009_whatsapp.py` — خمسة جداول `whatsapp_accounts`, `wa_conversations`, `wa_messages`, `wa_reply_drafts`, `wa_learning_signals` بقيود مسمّاة (`uq_whatsapp_accounts_phone_number_id`, `uq_whatsapp_accounts_brand_id`, `uq_wa_conversations_account_wa_id`, `uq_wa_messages_wamid` جزئي, `uq_wa_messages_client_message_id` جزئي, `ck_wa_messages_outbound_has_actor`, `ck_wa_reply_drafts_decided_has_actor`, `uq_wa_learning_signals_reply_draft_id`, …). `src/modules/whatsapp/domain/models.py` (`WINDOW = timedelta(hours=24)`, `MAX_BODY_LENGTH = 4096`, `Conversation.window_open(now)`, `WhatsAppAccount` بحقول مشفّرة `repr=False, exclude=True`)؛ `domain/repository.py::WhatsAppRepository(Protocol)`؛ `infrastructure/orm.py`, `infrastructure/postgres_repository.py::PostgresWhatsAppRepository` (`insert_inbound_if_new` بـ`ON CONFLICT (wamid) DO NOTHING`, `upsert_conversation_inbound` بـ`GREATEST`). `src/modules/whatsapp/client.py::WhatsAppCloudClient` فوق `core/http.py` — `describe_phone_number` (GET يُعاد), `send_text`/`send_template`/`mark_read` عبر `_post_once` (D-070: `ConnectError` ⇒ `WhatsAppTransientError`؛ بعد الإرسال ⇒ `WhatsAppAmbiguousError`)؛ `map_cloud_error` بأكواد `_RATE_LIMIT_CODES = {130429, 80007, 4, 80004}`, `_RECIPIENT_CODES = {131026, 131047, 131051}`, `_AUTH_CODES = {190, 10}`, `_TRANSIENT_CODES = {1, 2, 131000}`؛ التوكن **Bearer فقط** (D-064). `service.py::WhatsAppAccountService.connect_account` (يتحقق عبر `describe_phone_number` ثم يشفّر `system_token`/`app_secret` ويخزّن `hash_verify_token(verify_token)`), `PostgresWhatsAppTokenProvider.resolve/resolve_by_phone_number_id`. `webhook.py`: `SIGNATURE_HEADER = "X-Hub-Signature-256"`, `compute_signature`, `verify_signature` (`hmac.compare_digest`), `extract_phone_number_ids`, `WebhookProcessor.handle_webhook_payload`, مساران عامّان `GET/POST /api/v1/webhooks/whatsapp` (`WEBHOOK_PATH`). `pyproject.toml`: `"pywa".msg = "Read-only reference — D-071; use src.modules.whatsapp.client"` + `per-file-ignores "src/modules/whatsapp/client.py" = ["TID251"]`. اختبارات B1-U1..U8 (45) + `test_b1_m1_migration_0009_roundtrip_creates_and_drops_five_tables_on_pg` + `test_b1_i1_signed_webhook_persists_inbound_and_dedupes_on_pg`؛ حمولات `tests/modules/whatsapp/payloads/*.json` من وثائق Cloud API v26.0 (`payloads/README.md`).
  - **B-2** (`33e3023`): `src/modules/whatsapp/inbox.py::WhatsAppInboxService` (`list_conversations` keyset `last_inbound_at DESC, id DESC`, `list_messages`, `mark_read`, `archive`, `send_text`, `send_template`, `_deliver`: `queued ⇒ sent/ambiguous/failed`)؛ `schemas.py` (`mask_wa_id` ⇒ `...7890`، `MASKED_WA_ID`, `PUBLIC_DRAFT_PROVENANCE_KEYS = {"kind","generated_at","prompt_version","truncated"}`, `public_draft_provenance`, `ConversationPublic.from_domain(..., mask=)`, `ReplyDraftPublic.from_domain(..., private=)`, `SendTemplateRequest` بنمط الاسم `^[a-z0-9_]+$`)؛ `router.py` تحت `BRAND_PREFIX = "/brands/{brand_id}/whatsapp"`: `POST/GET/DELETE /account`, `GET /conversations`, `GET /conversations/{id}/messages`, `POST …/read`, `POST …/archive`, `POST …/messages` (201؛ 200 عند تكرار `Idempotency-Key`؛ 202 عند `ambiguous`), `POST …/template`؛ `to_http` يحوّل `WindowClosedError` ⇒ 422 `{"error":"whatsapp_window_closed","template_required":true}`؛ `_handle_send_error` يعطّل الحساب عند `ReauthRequiredError`. اختبارات B2-U1..U5 (15) + `test_b2_i1_keyset_pagination_over_25_conversations_on_pg` (25 محادثة، `limit=10` ⇒ 10/10/5) + `test_b2_i2_send_text_persists_outbound_with_actor_and_replays_idempotently_on_pg`.
  - **B-3** (`89e5730`): `src/modules/ai_gateway/gateway.py::Purpose = Literal["content_draft", "reply_suggestion"]` (التغيير الوحيد في البوابة). `src/modules/whatsapp/prompts.py` (`REPLY_PROMPT_VERSION = "wa-reply-v1"`, `REPLY_MAX_CHARS = 600`, `reply_system_prompt(brand_display_name, language, tone)`, `reply_user_prompt(last_messages, max_turns=6)`, `prompt_hash`). `signals.py` دوال نقية: `suggested_len`, `final_len`, `language` ∈ `{ar,en,mixed,other}` بنسبة الحروف العربية (عتبتا 0.8/0.2), `has_question` (`?`/`؟`), `has_cta` (`CTA_VERBS` ثابتة), `edit_distance_ratio = round(1 - SequenceMatcher(None, a, b).ratio(), 3)`, `features`. `learning.py` (`build_signal`, `record_expired` — مشتركة للـwebhook والـinbox بلا دورة استيراد). `suggestions.py::ReplySuggestionService` — `suggest` (آخر رسالة واردة وإلا `NoInboundToReplyError` 409 `no_inbound_to_reply`؛ مسودة قائمة ⇒ `DraftPendingError` 409 `draft_pending`؛ `limiter.hit(SUGGEST_BUCKET="wa_suggest", brand_id, 60, 3600)`؛ ميزانية A-5 عبر `WhatsAppRepository.ai_token_usage_combined_since` = **استعلام `UNION ALL` واحد** على `content_revisions.provenance` و`wa_reply_drafts.provenance` ⇒ `GenerationBudgetExceededError` نفسه؛ `CompletionRequest(purpose="reply_suggestion", max_tokens=400, temperature=0.5)`؛ `counters["wa_suggestions"]`؛ **صفر** نداء WhatsApp), `approve` (`approved`+`approved_unchanged` أو `edited`+`approved_after_edit`؛ ثم `WhatsAppInboxService.send_text(..., reply_draft_id=)`؛ نافذة مغلقة ⇒ 422 والمسودة تبقى `approved`), `reject`, `get_pending_or_raise`. `webhook.py`/`inbox.py` يستدعيان `record_expired` بعد `expire_pending_drafts` ⇒ إشارة `expired`؛ استجابة الـwebhook تضيف `"expired_drafts": [...]`. مسارات: `POST /conversations/{conversation_id}/suggest-reply` (201), `POST /reply-drafts/{draft_id}/approve` (200/202/422), `POST /reply-drafts/{draft_id}/reject`. اختبارات B3-U1..U7 (12) + `test_b3_i1_full_cycle_webhook_suggest_approve_edit_send_signal_budget_on_pg` + `test_b3_i2_budget_of_one_token_makes_suggest_reply_429_on_pg`.
- **اختبارات SPRINT-06/07/08 المعدَّلة (السبب لكل واحد):**
  - `tests/integration/test_postgres_persistence.py`: كل `assert version_num == "0007"` ⇒ `"0009"` (رأس الهجرات تقدّم)؛ `TABLES` في `tests/integration/conftest.py` أُضيف إليها الجداول الخمسة الجديدة أولاً (ترتيب TRUNCATE/FK).
  - `tests/test_auth_guard.py`: `PUBLIC` += `("GET","/api/v1/webhooks/whatsapp")`, `("POST","/api/v1/webhooks/whatsapp")` (التوقيع HMAC هو حارسها لا JWT)؛ `_concrete` يتعامل مع `conversation_id`/`draft_id`؛ `PROTECTED_ROUTE_COUNT` 34 ⇒ **46**. `tests/modules/content/test_router.py` السطر المطابق ⇒ 46.
  - `tests/modules/ai_gateway/test_contract.py`: `BANNED_PLATFORM_MODULES` += `pywa`؛ `ALLOWED_FILES` += `modules/whatsapp/client.py` (التحقق من `pyproject`).
  - `tests/modules/social_connect/test_router.py::test_openapi_has_no_token_named_fields_except_has_page_token`: حارس Zero-Secret كان يمسح **كل** الـschemas غير الموصولة بالاستجابات؛ أُضيفت قائمة `byok_request_fields` الثلاثية (`WhatsAppAccountCreate/properties/{system_token,app_secret,verify_token}`) **للطلب فقط** مع تأكيدين جديدين: `"WhatsAppAccountCreate" not in reachable` و`"WhatsAppAccountPublic" in reachable` — الاستجابات ما زالت ممنوعة من أي اسم سرّي.
  - `tests/fakes.py::FakeContentRepository.add_revision`: `media_refs=None` = أبقِ (مرآة A-3)؛ `FakeWhatsAppRepository` جديد.
  - `tests/modules/whatsapp/test_b1_webhook.py`: تأكيدا استجابة الـwebhook أُضيف إليهما `"expired_drafts": []` (B-3).
- **القبول §3 (نواتج الـgreps حرفياً):**
  - `grep -rln "import httpx" src | sort` ⇒ `src/core/http.py` `src/modules/ai_gateway/dependencies.py` `src/modules/ai_gateway/facades/openai_compatible.py` `src/modules/social_connect/meta_client.py` `src/modules/whatsapp/client.py` (خمسة فقط).
  - `grep -rn "pywa" src pyproject.toml` ⇒ سطر `banned-api` في `pyproject.toml` فقط (في `tests/` سطران في `test_contract.py` يتحققان منه).
  - `grep -rn "auto_reply\|AUTO_REPLY" src` ⇒ فارغ. `grep -rn "encrypted_system_token\|encrypted_app_secret" src/modules/whatsapp/schemas.py` ⇒ فارغ.
  - `ls alembic/versions/` ⇒ `0001`…`0008_published_at_check.py`, `0009_whatsapp.py`.
  - `pytest -k "a2_u or b1_u6 or b2_u3" -q` ⇒ كلها تمرّ؛ كل اختبار POST فيها يؤكد `len(route.calls) == 1`.
  - `tests/modules/content/test_lifecycle.py`: `assert len(EXPECTED) == 56` ما زال كما هو.
  - لا متغير بيئة جديد: `grep -n "WHATSAPP" src/core/config.py` ⇒ فارغ (BYOK كامل عبر `POST /account`).
  - `git diff ec9f7f0 --name-only` خالٍ من الملفات المحظورة (0 تطابق).
- **تقرير جاهزية الربط الحي (§3-12):** بعد حصول المالك على WABA (بلا أي سرّ هنا):
  1. في لوحة Meta for Developers ⇒ التطبيق ⇒ WhatsApp ⇒ *API Setup*: انسخ `Phone number ID` و`WhatsApp Business Account ID`؛ أنشئ **System User** بصلاحية `whatsapp_business_messaging` + `whatsapp_business_management` وولّد توكناً دائماً (هذا هو `system_token`)؛ من *App Settings ⇒ Basic* انسخ `App Secret` (هذا هو `app_secret`)؛ اختر سلسلة عشوائية ≥ 8 أحرف تكون `verify_token`.
  2. بصفة **OWNER** للعلامة: `POST /api/v1/brands/{brand_id}/whatsapp/account` بجسم `{"phone_number_id": "...", "waba_id": "...", "display_phone": "+20 ...", "system_token": "...", "app_secret": "...", "verify_token": "..."}` ⇒ 201 `WhatsAppAccountPublic` (بلا أي سرّ في الاستجابة). الخادم يتحقق من التوكن فوراً عبر `GET /{phone_number_id}` (Bearer) — توكن مرفوض ⇒ 422 `whatsapp_credentials_invalid` ولا صفّ يُكتب. يتطلب `TOKEN_ENCRYPTION_KEYS` مضبوطاً (D-058؛ وإلا 503).
  3. في لوحة Meta ⇒ WhatsApp ⇒ *Configuration* ⇒ Webhook: **Callback URL** = `https://<النطاق العام>/api/v1/webhooks/whatsapp`، **Verify token** = القيمة نفسها من الخطوة 2 ⇒ Meta تستدعي `GET …/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=…&hub.challenge=…` والخادم يردّ بالـchallenge فقط إن طابق `verify_token_hash` حساباً **نشطاً**. ثم اشترك في حقل **`messages`** (يشمل الحالات).
  4. أرسل رسالة اختبار من هاتف إلى الرقم ⇒ `POST …/webhooks/whatsapp` موقَّع بـ`X-Hub-Signature-256` (يُتحقق منه ضد `app_secret` العلامة بحسب `phone_number_id` في الحمولة؛ توقيع خاطئ ⇒ 401 ولا شيء يُخزَّن) ⇒ تظهر في `GET …/whatsapp/conversations` مع `window_open: true` لمدة 24h.
  5. الردّ: `POST …/conversations/{id}/messages` بنص حرّ داخل النافذة (أو `…/template` خارجها باسم قالب **معتمد** في WABA)؛ أو `…/suggest-reply` ⇒ `…/reply-drafts/{id}/approve`. كل صادر يحمل `sent_by_user_id` (لا إرسال آلي).
  6. مراقبة: عدّادات `wa_inbound`, `wa_duplicate`, `wa_status_updates`, `wa_webhook_rejected`, `wa_sent`, `wa_send_ambiguous`, `wa_send_failed`, `wa_suggestions`؛ السجلات لا تحوي نص الرسائل ولا التوكن (الرقم مقنَّع `…7890`). عند 401/190 من Meta يتحوّل الحساب إلى `disabled` ويردّ الـAPI 409 `whatsapp_reauth_required` ⇒ أعد الخطوة 2 بعد `DELETE …/account`.
  7. بعد M6: أضف `whatsapp_business_messaging` إلى طلب App Review.
- **انحرافات (بالسطر والسبب):**
  1. **`PROTECTED_ROUTE_COUNT == 46` لا 45:** التوجيه عدّ B-2 بـ8 مسارات بينما نصّه يسرد **9** (`account` ×3، `conversations`، `messages` GET، `read`، `archive`، `messages` POST، `template`) ⇒ 34 + 9 + 3 = 46. الأسماء في الكود هي المرجع.
  2. **`MetaAmbiguousError(MetaGraphError)`:** التوجيه ذكر الأصل `MetaClientError`؛ الاسم الفعلي للأصل في `src/modules/social_connect/exceptions.py` هو `MetaGraphError` — لم أُنشئ صنفاً جديداً باسم غير موجود.
  3. **`learning.py` ملف إضافي** غير مذكور في التوجيه: استُخرج منه `build_signal`/`record_expired` لأن `inbox.py` و`webhook.py` يحتاجان تسجيل إشارة `expired` بينما `suggestions.py` يستورد `inbox.py` (تفادي دورة استيراد). السلوك مطابق للنصّ.
  4. **`ai_token_usage_combined_since`** أُضيفت إلى بروتوكول `WhatsAppRepository` (بجانب `ai_token_usage_since`) لتنفيذ «استعلام واحد UNION ALL» حرفياً على PG؛ `FakeWhatsAppRepository` يحاكيها عبر مرجع `content_repo` يضبطه fixture `wa_app`.
  5. **حارس Zero-Secret** (`test_openapi_has_no_token_named_fields_except_has_page_token`) وُسِّع بثلاثة حقول **طلب** فقط كما ورد أعلاه؛ لم يُضعَف جانب الاستجابة.
  6. **`SuggestReplyRequest.tone`** = `{"friendly","formal","promotional"}` (الـcontent يستخدم `professional`؛ اخترت `formal` لسياق خدمة العملاء) — ترميز واجهة فقط.
  7. **البيئة:** 30 إعادة ضبط للـsandbox خلال المشروع (#27–#30 في هذه الجلسة)؛ كل ملف دُفع بعد كتابته (`wip:`) ثم سُحق إلى الكوميتات الأربعة بـ`git commit-tree` على الأشجار نفسها المُتحقَّق منها (فروق الأشجار = 0).
- **أسئلة:**
  1. هل يُراد كشف `wamid` في `MessagePublic`؟ أبقيته خادمياً (B2-I2 يؤكد غيابه) — إن احتاجه الواجهة لأغراض الدعم أضيفه لـOWNER/MANAGER فقط.
  2. `SuggestReplyRequest.language="auto"` يترك اللغة للقالب (لغة آخر رسالة)؛ هل تريد اشتقاقها خادمياً من `signals.language()` على آخر رسالة واردة وتثبيتها في الـprovenance؟

---

## 7. ما ينتظر المالك

1. 🔑 **تدوير توكن GitHub — الطلب التاسع.** التوكن ظهر في المحادثة مجدداً؛ يُعدّ مكشوفاً. بعد التدوير يُمرَّر للمخطط مرة واحدة في بداية الجلسة فقط.
2. 🛡️ حماية `main` بالفحوص الخمسة + منع الدفع المباشر (ما زالت غير مفعَّلة؛ الدمج يتم عبر API بلا قيود).
3. **M4 — App Review من Meta** لنطاقي `pages_manage_posts` و`instagram_content_publish`: **ابدأ الآن** (أسابيع). مصفوفة `GET /channels` تُظهره `pending`.
4. **M6 — قبل الربط الحي (ليس قبل التطوير):** حساب WhatsApp Business (Cloud API) + رقم اختبار + توكن نظام (System User) + `app_secret` للتطبيق. **التطوير والاختبار لا يتوقفان عليها** — تُحقن لاحقاً عبر `POST /brands/{id}/whatsapp/account`. بعد M6: إضافة نطاق `whatsapp_business_messaging` إلى App Review.
5. **M5 — تفعيل المنصة الثالثة** يبقى مؤجَّلاً بقرارك (مؤجَّل #17).
6. **قبل M7:** مزوّد الذكاء الاصطناعي الحقيقي الأول (OpenAI-compatible؛ الآن `AI_PROVIDER=fake`).
7. **للعلم (قرارات المخطط على سؤالَي المنفذ في SPRINT-08):** `ChannelRegistry.active()` **تبقى**؛ مصدر `activation_status` = تعريف + وجود اعتماد (مؤجَّل #16). قرار **D-071**: PyWa مرجع قراءة لا تبعية.
