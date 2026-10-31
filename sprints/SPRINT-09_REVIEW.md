# 📋 SPRINT-09-WHATSAPP-CONVERSATIONS — تقرير مراجعة المخطط

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-09-WHATSAPP-CONVERSATIONS` (المحطة M6 + إغلاق F1/F2/F3 من مراجعة SPRINT-08) |
| **المُراجِع** | AI Master Planner |
| **تاريخ المراجعة** | 2026-10-31 |
| **الفرع المُراجَع** | `work/implementation` @ `8fc4917` (Part A `0c0f3bd`، B-1 `bed94f3`، B-2 `33e3023`، B-3 `89e5730`، وثائق حتى `8fc4917`) فوق `main` @ `ec9f7f0` |
| **Pull Request** | [#29](https://github.com/belalalibb/ai_agancy_markting/pull/29) — 58 ملفاً، +8000/−45، 8 كوميتات |
| **الحكم النهائي** | 🟢 **مقبول — دُمج بواسطة المخطط (D-063)**. Part A (F1/F2/F3 + D-070) وB-1/B-2/B-3 منفَّذة ومُتحقَّق منها بالسطر وبالتشغيل على PostgreSQL 17 حقيقية (بيئة أُعيد بناؤها من الصفر مرتين خلال هذه المراجعة)؛ **950 اختباراً تمرّ**؛ ruff نظيف؛ لا تبعية جديدة؛ لا ملف محظور مُسّ؛ الانحرافات السبعة مقبولة. في المقابل أثبت المخطط **بالتجربة على PG** خمس ثغرات (§4: N1–N5 — ثلاث منها حرجة سلوكياً) مصدرها نمط «اكتب ثم ارفع استثناءً» داخل جلسة تُرجِع كل شيء عند أي استثناء، وغياب نطاق العلامة في تحديث الحالات، وعدم تماثل الميزانية. **تُغلق في SPRINT-10 الجزء A** ولا تمنع الدمج لأن M6 لم يُربط حياً بعد (لا WABA). |

---

## 1. ما تحقق منه المخطط فعلياً (بيئة طازجة ×2؛ PG 17 + pgvector من pgdg؛ `alembic upgrade head` ⇒ 0008 ⇒ 0009)

| الفحص | أمر المخطط | النتيجة |
|---|---|---|
| المجموعة الكاملة على PG حقيقية | `pytest -q -p no:cacheprovider` (`TEST_DATABASE_URL` مضبوط) | **950 passed, 0 failed** (232.85s) — يطابق تقرير المنفذ |
| ruff | `ruff check . && ruff format --check .` | `All checks passed!` / `251 files already formatted` |
| الهجرات | `alembic upgrade head` على قاعدة فارغة | `0007 → 0008` (published_at CHECK) `→ 0009` (whatsapp) بلا خطأ |
| CI على GitHub | Checks API على `8fc4917` | **5/5 success ×2** (lint/unit/integration/secrets/licenses) |
| قابلية الدمج | `GET /pulls/29` | `mergeable: true`، `mergeable_state: clean`، 8 كوميتات |
| ملفات محظورة | `git diff --name-only ec9f7f0..8fc4917` | التطابق الوحيد لـ`README.md` هو `tests/modules/whatsapp/payloads/README.md` (ملف حمولات اختبار — **ليس** جذر المستودع) ⇒ لا `ARCHITECTURE_LOCK`/`SESSION_RECOVERY`/`imaster_planner`/`README` الجذري/`Hub`/`sprints` |
| `pyproject.toml` | `git diff ec9f7f0 -- pyproject.toml` | سطر `banned-api` لـ`pywa` + `per-file-ignores` لـ`src/modules/whatsapp/client.py` فقط — **لا تبعية جديدة** |
| عدّاد المسارات المحمية | `test_router.py:1417` | `== 46` (34 + 9 + 3) — عدّ المنفذ صحيح والتوجيه أخطأ (انظر §2-1) |

> **التغطية:** المنفذ يُبلغ 98.05% على الكل؛ CI `unit` نجح ببوابة 95 على `8fc4917` (المصدر الموثوق للبوابة).

### 1-أ. التحقق بنداً بنداً (بالسطر @ `8fc4917`)

| البند | الملف:السطر | ما وجده المخطط |
|---|---|---|
| **A-1** (F1) | `alembic/versions/0008_published_at_check.py:60` | `op.create_check_constraint(CONSTRAINT, TABLE, NEW_EXPRESSION)` بالتعبير الثلاثي (`published` ⇒ غير فارغ؛ `archived` حرّ؛ غيرهما ⇒ فارغ)؛ `downgrade` يعيد القديم؛ الهجرة طُبّقت فعلياً على PG |
| **A-2** (F2, D-070) | `meta_client.py:456-457, 468, 482-483, 522` | `idempotent=method.upper() == "GET"`؛ `_request_absolute(..., idempotent=True)`؛ `if not idempotent: return await self._request_once_side_effecting(...)` — تعليق D-070 صريح في الكود |
| **A-3** (F3) | `content/service.py:500-577` | docstring يوثّق الترتيب «plain read ⇒ state check ⇒ limiter ⇒ budget ⇒ `gateway.complete()` ⇒ lock» ؛ `gateway.complete` @526 **قبل** `_lock_item` @544؛ إعادة فحص الحالة @545؛ `add_revision` @577 |
| **B-1** | `0009_whatsapp.py`, `whatsapp/client.py`, `webhook.py` | خمسة جداول بالقيود المسمّاة (تحقق `\d` ضمناً عبر مرور B1-M1 على PG)؛ `describe_phone_number` GET `fields=display_phone_number,verified_name` @138-142؛ `verify_signature` بـ`hmac.compare_digest`؛ مساران عامّان |
| **B-2** | `whatsapp/router.py:241-600`, `inbox.py` | `POST/GET/DELETE /account`, `GET /conversations`, `GET …/messages`, `POST …/read`, `…/archive`, `…/messages`, `…/template` (9 مسارات)؛ `to_http` @180 «Never carries Meta's message text (codes only)»؛ `_handle_send_error` @409 |
| **B-3** | `suggestions.py:192-263`, `signals.py`, `learning.py` | `ai_token_usage_combined_since` (UNION ALL) @192؛ `approve` ⇒ `decide_draft` ⇒ `add_signal(build_signal)` ⇒ `send_text(reply_draft_id=)`؛ لا عمود نصّ في `wa_learning_signals` |

---

## 2. تقييم تقرير المنفذ (§6 @ `8fc4917`)

| # | الانحراف المُبلَّغ | حكم المخطط |
|---|---|---|
| 1 | `PROTECTED_ROUTE_COUNT == 46` لا 45 | ✅ **مقبول — خطأ في التوجيه.** نصّ B-2 يسرد 9 مسارات بالفعل (account ×3 + 6). الكود هو المرجع. |
| 2 | `MetaAmbiguousError(MetaGraphError)` لا `MetaClientError` | ✅ مقبول — الأصل الفعلي هو `MetaGraphError`؛ لا يُنشأ صنف باسم غير موجود. |
| 3 | `learning.py` ملف إضافي لتفادي دورة استيراد | ✅ مقبول — فصل سليم (`build_signal`/`record_expired` مشتركان بين `inbox`/`webhook`/`suggestions`). |
| 4 | `ai_token_usage_combined_since` في البروتوكول | ✅ مقبول — ينفّذ «استعلام UNION ALL واحد» حرفياً. **لكن** انظر N4: جانب المحتوى لم يُحدَّث ليستخدمه ⇒ عدم تماثل. |
| 5 | توسيع حارس Zero-Secret بثلاثة حقول **طلب** فقط | ✅ مقبول — التأكيدان الجديدان (`WhatsAppAccountCreate` غير قابل للوصول من الاستجابات؛ `WhatsAppAccountPublic` قابل) يُبقيان الحارس حاداً. |
| 6 | `tone ∈ {friendly, formal, promotional}` | ✅ مقبول — ترميز واجهة؛ `formal` أنسب لخدمة العملاء من `professional`. |
| 7 | 30 إعادة ضبط؛ سحق بـ`git commit-tree` على الأشجار نفسها | ✅ مقبول — المخطط تحقق أن كل كوميت من الأربعة يمرّ الحزمة كما أُبلغ (CI 5/5 ×2 على الأعلى). |

تقرير جاهزية الربط الحي (§3-12) **كامل ودقيق** ويُعتمد كما هو كدليل تشغيل للمالك (يُنسخ إلى `DATA_AND_INTEGRATIONS.md` §1-د).

---

## 3. أجوبة سؤالَي المنفذ

1. **كشف `wamid` في `MessagePublic`؟ — نعم، لكن ضمن الطبقة الخاصة فقط.** `wamid` معرّف منصة لا سرّ، لكنه يُمكّن ربط سجلّات الدعم بمحادثات العميل عند Meta. القرار: يُعرض **لـOWNER/MANAGER فقط** عبر معامل `private=` نفسه المستخدم في `ReplyDraftPublic.from_domain(..., private=)` (D-066 — مفاتيح عامة/خاصة)؛ VIEWER لا يراه. يُنفَّذ في SPRINT-10 A-6 (سطر واحد في الـschema + اختبار رؤية).
2. **`language="auto"` — نعم، اشتقّها خادمياً وثبّتها.** `signals.language()` موجودة وتُحسب أصلاً للإشارة؛ استخدامها على آخر رسالة واردة عند `auto` يجعل القالب حتمياً (لا «لغة آخر رسالة» ضمنياً عند المزوّد) ويُسجَّل الناتج في الـprovenance **الخاص** كـ`language_detected ∈ {ar,en,mixed,other}`؛ `mixed`/`other` ⇒ `ar` افتراضياً (السوق المستهدف). قيمة `language` الصريحة من الطلب تتقدّم على المُشتقّة. SPRINT-10 A-6 كذلك.

---

## 4. ما أثبته المخطط بالتجربة (خمس ملاحظات تدقيق أرسلها المالك — تحقق منها المخطط على PG 17 بمِجسّ مستقل، ثم حُذف المِجسّ)

> **المنهج:** ملف اختبار مؤقت `tests/integration/test_zz_probe_s09.py` يستخدم fixtures المشروع نفسها (`integration_app`, `graph_mock`, `_wa_account_row`, `_wa_conversation_row`) على القاعدة الحقيقية، يطبع الحالة الفعلية للصفوف بعد كل طلب HTTP. المخرجات أدناه **حرفية**. السبب الجذري المشترك لـN1: `src/core/database.py:51-62` — جلسة واحدة لكل طلب تُنفّذ `rollback()` عند **أي** `BaseException` بما فيها `HTTPException` ⇒ كل ما كُتب قبل `raise` يُمحى. الـFake repos في اختبارات الوحدة **لا تملك معاملات** فلا ترى ذلك (درس 22).

### N1 — الكتابة قبل الرفع تُفقد بالـrollback (ثلاث حالات) — 🔴 حرجة

```
PROBE1a approve-closed-window http=422 error=whatsapp_window_closed draft_status_after=suggested learning_signals=0
PROBE1a2 second-approve http=422 error=whatsapp_window_closed
PROBE1b send-with-190 http=409 error=whatsapp_reauth_required account_status_after=active outbound_rows=[]
PROBE1c send-connect-error http=503 error=whatsapp_transient outbound_rows=[]
PROBE1d send-429 http=429 error=whatsapp_rate_limited outbound_rows=[]
```

- **(a)** `approve()` @`suggestions.py:225-263` يكتب `decide_draft(approved)` + `add_signal` + عدّاداً، ثم `send_text` يرفع `WindowClosedError` ⇒ 422 ⇒ **المسودة تعود `suggested` والإشارة تختفي**. تقرير المنفذ قال «المسودة تبقى `approved`» — صحيح مع Fake، **خاطئ على PG**. النتيجة العملية: المستخدم يرى 422 «أرسل قالباً» لكن القرار البشري ضاع ولا أثر تعلّمي (D-069).
- **(b)** `_handle_send_error` @`router.py:409-413` يستدعي `disable_account_after_auth_failure` ثم `raise to_http(exc)` ⇒ 409 ⇒ **الحساب يبقى `active`**، فتعود المحاولة التالية لتضرب Meta بتوكن ميت وتتكرر 190 إلى ما لا نهاية. ادّعاء §3-12 البند 6 («يتحوّل الحساب إلى disabled») لا يصحّ على PG.
- **(c/d)** `_deliver` يكتب صفّ `wa_messages(delivery_status='failed', error_code=…)` ثم يرفع ⇒ 503/429 ⇒ **صفر صفوف**. سجلّ الفشل التشغيلي (D-070 «بعد الإرسال ⇒ مبهم») لا يُحفظ أصلاً، وعدّاد `wa_send_failed` في الذاكرة وحده يبقى.

### N2 — إعادة الربط مستحيلة + تضارب `phone_number_id` ⇒ 500 — 🔴 حرجة للتشغيل

```
PROBE2a disable http=200 status=disabled
PROBE2b reconnect-after-disable http=409 error=whatsapp_account_exists
PROBE2c pnid-clash-other-brand RAISED IntegrityError: (sqlalchemy.dialects.postgresql.asyncpg.UniqueViolationError) duplicate key value violates unique constraint "uq_whatsapp_accounts_phone_number_id"
```

- `connect_account` @`service.py:95-145` يرفض على `get_account(brand_id) is not None` بلا نظر إلى الحالة؛ `disable_account` يُبقي الصف `disabled` ⇒ **لا طريق لتدوير التوكن أو إعادة الربط** بعد 190 (تعليمة §3-12 البند 6 «أعد الخطوة 2 بعد DELETE» غير قابلة للتنفيذ).
- تصادم `phone_number_id` مع علامة أخرى يصل إلى الـDB ويخرج `IntegrityError` خاماً ⇒ 500 (TestClient أعاد رفعه). يجب 409 `whatsapp_phone_number_in_use` بلا تسريب اسم القيد.

### N3 — تحديث الحالة غير مقيّد بالعلامة ولا رتيب — 🔴 حرجة للعزل

```
PROBE3 cross-brand status via brand-A account http=200 body={'inbound': 0, 'duplicates': 0, 'status_updates': 1, 'unparsed': 0, 'expired_drafts': []} brandB_row_status_after=sent (was read)
PROBE3b same-brand regression delivered->sent http=200 after=sent
```

- `update_status_by_wamid` @`postgres_repository.py:423-434` يُطابق `wamid` + `direction` فقط. حمولة موقَّعة بسرّ **العلامة A** عدّلت صفّ **العلامة B** (`status_updates: 1`). كل حساب BYOK يحمل سرّ تطبيقه؛ مستأجر خبيث أو تطبيق Meta مخترَق لعلامة واحدة يستطيع تلويث حالات الجميع إن عرف `wamid` (وهو يظهر في إشعارات Meta للطرفين).
- لا حارس رتابة: `delivered` ⇒ `sent` قُبل. Meta توثّق أن الإشعارات قد تصل مرتّبة عكسياً.

### N4 — الميزانية غير متماثلة — 🟠 متوسطة (تجاوز الميزانية من جهة واحدة)

```
PROBE4a content-generate-with-900k-wa-tokens http=200 error=- retry_after=None
PROBE4b wa-suggest http=429 error=generation_budget_exceeded retry_after=86399
```

- مسودة واتساب بـ900k رمز (الميزانية 200k): `suggest-reply` يُرفض (UNION ALL) لكن `/generate` **يمرّ** لأن `content/service.py:591` يقرأ `content_revisions` وحده. والـ`Retry-After` في المسارين يُحسب من `content_repo.oldest_ai_generation_at` فقط ⇒ قيمة خاطئة حين يكون أقدم استهلاك من واتساب.

### N5 — تصلّب الـwebhook وسباق `suggest-reply` — 🟡 منخفضة الآن، تُغلق قبل الربط الحي

```
PROBE5a 3MiB-body bad-sig http=401 took=0.223s body={"detail":{"error":"webhook_unverified"}}
PROBE5b missing-header http=401
PROBE5c malformed-header http=401
PROBE5d non-json valid-sig http=401
PROBE5e messages_after=0
```

- **السلوك الخارجي صحيح** (401 في كل الحالات، صفر كتابة). لكن الترتيب داخل `receive_webhook` @`webhook.py:336-365`: `json.loads(raw_body)` ⇒ `extract_phone_number_ids` ⇒ **استعلامات DB** لكل `phone_number_id` ⇒ ثم التوقيع. 3 MiB غير موقَّعة تُحلَّل كاملةً وتُستعلَم قبل الرفض؛ لا سقف لحجم الجسم؛ لا فحص صيغة `sha256=<64 hex>` قبل فكّ تشفير السرّ. (0.22s محلياً — لكنه حمل CPU/DB بلا مصادقة.)
- **سباق `suggest-reply`:** الفحص `EXISTS … status='suggested'` قبل `gateway.complete()` (نداء شبكة) بلا إعادة فحص تحت قفل ولا فهرس فريد جزئي ⇒ طلبان متزامنان ينتجان مسودتين معلّقتين. لم يُقَس بمِجسّ (يتطلب تزامناً حقيقياً) لكنه ثابت بالقراءة: `orm.py:152` فهرس `ix_wa_reply_drafts_conversation_status` **غير فريد**.

> **لماذا لا يمنع ذلك الدمج:** M6 غير مربوط حياً (لا WABA)، الاختبارات الـ950 تمرّ، والإصلاحات محصورة (Part A من SPRINT-10 بخمس نقاط واضحة + هجرة 0010). الدمج الآن يحفظ أساس M6 المُتحقَّق منه ويُبقي `work/implementation` مطابقاً لـ`main`.

---

## 5. ما يُفتح الآن

- **SPRINT-10 الجزء A (إلزامي قبل B):** A-1 نمط «ثبّت ثم أبلغ» (D-072) لثلاث مواضع N1؛ A-2 إعادة ربط/تدوير + 409 على التضارب؛ A-3 نطاق العلامة + رتابة الحالات؛ A-4 تماثل الميزانية (دالة مشتركة واحدة)؛ A-5 سقف جسم + فحص الرأس قبل التحليل + فهرس فريد جزئي للمسودة المعلّقة (0010)؛ A-6 جوابا §3 (wamid خاص، `language_detected`).
- **SPRINT-10 الجزء B (M7):** انظر `CURRENT_DIRECTIVE.md` — Brand Brain بـEvidence Schema (D-074) ومزوّد حقيقي عبر البوابة الحالية، وتخطيط طبقة الباقات (D-073).
- **الدروس** 22–24 في `KNOWN_ISSUES.md`؛ صفوف ⚠️ N1–N5 في `SECURITY_BASELINE.md` §2.

---

## 6. ملاحظة حوكمة

- التوكن ظهر في المحادثة مرة أخرى ⇒ **طلب التدوير العاشر** (§7 في التوجيه).
- إعادتا ضبط للساندبوكس خلال هذه المراجعة (#17 و#18 من جهة المخطط)؛ إعادة البناء بإجراء `SESSION_RECOVERY.md` §2-ب في أقل من دقيقتين كل مرة. المنفذ أبلغ 30 إعادة ضبط من جهته.
- المِجسّ المؤقت حُذف قبل أي `git add`؛ `.rv/` في `.gitignore`.
