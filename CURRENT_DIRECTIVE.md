# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-11.1 رُوجع مستقلاً على PostgreSQL 17.11 UTF8 و**قُبل ودُمج** (PR #42؛ التقرير [`sprints/SPRINT-11.1_REVIEW.md`](./sprints/SPRINT-11.1_REVIEW.md) §3 يثبت **H1–H6 حياً**). هذا التوجيه = **SPRINT-12**: **Part A** يغلق H1–H6 (شرط مسبق لأي تفعيل للكتالوج أو لمشرف منصة) ⇒ **Part B** حلقة الليد + Pulse-SQL + أسقف واتساب D-082 (الخلفية التي تحتاجها L1) ⇒ **Part C** رابط التشخيص العام `/d/<token>` + Batch Approval API. **واجهة L1 الست + Umami + Playbooks + AI admin tab** ⇒ **SPRINT-12.1** (يصدره المخطط فور قبول هذا السبرنت) — انظر بوابة G6 أدناه.

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-12-CLOSURES-LEAD-LOOP-PULSE` |
| **النوع** | Part A: إغلاق H1 (`plan_inactive` ⇒ 200 للـwebhook / 402 للصادر) · H2 (تفويض قبل حصة) · H3 (Brand Brain عبر الكتالوج) · H4 (SSRF عبر DNS في `validate_provider_host`) · H5 (`CatalogPriceTable` بمفتاح مزوّد+اسم وتجاهل المعطَّل) · H6a–d ⇒ Part B: هجرة `0016` (`lead_stage` + `lead_attributions` + `pulse_alerts` + `wa_number_caps`) + وحدة `pulse` (8 فحوص SQL على `scheduler_loop`) + أسقف D-082 ⇒ Part C: `GET /d/{token}` (عام، بعلامة مائية، للقراءة فقط) + `POST /brands/{id}/approvals/batch` |
| **الحالة** | `ISSUED` — جاهز للتنفيذ فور دمج هذا التوجيه في `main` (المخطط يدمجه — D-063) |
| **يبدأ من** | `main` @ **SHA دمج هذا التوجيه** (يسجَّل في `WORK_STATE.md` §6). أول أمر: `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` |
| **الفرع المستهدف** | `work/implementation` ⇒ PR إلى `main` بعنوان `SPRINT-12: H1–H6 closure + lead loop + Pulse-SQL + WA caps + public diagnosis link` |
| **الكوميتات** | **ثلاثة** متسلسلة، **كلٌّ منها يُدفع فور اكتماله** ليحصل على CI 5/5 مستقل: **A** (الإغلاقات، بلا هجرة) ⇒ **B** (هجرة 0016 + lead loop + pulse + caps) ⇒ **C** (`/d/{token}` + batch). كوميت رابع `docs(sprint-12): executor report` يملأ §6 فقط. |
| **تبعيات جديدة** | **لا شيء** في `pyproject.toml`. (L1 UI وتبعياتها ⇒ SPRINT-12.1.) |
| **الحجم المتوقع (G6)** | A ≈ 300 سطر `src/` · B ≈ 900 · C ≈ 400 ⇒ **≈ 1,600 ≤ سقف 1,800 فعلياً**. **صمّام:** إن تجاوز A+B معاً 1,300 سطر `src/` فعلياً، أنجز C بـ`/d/{token}` فقط (بلا batch) وسجّل في §6. |
| **المسارات المحمية بعد السبرنت** | **`PROTECTED_ROUTE_COUNT == 81`** = 77 + **4** (`PATCH …/conversations/{id}/lead-stage`, `GET …/pulse/alerts`, `POST …/pulse/alerts/{id}/ack`, `POST …/approvals/batch`) · العامة **7** = 6 + **1** (`GET /api/v1/d/{token}`) · (`POST …/brain/diagnoses/{id}/share` يُحتسب ضمن الأربعة؟ **لا** — انظر §2-C-1: المشاركة عبر `PATCH …/brain/diagnoses/{id}` القائم بحقل `public_share`؛ إن لم يوجد مسار PATCH للتشخيص فأضفه وعدّل العدد إلى **82** وسجّل) |
| **الهجرات** | `0016_lead_loop_pulse_caps.py` (Part B) — جدولان جديدان (`lead_attributions`, `pulse_alerts`) + جدول `wa_number_caps` + أعمدة على `wa_conversations` (`lead_stage`, `lead_stage_set_by_user_id`, `lead_stage_at`) و`brand_diagnoses` (`share_token_hash`, `shared_at`)؛ `downgrade` فعلي مُختبَر `head ⇒ 0015 ⇒ head` |
| **تاريخ الإصدار** | 2026-11-02 |

---

## 0. لماذا؟ (ما يجب أن يفهمه المنفذ قبل أي سطر)

1. **الإنتاج محظور تشغيلياً الآن** على ثلاث نقاط حتى يُدمج Part A: لا `FEATURE_PROVIDER_CATALOG_ENABLED=true`، لا `PLATFORM_ADMIN_USER_IDS`، لا `suspend` لباقة علامة لها webhook واتساب حيّ — لأن H1 يعيد **500** لـMeta ويفقد الرسالة، وH4 يسمح لمشرف المنصة بـSSRF إلى metadata عبر `/admin/ai/probe` حتى والكتالوج معطَّل.
2. **أقصر طريق لأول 3–5 دافعين** (POST_M7 §1-د) هو واجهة L1 — لكن L1 تعرض بيانات لا توجد بعد: `lead_stage`، تنبيهات Pulse، سقف الرقم. لذلك Part B يسبق L1 ولا يمكن دمجهما في سبرنت واحد (G6).
3. **بوابة G6 (النطاق والحجم) — قرار المخطط:** خارطة POST_M7 لـSPRINT-12 كاملة (إغلاقات + L1 ست شاشات + تبويب AI للمشرف + حلقة الليد + Pulse + Batch + Playbooks + `/d/<token>` + أسقف WA + Umami) تُقدَّر بـ**≈ 3,600–4,200 سطر `src/` فعلياً** (L1 وحدها ≈ 1,800–2,200 قوالب + مسارات HTML) ⇒ **تتجاوز السقف ×2**. التقسيم: **SPRINT-12** = كل ما هو **خلفية** (A+B+C أعلاه، ≈ 1,600) · **SPRINT-12.1** = كل ما هو **واجهة** (L1 الست + تبويب `/admin/ai` + Umami + Playbooks JSON كبذور). TTFV ≤ 30 دقيقة يُقاس في نهاية 12.1.
4. **ترخيص أدوات الواجهة (D-054) تحقق منه المخطط عبر GitHub API بتاريخ 2026-11-02** لاستخدامها في 12.1: `pallets/jinja` **BSD-3-Clause** (جinja2 3.1.6 مثبَّت ضمنياً عبر starlette؛ يصبح تبعية صريحة في 12.1) · `bigskysoftware/htmx` **0BSD** (ملف LICENSE؛ GitHub يعرض NOASSERTION) · `picocss/pico` **MIT** · `umami-software/umami` **MIT**. لا React/Next في V1 (يؤخّر App Review — Q12).

---

## 1. النطاق

### 1-أ. داخل النطاق
- Part A: H1–H6 كما في `sprints/SPRINT-11.1_REVIEW.md` §4 **حرفياً** + اختبار PG لكل بند.
- Part B: حلقة الليد (POST_M7 §1-ب «ما يلزم في الكود») · وحدة `pulse` بالفحوص الثمانية (§1-ج) · أسقف D-082 (300 ردّ/رقم/يوم + kill switch لكل رقم) · هجرة 0016 · `REMOVABILITY.md` + اختبار عزل لكل من `pulse` و`lead_loop`.
- Part C: رابط تشخيص عام `/d/{token}` (قراءة فقط، علامة مائية، لا PII، لا أدلة خام) · Batch Approval API (≤ 50/دفعة؛ صفّ `approval_events` مستقل لكل عنصر — D-055).

### 1-ب. خارج النطاق (مؤجَّل صراحةً)
- **SPRINT-12.1:** واجهة L1 الست (Jinja2 + htmx + Pico، RTL)، تبويب `/admin/ai` و`/admin/entitlements`، Umami، Playbooks JSON، فيديوهات App Review.
- **SPRINT-13:** تنبيه 800 رسالة خدمة/شهر (D-082 الجزء الثاني)، Day-2 Ops، الاحتفاظ D-081، تفريغ الصوت.
- أي تغيير في `ErrorCategory` (12 ثابتة)، `contract.py`، `public_fetch.py`، D-075–D-082 (إلا عبر توصية D-019 في §6).

---

## 2. المواصفات

### Part A — الإغلاقات (كوميت `fix(sprint-12): close H1–H6 (PG17-proven)`)

| # | المطلوب | الملفات (الكود الفعلي هو المرجع §5-5) |
|---|---|---|
| **A-1 (H1) `[DECISION]`** | **دلالة `plan_inactive`:** (أ) **webhook واتساب الموقَّع لا يعيد 5xx أبداً**: عند `plan_inactive` تُخزَّن الرسالة والمحادثة وتُعلَّم المحادثة في حمولة الاستجابة تحت مفتاح جديد `plan_inactive: [conv_id]` (بجانب `quota_exceeded`)؛ (ب) كل مسار **صادر/مكلف** (`approve_item`, `send_text`, `send_template`, `suggest_reply`, `generate`, `brain/*`) ⇒ **402** `{"error":"entitlement_plan_inactive","message":…,"plan":…,"active_until":…}` — **لا 429** (429 محجوز لـ`plan_daily_tokens`/حدود المعدل؛ العميل يميّز «ادفع» عن «انتظر»). التنفيذ: `PlanCounterGuard._ensure` يحوّل `limit_kind == "plan_inactive"` إلى `CounterPlanInactiveError` في منفذ المستهلك (`content/entitlements_port.py`, `whatsapp/entitlements_port.py`) + `plan_inactive_to_http` بجانب `quota_exceeded_to_http`؛ `budget_error_to_http` يحوّل `EntitlementLimitError(plan_inactive)` إلى 402 أيضاً (المسار عبر `BudgetGuard`). **اختبار PG:** `test_a1_suspended_brand_webhook_200_stores_and_flags_on_pg` + `test_a1_suspended_brand_five_outbound_paths_402_on_pg` | `src/modules/entitlements/dependencies.py:82–88`, `content/entitlements_port.py`, `whatsapp/entitlements_port.py`, `whatsapp/webhook.py:315–331`, `whatsapp/router.py:214–222`, `content/router.py:486–512`, `ai_gateway/http_errors.py:35–37` |
| **A-2 (H2)** | `approve_item`: `quota.ensure_can_approve_post(brand_id)` **بعد** `_act(...)`/التفويض وقبل كتابة الحدث. **اختبار PG:** STRANGER + `monthly_posts=0` ⇒ **404** `brand_not_found` (لا 402)؛ MANAGER/OWNER ⇒ 402 | `content/router.py:498–502` |
| **A-3 (H3)** | `brand_brain/router.py::get_brain_gateway`: عند غياب `X-AI-Key` يستهلك `get_budget_guard` (الكتالوج عند تفعيله) بدل `get_ai_gateway` مباشرة؛ مع `X-AI-Key` يبقى بلا كتالوج وبلا fallback (D-077). **اختبار PG:** كتالوج ON + مسار `brand_diagnosis` + لا مفتاح `.env` ⇒ `diagnose` **200** وصف `ai_cost_log.purpose='brand_diagnosis'` | `brand_brain/router.py:223–247`, `ai_gateway/dependencies.py:213–250` |
| **A-4 (H4)** | `validate_provider_host`: مضيف DNS ⇒ `getaddrinfo` **وقت الإنشاء/التعديل** ورفض إن وُجد أي عنوان غير `netguard.is_global_address` (نفس عقد `public_fetch.py:235`)؛ رفض `*.internal`, `*.local`, `*.nip.io`, `*.sslip.io`, `*.localhost`؛ تطبيع الأشكال غير القياسية (`0177.0.0.1`, `2130706433`, `0x7f000001`) إلى IP حرفي قبل الفحص؛ **وقت الاتصال** في `build_provider_client`: ربط `httpx` بـtransport يتحقق من العنوان المحلول (DNS rebinding) — أعد استخدام نمط `public_fetch.py` دون استيراده (D-016: انقل المشترك إلى `src/core/netguard.py`). `AI_ALLOW_LOCAL_PROVIDER_HOSTS` يبقى الاستثناء الوحيد. **اختبار PG:** الستة التي أعطت 201 في المراجعة ⇒ **422** و0 صفوف؛ `api.openai.com` ⇒ 201 (mock `getaddrinfo`) | `ai_gateway/catalog/service.py:127–150, 270–295`, `src/core/netguard.py` |
| **A-5 (H5)** | `CatalogPriceTable`: الفهرس `(provider_id, model_name)`؛ تجاهل النماذج/المزوّدين `enabled=false`؛ `BudgetGuard` يستلم مفتاح النموذج المحلول (المزوّد+الاسم) لا الاسم وحده؛ تسعير `probe` و`ai_cost_log` من `CatalogPriceTable` وحدها (إزالة التضارب). **اختبار PG:** سيناريو H5 ⇒ **503 `ai_model_unpriced`** و0 نداء للمزوّد و0 صف | `catalog/service.py:192–221`, `catalog/admin_service.py:236–300` |
| **A-6 (H6)** | (a) `max_tokens_cap` يُطبَّق في `CatalogGatewayResolver._attempt` و`probe` بـ`min(req.max_tokens, cap)`؛ (b) validator مشترك `api_key ⊕ credential_ref` على `ProviderPatch`؛ (c) `_cipher` بلا تشفير ⇒ **503** `catalog_encryption_unavailable` (لا 404)؛ (d) `PUT /routing/{purpose}` يرفض نموذجاً `capability` لا يطابق الغرض ⇒ 422 `catalog_invalid_pricing` القائم. اختبارات وحدات + PG لـ(a) | `catalog/service.py:346–356`, `catalog/schemas.py:43–63`, `catalog/admin_service.py:101–106`, `catalog/router.py` |

### Part B — حلقة الليد + Pulse + أسقف WA (كوميت `feat(sprint-12): lead loop + pulse-sql + WA caps + migration 0016 (D-079/D-082)`)

| # | المطلوب |
|---|---|
| **B-1 هجرة 0016** | `wa_conversations`: `lead_stage VARCHAR(16) NOT NULL DEFAULT 'new'` + CHECK `ck_wa_conversations_lead_stage IN ('new','qualified','quoted','won','lost','not_lead')` + `lead_stage_set_by_user_id UUID NULL` + `lead_stage_at TIMESTAMPTZ NULL` · جدول `lead_attributions(id, brand_id FK CASCADE, conversation_id FK CASCADE, grade CHECK IN ('measured','inferred','owner_input'), source_kind VARCHAR(16), source_ref VARCHAR(128), signals JSONB, created_at)` + `uq_lead_attributions_conversation_source` · جدول `pulse_alerts(id, brand_id FK CASCADE, kind VARCHAR(32) CHECK في الثمانية, severity CHECK IN ('info','warning','critical'), payload JSONB, created_at, acknowledged_at NULL, acknowledged_by_user_id NULL)` + `ix_pulse_alerts_brand_open` جزئي على `acknowledged_at IS NULL` · جدول `wa_number_caps(account_id PK FK CASCADE, daily_reply_cap INT NOT NULL DEFAULT 300 CHECK > 0, kill_switch BOOL NOT NULL DEFAULT false, updated_at)` · `brand_diagnoses`: `share_token_hash VARCHAR(64) NULL UNIQUE`, `shared_at TIMESTAMPTZ NULL` (إن كان اسم الجدول مختلفاً فالكود هو المرجع §5-5). **لا FK من جداول M1–M7 إلى الجداول الجديدة.** `downgrade` مُختبَر `head ⇒ 0015 ⇒ head` |
| **B-2 حلقة الليد** | `PATCH /brands/{id}/whatsapp/conversations/{conv_id}/lead-stage {lead_stage, objection_code?}` (OWNER/MANAGER؛ VIEWER 403) يكتب الأعمدة الثلاثة + `wa_learning_signals` بالرمز لا النص (D-069) · عند الـwebhook: إن وُجد `referral` (موجود منذ 0014) وطابق `source_id` منشوراً في `publish_jobs` ⇒ صف `lead_attributions(grade='measured')`؛ لا استنتاج آلي في V1 (`inferred` ⇒ V1.1) · مقياسان في `brand_brain/domain/metrics.py`: `measured_leads_per_post`, `lead_close_rate` (من `won/(won+lost)`) · `FEATURE_LEAD_LOOP` (افتراضه `true`؛ `false` ⇒ المسار 404 والـwebhook لا يكتب attribution) + `REMOVABILITY.md` |
| **B-3 وحدة `pulse`** | `src/modules/pulse/` (domain/service/infrastructure/router) · `Pulse.run(now) -> list[Alert]` خطوة ثانية في `extra_steps` لـ`scheduler_loop` كل `PULSE_INTERVAL_SECONDS` (افتراضه 900) · الفحوص الثمانية **SQL فقط بلا نموذج** (POST_M7 §1-ج): (1) محادثة بلا ردّ ونافذة 24h تُغلق خلال ≤ 4h ⇒ `critical`؛ (2) متوسط ردّ > 2h/7 أيام؛ (3) لا نشر منذ 7 أيام؛ (4) `publish_failed` خلال 24h؛ (5) توكن Meta ينتهي ≤ 7 أيام (من `social_connections`)؛ (6) استهلاك ≥ 80% من أي عدّاد باقة (عبر منفذ يملكه `pulse` ويُربط في `main.py` — D-016، نفس نمط `counter_guard_dependency`)؛ (7) `quality_rating`/`messaging_limit` متدهور (من `whatsapp_accounts` إن كان العمود موجوداً وإلا سجّل انحرافاً)؛ (8) ≥ 80% من `daily_reply_cap` للرقم · **idempotent:** لا تكرار لتنبيه مفتوح بنفس `(brand_id, kind, payload->>'ref')` · `GET /brands/{id}/pulse/alerts?open=true` + `POST /brands/{id}/pulse/alerts/{id}/ack` (OWNER/MANAGER) · `FEATURE_PULSE` (افتراضه `true`) + `REMOVABILITY.md` + `tests/isolation/test_pulse_removable.py` |
| **B-4 أسقف D-082** | `wa_number_caps` يُقرأ في `send_text`/`send_template`: `kill_switch` ⇒ **423 `wa_number_disabled`**؛ تجاوز `daily_reply_cap` (عدّ `wa_messages` الصادرة لليوم UTC للرقم) ⇒ **429 `wa_daily_cap_reached` {cap, used, resets_in_seconds}` · الإدارة عبر `PATCH /admin/entitlements/wa-caps/{account_id}` (PlatformAdmin؛ **يُحتسب مساراً محمياً جديداً** ⇒ عدّل العدد إلى **82** وسجّل) · البث الجماعي يبقى غير موجود كمسار (D-082) |

### Part C — الرابط العام + Batch (كوميت `feat(sprint-12): public diagnosis link /d/{token} + batch approval (D-055)`)

| # | المطلوب |
|---|---|
| **C-1 `/d/{token}`** | `POST /brands/{id}/brain/diagnoses/{diag_id}/share` (OWNER فقط) ⇒ يولّد token عشوائي 32 بايت، يخزّن `sha256` فقط، يعيد الرابط مرة واحدة؛ `DELETE …/share` يلغيه · `GET /api/v1/d/{token}` **عام** (7 عام): يعيد `DiagnosisPublicShare` = العنوان + 3–5 توصيات مختصرة + درجة الأدلة **بلا** أسماء منافسين خام/روابط/أرقام/PII + `watermark: "Generated by <APP_NAME>"` + `cta_url` · معدل 60/دقيقة/IP على المسار العام (`RateLimiter` القائم) · `Cache-Control: public, max-age=300` · **لا نداء AI** في المسار العام · اختبار: VIEWER لا يستطيع المشاركة؛ token ملغى ⇒ 404؛ الجسم لا يحوي أي حقل من قائمة محظورة |
| **C-2 Batch Approval** | `POST /brands/{id}/approvals/batch {items:[{kind: content_item|reply_draft|recommendation, id, action: approve|reject}]}` (≤ 50؛ OWNER/MANAGER) ⇒ يعالج كل عنصر عبر **الخدمة القائمة نفسها** لكل نوع (لا منطق موافقة جديد) ويعيد `results[]` لكل عنصر بحالة/خطأ مستقل (207-style داخل 200) · كل عنصر ⇒ صفّ `approval_events` مستقل بنفس الفاعل والطابع (D-055) · ردود انتهت نافذتها ⇒ `skipped: window_closed` · حصة `monthly_posts` تُفحص لكل عنصر محتوى (A-2) |

---

## 3. معايير القبول (المخطط يتحقق منها حرفياً على PG 17 UTF8 ببيئة مُعاد بناؤها)

1. **ruff** نظيف على شجرة كل كوميت؛ **CI 5/5 لكل كوميت** (ادفع كل Part فور اكتماله).
2. المجموعة الكاملة على PG 17 **و** بوابة بلا DB `--cov-fail-under=95 --cov-precision=2` ≥ 95.00 لكل كوميت.
3. `alembic upgrade head ⇒ downgrade 0015 ⇒ upgrade head` يمرّ؛ كل الجداول/الأعمدة/القيود المسمّاة تختفي وتعود.
4. **H1:** علامة `is_active=false` ⇒ webhook محادثة جديدة **200** + صف `wa_messages` + `plan_inactive:[conv]`؛ المسارات الخمسة الصادرة ⇒ **402 `entitlement_plan_inactive`**؛ 0 حالات 500 في المجموعة.
5. **H2:** STRANGER + حصة 0 ⇒ 404. **H3:** brain عبر الكتالوج 200 بلا `.env`. **H4:** الستة ⇒ 422. **H5:** ⇒ 503 `ai_model_unpriced`. **H6a:** المزوّد يستلم `max_tokens ≤ cap`.
6. Pulse: الفحوص الثمانية لكل منها اختبار PG يولّد تنبيهاً ثم لا يكرّره في الدورة التالية؛ `FEATURE_PULSE=false` ⇒ المجدول لا يكتب شيئاً وM1–M7 خضراء.
7. D-082: الرسالة 301 في اليوم ⇒ 429؛ `kill_switch` ⇒ 423؛ الـwebhook الوارد غير متأثر.
8. `/d/{token}`: جسم الاستجابة لا يحوي أياً من `competitor`, `phone`, `email`, `http`, `wa_id`, `evidence_url` (grep على JSON)؛ token ملغى ⇒ 404؛ 61 طلباً/دقيقة ⇒ 429.
9. Batch: 50 عنصراً ⇒ 50 صفاً في `approval_events`؛ 51 ⇒ 422؛ عنصر فاشل لا يُفشل الدفعة.
10. `PROTECTED_ROUTE_COUNT` = **82** (77 + lead-stage + pulse×2 + batch + wa-caps؛ `share`/`unshare` يُضافان ⇒ إن أصبح 84 فسجّل العدد الفعلي في §6 — **الكود هو المرجع**) · العامة **7**.
11. greps §4-3 = 0 · `ErrorCategory` = 12 · `contract.py`/`public_fetch.py`/`pyproject.toml` بلا تغيير.
12. كل وحدة جديدة (`pulse`, امتداد lead loop) لها `REMOVABILITY.md` + اختبار عزل.

---

## 4. أوامر

```bash
# 0) البداية
git checkout work/implementation && git fetch origin && git merge --ff-only origin/main

# 1) بيئة PG 17 UTF8 (كما في SPRINT-11/11.1)
initdb -D /var/lib/pg17 -U postgres --auth=trust -E UTF8 --locale=C.UTF-8
pg_ctl -D /var/lib/pg17 -l /var/lib/pg17/log.txt -o "-p 5432 -c listen_addresses=127.0.0.1" start
psql -h 127.0.0.1 -U postgres -c "CREATE DATABASE ai_growth_os_test ENCODING 'UTF8' TEMPLATE template0"
psql -h 127.0.0.1 -U postgres -d ai_growth_os_test -c "CREATE EXTENSION vector"
pip install -e ".[dev]"

# 2) بعد كل Part
ruff check src tests alembic scripts && ruff format --check src tests alembic scripts
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_growth_os_test pytest -q --cov=src --cov-fail-under=95 --cov-precision=2
env -u TEST_DATABASE_URL pytest -m "not integration" -q --cov=src --cov-fail-under=95 --cov-precision=2
alembic -x database_url=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_growth_os_test upgrade head

# 3) فحوص grep الملزمة (0 نتائج)
grep -rn "learning-consent" src/modules/entitlements
grep -rn "tenancy.infrastructure" src/modules --include=*.py | grep -v "src/modules/tenancy/\|brand_brain/infrastructure/postgres_repository.py"
grep -rn "src.modules.entitlements" src --include=*.py | grep -v "src/modules/entitlements/\|src/main.py\|ai_gateway/dependencies.py"
grep -rn "ai_gateway.catalog" src --include=*.py | grep -v "src/modules/ai_gateway/\|src/main.py"
grep -rn "get_secret_value()" src/modules/ai_gateway/catalog | grep -v "credential"
grep -rn "src.modules.pulse" src --include=*.py | grep -v "src/modules/pulse/\|src/main.py"          # جديد
grep -rn "brand_brain.public_fetch" src/modules/ai_gateway                                              # جديد: 0 (المشترك في core/netguard)
git diff origin/main -- src/modules/ai_gateway/contract.py src/modules/brand_brain/public_fetch.py pyproject.toml | wc -l   # 0

# 4) الدفع بعد كل كوميت
git push origin work/implementation
```

---

## 5. التزامات المنفذ

1. **لا دمج** إلى `main` (D-063). PR واحد من `work/implementation`.
2. **لا** `ErrorCategory` جديد؛ **لا** تبعية جديدة؛ **لا** `# pragma: no cover` جديد؛ **لا** لمس `public_fetch.py` (انقل المشترك إلى `core/netguard.py`).
3. **Part A أولاً ويُدفع وحده** — هو شرط رفع الحظر التشغيلي.
4. **ادفع بعد كل كوميت** وحدّث §6 في كوميت رابع `docs(sprint-12): executor report`.
5. إن تعارض هذا التوجيه مع الكود الفعلي (اسم جدول/دالة/سطر)، **الكود الفعلي هو المرجع**: نفّذ المقصود وسجّل الفرق في §6.
6. **صمّام الحجم:** إن تجاوز A+B معاً 1,300 سطر `src/` فعلياً، سلّم C بـ`/d/{token}` فقط وأجّل Batch إلى 12.1 وسجّل.
7. لا نص خام من العميل في `lead_attributions.signals` أو `wa_learning_signals` (D-069) — رموز فقط.

---

## 6. تقرير المنفذ (يملؤه المنفذ)

> أُنجز على `work/implementation` فوق `main` @ `e66e561` (دمج checkpoint المخطط فوق `0efaa92`). كل Part دُفع وحده وحصل على CI مستقل. الأرقام المحلية على **PostgreSQL 17.11 UTF8 + pgvector** (منفذ 5433؛ بيئة أُعيد بناؤها بعد كل إعادة ضبط للساندبوكس — ≥ 10 مرات في هذا السبرنت؛ كل دفعة دُفعت فوراً). **الكود هو المرجع** (§5-5): كل فرق عن §2 مُسجَّل أدناه.

| البند | القيمة |
|---|---|
| SHA الكوميتات | **A:** `8ed1346` `fix(sprint-12): close H1–H6 (PG17-proven)` · **B:** `5233aaa` `feat(sprint-12): lead loop + pulse-sql + WA caps + migration 0016 (D-079/D-082)` · **C:** `38bd73d` `feat(sprint-12): public diagnosis link /d/{token} + batch approval (D-055)` (**C-1 فقط — الصمّام**؛ انظر الانحراف 1) · **C-fix:** `f606350` `test(sprint-12): pin the Pulse PG test to a deterministic noon-UTC clock` (اختبار فقط — انظر الانحراف 12) · **docs:** هذا الكوميت · الأساس `main` @ `e66e561` |
| نتائج `pytest` (العدد/التغطية) لكل كوميت — كامل **و** بلا DB | **A `8ed1346`:** CI **5/5** ✅ — بلا DB **1,371 passed / 1 skipped / 0 failed · 95.10%** (`-m "not integration" --cov-fail-under=95 --cov-precision=2`) · تكامل PG **134 / 0**. **B `5233aaa`:** CI lint/secrets/licenses/integration ✅ (تكامل **139 / 0**)؛ وظيفة `unit` **أُلغيت** بـ`concurrency: cancel-in-progress` في الـworkflow لحظة دفع أول WIP لـPart C (ليست فشلاً). دليل شجرة B: محلياً `lead_loop/caps` 13 + `pulse` 12 + عزل/عدّ 6 + `test_main` 40 ✅، وشجرة C الحاوية لها كاملةً نجحت 5/5 (أدناه). **C:** `3675da6` = **شجرة `38bd73d` حرفياً** قبل السحق (0 فرق): CI **5/5** ✅ — بلا DB **1,421 passed / 1 skipped / 0 failed · 95.28%** · تكامل PG **140 / 0**. `38bd73d` نفسه: lint/secrets/licenses/integration ✅ و`unit` **أُلغيت** بالتزامن لحظة دفع كوميت docs (كشجرة B). **تشغيل CI لأول كوميت docs (`62f5fb0`، نفس شجرة `src`) كشف عشوائية ساعة في `test_b3_pulse_…` عند 00:03 UTC (الفحص 8 عدّ 1/300 بدل 250/300) ⇒ `f606350` يثبّت ساعة الاختبار؛ محلياً `test_sprint12_b_pg.py` 5/0 على PG 17.11. CI لـ`f606350` ورأس docs: يُثبَّت في وصف الـPR (الهدف 5/5). **محلياً على PG 17.11 (شجرة C):** تكامل **140 / 0** (250s) · وحدات بلا DB النصف الأول (ai_gateway/brand_brain/content/whatsapp) **624 / 0**؛ النصف الثاني قُطع بإعادة ضبط الساندبوكس (985 MB) — CI هو الدليل المكتمل للبوابة ≥ 95.00%. |
| `PROTECTED_ROUTE_COUNT` / العامة | **83** محمياً / **7** عامة تحت `/api/v1` (92 عملية OpenAPI مع `/health`×2). الفرق عن توقّع التوجيه (82): **+2** `POST` **و** `DELETE …/brain/diagnoses/{diag_id}/share` (مساران لا PATCH) **−1** `POST …/approvals/batch` (مؤجَّل بالصمّام). الجديد من OpenAPI الحي: `PATCH …/whatsapp/conversations/{conversation_id}/lead-stage` · `GET …/pulse/alerts` · `POST …/pulse/alerts/{alert_id}/ack` · `PATCH /admin/entitlements/wa-caps/{account_id}` · `POST`/`DELETE …/brain/diagnoses/{diag_id}/share` · عام: `GET /api/v1/d/{token}`. مُحدَّث في `tests/test_auth_guard.py` (PUBLIC + 83)، `tests/modules/content/test_router.py` (83)، `tests/modules/brand_brain/test_b1_routes.py` (brain 15 ⇒ 17). |
| هجرة 0016 ذهاباً وإياباً | ✅ حقيقية على PG 17.11: `alembic upgrade head` ⇒ `0016 (head)` ⇒ `alembic downgrade 0015` ⇒ `0015` ⇒ `alembic upgrade head` ⇒ `0016 (head)`. `tests/integration/test_sprint12_b_pg.py::test_b_migration_0016_round_trip_on_pg` يثبت: 3 جداول (`lead_attributions`, `pulse_alerts`, `wa_number_caps`) + 5 أعمدة (`wa_conversations.lead_stage/lead_stage_set_by_user_id/lead_stage_at`, `brand_recommendations.share_token_hash/shared_at`) + 11 قيداً مسمّى + فهرس تختفي وتعود؛ لا شيء آخر يُحذف؛ CHECKs ترفض الصفوف السيئة؛ FK CASCADE. 24 تأكيداً قديماً لرأس `"0015"` ⇒ `"0016"` في `test_postgres_persistence.py` + خط أساس 0015 يستثني جداول 0016. |
| نتائج grep الملزمة (§4-3) | الثمانية = **0** على شجرة C: (1) `learning-consent` في entitlements ⇒ فارغ · (2) `tenancy.infrastructure` خارج tenancy/brand_brain-PG ⇒ فارغ · (3) `src.modules.entitlements` خارج entitlements/main/ai_gateway-deps ⇒ فارغ · (4) `ai_gateway.catalog` خارج ai_gateway/main ⇒ فارغ · (5) `get_secret_value()` غير credential في catalog ⇒ فارغ · (6) `src.modules.pulse` خارج pulse/main ⇒ فارغ · (7) `brand_brain.public_fetch` في ai_gateway ⇒ فارغ · (8) `git diff origin/main -- contract.py public_fetch.py pyproject.toml \| wc -l` ⇒ **0**. إضافة: `# pragma: no cover` جديد = **0** (أُزيل الوحيد الذي أُضيف في B)؛ `ErrorCategory` = **12**؛ ملفات محظورة = **0**؛ `ruff check` + `ruff format --check` على `src tests alembic scripts` نظيف. |
| أسطر `src/` المضافة لكل Part (خام/فعلي) | `git diff --numstat -- src`: **A** +510 / −89 = **421** فعلياً (خام 510) · **B** +1,636 / −14 = **1,622** (خام 1,636) · **C** +283 / −0 = **283** · **A+B = 2,043 > 1,300 ⇒ صمّام §4-6 مُفعَّل** · **A+B+C = +2,429 / −103 = 2,326** (التوجيه قدّر ≈ 1,600). سبب تجاوز B: وحدة `pulse` كاملة (domain/service/PG repo/router/step ≈ 700) + 8 فحوص SQL + أسقف D-082 داخل مسارَي الإرسال + أدمن wa-caps + 0016 (≈ 210) + `REMOVABILITY.md` ×2. |
| انحرافات عن §2 (مع السبب) | **1. الصمّام (§4-6):** A+B = 2,043 فعلياً > 1,300 ⇒ Part C سلّم **C-1 `/d/{token}` فقط**؛ **C-2 Batch Approval مؤجَّل إلى SPRINT-12.1** (عنوان الكوميت أُبقي حرفياً كما يطلب التوجيه؛ الجسم يصرّح بالتأجيل). **2. لا جدول `brand_diagnoses`:** التشخيص لا يُخزَّن إلا كتوصيات `proposed` ⇒ `{diag_id}` **هو معرّف توصية** للعلامة، وختم المشاركة على `brand_recommendations.share_token_hash` (sha256 hex، UNIQUE) + `shared_at` (0016)؛ إعادة المشاركة تدوّر الرمز وتُميت الرابط القديم. **3.** المشاركة بـ`POST`/`DELETE …/share` كما نصّ §2-C-1 لا `PATCH …/diagnoses/{id}` كما في بطاقة التوجيه (لا مسار PATCH للتشخيص) ⇒ 83. **4.** البطاقة العامة: العنوان **مُنظَّف** بـregex (روابط/أرقام/بريد/handles تُحذف، ≤ 80 حرفاً) + ≤ 5 توصيات (`kind`/`title`/`evidence_grade`) + درجة أدلة إجمالية = **أقوى** درجة مُستشهَد بها؛ لا `rationale`/`provenance`/`evidence_ids`/معرّفات؛ `cta_url` = جذر الخدمة. «3–5»: عند وجود أقل من 3 توصيات للعلامة تُعرض الموجودة (حدّ أدنى 1 — لا اختلاق). **5.** حارس OpenAPI `test_openapi_has_no_token_named_fields_except_has_page_token` كان يمنع أي مسار يحوي `token` ⇒ استثناء واحد صريح لـ`/api/v1/d/{token}` (الاسم الحرفي في التوجيه؛ قدرة قابلة للإلغاء لا اعتماد حساب). **6. B-2:** رموز الاعتراض تُكتب صفَّ `lead_attributions(grade='owner_input')` بالرمز فقط (D-069) لا في `wa_learning_signals` (تلك تخصّ قرارات مسودات الرد وتفرض `draft_id`). **7. B-3 فحص (7):** `whatsapp_accounts` بلا `quality_rating`/`messaging_limit` ⇒ الفحص يعيد `[]` ويُسجَّل هنا كما طلب التوجيه. **8. B-3:** عالم الاختبار أنتج **7** تنبيهات لا 6 (`publish_failed` لعلامتين) — سلوك صحيح، التوقع عُدِّل. **9. B:** `tests/conftest.py` يثبّت `get_published_post_lookup ⇒ None` في التطبيق المزيَّف (كان يفتح جلسة PG داخل اختبار وحدات ⇒ فشل CI الأول لـB)، و`test_main` يتوقع خطوتين في `extra_steps` (`settle_stale_reservations` + `pulse_step` لأن `FEATURE_PULSE` افتراضه `true`). **10.** منفذ العدّادات يملكه `pulse` ويُربط في `main.py` (D-016) عبر `entitlements.dependencies.PgPlanCounterUsageReader`؛ `entitlements` لا يستورد `pulse` (اختبار عزل AST `tests/isolation/test_pulse_removable.py`). **11.** CI لشجرة B: `unit` أُلغيت بالتزامن (انظر صف pytest). **12. عشوائية ساعة في اختبار Pulse (PG):** `daily_cap_near` يعدّ الصادر منذ 00:00 من يوم `now` (نفس تعريف `inbox.py` D-082)، والاختبار كان يختم الصفوف بـ`now() − 10 دقائق` ⇒ عند تشغيل CI قرب منتصف الليل UTC تسقط على اليوم السابق ⇒ فشل عابر. الإصلاح `f606350` **اختبار فقط** (ساعة صريحة 12:00 UTC تُمرَّر للصفوف ولـ`Pulse.run`)؛ لا تغيير في `src/`. |
| ما يحتاج قرار المالك | **(أ)** قبول الصمّام: C-1 الآن و**Batch Approval ⇒ SPRINT-12.1** (أو 12.0.1 منفصل). **(ب)** قبول الحجم الإجمالي (2,326 فعلياً مقابل ≈ 1,600) — كله مطلوب نصاً في §2. **(ج)** الاستثناء الوحيد في حارس `token` للمسار العام `/d/{token}` (البديل: إعادة التسمية إلى `/d/{share_id}` — يخالف اسم التوجيه). **(د)** 🔑 **تدوير توكن GitHub فوراً** — ظهر في المحادثة في كل دورة (**≥ 58 مرة**) + حماية `main`. **(هـ)** قبل الربط الحي: `FEATURE_PULSE`/`FEATURE_LEAD_LOOP` افتراضهما `true`، `PULSE_INTERVAL_SECONDS=900`، سقف D-082 الافتراضي للرقم — راجعها. |

---

## 7. ما ينتظر المالك

1. **تدوير التوكن** (`ghp_…` ظهر في المحادثة **≥ 16 مرة** في جلسة المخطط وحدها) + حماية فرع `main`.
2. **حظر تشغيلي حتى دمج SPRINT-12 Part A:** لا `FEATURE_PROVIDER_CATALOG_ENABLED=true`، لا `PLATFORM_ADMIN_USER_IDS`، لا `suspend` لعلامة لها webhook واتساب حيّ.
3. بعد قبول SPRINT-12: المخطط يصدر **SPRINT-12.1** (L1 الست + Umami + Playbooks + تبويب AI) ⇒ تقديم App Review ⇒ قياس TTFV على 3 علامات تجريبية.
