# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-12.1-A رُوجع مستقلاً على PostgreSQL 17.11 UTF8 و**قُبل ودُمج** (PR #49 ⇒ `main @ 278de36`؛ التقرير [`sprints/SPRINT-12.1-A_REVIEW.md`](./sprints/SPRINT-12.1-A_REVIEW.md)). المِجسّ الحي أثبت **U1-a/U1-b/U2/U3/U4** في طبقة الواجهة فقط (لا مسار `/api/v1/*` متأثر) ⇒ تُغلق **أولاً** في Part C-0 أدناه قبل أي شاشة جديدة. 🔴 **لا تجربة حيّة لـ`/app` ولا تصوير App Review ولا قياس TTFV قبل دمج C-0.**

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-12.1-B-L1-HARDEN-AND-COMPLETE` |
| **النوع** | **C-0** تصليب الجلسة/CSRF/htmx (إغلاق U1–U4، بلا هجرة) ⇒ **C** شاشات المشغّل الثلاث (`content`, `diagnosis`, `settings`) ⇒ **D** تبويبا المشرف (`/app/admin/ai` D-077 + `/app/admin/entitlements`) + Umami + بذور Playbooks ⇒ docs |
| **الحالة** | `ISSUED` — جاهز فور دمج هذا التوجيه في `main` (المخطط يدمجه — D-063) |
| **يبدأ من** | `main` @ **SHA دمج هذا التوجيه** (يسجَّل في `WORK_STATE.md` §6). أول أمر: `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` |
| **الفرع المستهدف** | `work/implementation` ⇒ **PR واحد** بعنوان `SPRINT-12.1-B: L1 hardening (U1–U4) + content/diagnosis/settings + admin tabs + Umami + playbooks` |
| **الكوميتات (بالترتيب، كلٌّ يُدفع فور اكتماله)** | **C-0** `fix(sprint-12.1): UI session — app_access cookie, refresh only on expiry, per-login CSRF nonce, HX-Redirect, flash in partials (U1–U4)` — **يُدفع وحده أولاً** ⇒ **C** `feat(sprint-12.1): L1 content/diagnosis/settings screens` ⇒ **D** `feat(sprint-12.1): admin AI catalog + entitlements tabs, Umami tag, sector playbooks` ⇒ **docs** (§6 + `WORK_STATE.md`) |
| **تبعيات جديدة** | **لا شيء.** (Umami = وسم `<script>` خارجي اختياري فقط؛ Playbooks = JSON) |
| **الحجم المتوقع (G6)** | C-0 ≈ 150 · C ≈ 750 · D ≈ 600 ⇒ **≈ 1,500 سطر فعلي ≤ 1,600**. **صمّام مرتَّب الأولوية** (§1-ج). |
| **المسارات المحمية** | **`PROTECTED_ROUTE_COUNT == 84`** بلا تغيير · العامة **7** · `/app/*` يرتفع من 11 إلى ≈ 30 مساراً خارج العدّاد (يُحصى في `test_ui_routes_are_hidden_from_openapi_and_counted`) |
| **الهجرات** | **لا هجرة.** إن احتجت عموداً، توقّف وسجّل توصية D-019 في §6. |
| **تاريخ الإصدار** | 2026-11-03 |

---

## 0. لماذا؟ (ما يجب أن يفهمه المنفذ قبل أي سطر)

1. **الجذر المعماري لـU1/U2** (`[VERIFIED live]` في المراجعة §3): `_resolve_user` يعامل `POST /auth/refresh` كمصادقة **لكل طلب**. النتائج المُقاسة بالمحدِّد الحقيقي: الطلب العاشر بعد الدخول ⇒ `303 /app/login` + مسح الكوكي + إعادة الدخول ⇒ 429 (U1-a)؛ وطلبان بنفس الكوكي (استطلاع `every 30s` + نقرة) ⇒ `revoke_all_for_user` ⇒ **خروج من كل الأجهزة** (U1-b). ولأن `sid` يتغيّر كل طلب، رُبط CSRF بـ`user_id` ⇒ ختم ثابت مدى الحياة (U2).
2. **الحل الواحد بلا هجرة:** كوكي ثانية **`app_access`** تحمل access JWT القصير القائم (`auth_access_ttl_seconds` = 15 دقيقة) يُفكّ محلياً بـ`AccessTokenCodec` — `/auth/refresh` يُستدعى **فقط** عند انتهاء الصلاحية أو 401 من API. CSRF = nonce عشوائي لدورة الدخول. التفاصيل الملزمة في §2 C-0.
3. **`CurrentUserFresh` لا يتأثر:** المسارات الحساسة في API تفحص `refresh_sessions` بنفسها؛ إبطال من جهاز آخر ⇒ API يعيد 401 ⇒ الواجهة تمسح الكوكيات وتعيد `303` (+`HX-Redirect`). نافذة 15 دقيقة على القراءة فقط = عقد الـSPA الأصلي.
4. **D-077 عملياً:** `/app/admin/ai` ليس «عرضاً» للكتالوج بل **لوحة تحكم حيّة كاملة**: إضافة/تعديل/تعطيل مزود، نماذج وتسعير (بما فيه `$0` والصوت و`max_tokens_cap`)، سلاسل توجيه/fallback لكل غرض، ومختبر `probe` بنقرة. بلا SSH ولا كود. الأسرار **لا تُعرض أبداً** (API لا يعيدها أصلاً — `has_api_key`/`credential_ref` فقط).
5. **TTFV ≤ 30 دقيقة** يُقاس **بعد** هذا السبرنت على 3 علامات تجريبية: دخول ⇒ أول توصية مقبولة أو أول ردّ واتساب مُرسَل. الـPlaybook القطاعي يُسرّعه (قيم أولية للملف + أعمدة محتوى + عتبات Pulse).

---

## 1. النطاق

### 1-أ. داخل النطاق
- **C-0** (ملزم أولاً): `src/ui/session.py`, `src/ui/router.py`, `base.html`, `partials/today_cards.html`, `partials/thread.html`, `i18n/ar.json` + اختبارات §3-1.
- **C**: `/app/b/{brand_id}/content` · `/app/b/{brand_id}/diagnosis` · `/app/b/{brand_id}/settings` (+ جزئيات htmx).
- **D**: `/app/admin/ai` (4 تبويبات: providers/models/routing/probe) · `/app/admin/entitlements` · وسم Umami · `src/modules/brand_brain/playbooks/{restaurants,clinics}.json` + قارئ عند الإقلاع + بطاقة «Playbook» في «اليوم».
- `src/ui/REMOVABILITY.md` يُحدَّث؛ `tests/isolation/test_ui_removable.py` يُوسَّع للملفات الجديدة.

### 1-ب. خارج النطاق (رفض صريح)
- أي هجرة · أي `ErrorCategory` · أي تبعية Python جديدة · أي SPA/bundler · أي CDN · أي منطق أعمال في `src/ui` (الواجهة تستدعي `/api/v1` فقط عبر `ApiClient`) · تعديل `src/modules/auth/*` (الحل كله في `src/ui`) · `media_jobs` · SRI (قرار المالك: مؤجَّل).

### 1-ج. صمّام الحجم (G6) — مرتَّب الأولوية، **لا يُضحّى بالبنود 1–4 أبداً**
| الأولوية | البند | عند الاقتراب من 1,600 |
|---|---|---|
| 1 | **C-0** كاملاً | لا يُمسّ |
| 2 | `/app/b/{id}/content` + `/app/b/{id}/diagnosis` | لا يُمسّ |
| 3 | `/app/admin/ai` بتبويباته الأربعة **بما فيها probe** | لا يُمسّ |
| 4 | `/app/b/{id}/settings` | لا يُمسّ |
| 5 | `/app/admin/entitlements` | يُختصر إلى تفعيل/تمديد + wa-caps فقط |
| 6 | بذور Playbooks + بطاقة اليوم | قطاع واحد بدل اثنين |
| 7 | وسم Umami | يُؤجَّل إلى SPRINT-13 (سجّل في §6) |

تجاوز 1,600 فعلياً بعد تطبيق 5⇒7 = توقّف وسجّل في §6 لقرار المخطط.

---

## 2. المواصفات (مقتضبة — الكود القائم هو المرجع §5-5)

### C-0 — تصليب الجلسة (يغلق U1-a, U1-b, U2, U3, U4)

**C-0.1 كوكيان بدل واحدة** (`session.py`):
- `app_session` كما هي (قيمة refresh، `Path=/app`, `HttpOnly; Secure; SameSite=Lax`, `Max-Age=auth_refresh_ttl_seconds`).
- **جديدة** `app_access` = access JWT الخام من `/auth/login` أو `/auth/refresh`؛ نفس الخصائص؛ `Max-Age=auth_access_ttl_seconds`.
- **جديدة** `app_csrf` = `secrets.token_urlsafe(32)` يُولَّد **عند الدخول فقط**؛ `HttpOnly; Secure; SameSite=Lax; Path=/app`; `Max-Age=auth_refresh_ttl_seconds`. تُمسح الثلاث عند الخروج/الطرد.

**C-0.2 `_resolve_user` الجديد** (`router.py`):
1. اقرأ `app_access` ⇒ `AccessTokenCodec.decode` محلياً (يرفض المنتهي/التالف). **نجاح ⇒ `AppUser` بلا أي نداء شبكة** و**بلا تدوير** للكوكي.
2. فشل/غياب ⇒ `app_session` موجودة؟ ⇒ `POST /auth/refresh` **مرة واحدة**:
   - 200 ⇒ `AppUser` + علامة `rotated=True` ⇒ الاستجابة تضبط `app_access` + `app_session` الجديدتين.
   - **429 ⇒ لا مسح للجلسة**: أعد `None` مع علامة `retry_after` ⇒ الصفحة تعرض `error.rate_limited` مترجَماً بـ`503 Retry-After` (أو، إن كان الطلب htmx، `HX-Retarget` إلى `#flash`) — **الكوكيات تبقى كما هي**.
   - 401 (منتهٍ/مُبطَل/reuse) ⇒ `None` ⇒ `_denied` يمسح الثلاث.
3. **401 من أي نداء API أثناء عرض الصفحة** (إبطال من جهاز آخر — H6/`CurrentUserFresh`): محاولة refresh **واحدة** ثم إعادة النداء؛ إن فشلت ⇒ `_denied`.
4. **تزامن الطلبات:** لأن refresh لا يُستدعى إلا عند الانتهاء، التوأم المتأخر يحمل `app_access` صالحاً ويُخدَم محلياً. في الحالة الهامشية (توأمان كلاهما منتهٍ) أضف **قفلاً داخل العملية** `asyncio.Lock` مفتاحه sha256(refresh_value) حتى يستدعي واحد فقط refresh والآخر ينتظر ويقرأ النتيجة (قاموس `dict[str, tuple[expires_at, AppUser]]` محدود بـ256 مدخلاً، يُنظَّف lazily). **لا Redis، لا جدول.**

**C-0.3 CSRF**: `csrf_token(settings, nonce)` = `HMAC(token_encryption_key ∥ fallback auth_jwt_secret, nonce)[:32]`؛ `verify_csrf` يقرأ `app_csrf` من الطلب. **يُفحص على كل `POST /app/*` بلا استثناء بما فيها `/app/logout`** (فشل ⇒ 403 + الكوكيات تبقى). اختبار: ختمان من دورتي دخول **مختلفان**.

**C-0.4 htmx**: (أ) كل استجابة `_denied` لطلب `HX-Request: true` ⇒ `HX-Redirect: /app/login?next=…` مع **200 جسم فارغ** (لا 303 — المتصفح يتبعه شفافاً ويحقن صفحة الدخول). (ب) `partials/today_cards.html` و`partials/thread.html` يبدآن بـ`{% if flash %}<p role="alert" data-flash>…{% endif %}` (thread يحويه أصلاً — أضفه لـtoday). (ج) 403 CSRF لطلب htmx ⇒ `HX-Retarget: #flash` + `HX-Reswap: innerHTML` وجسم `<p role="alert" data-flash>…</p>`؛ أضف `<div id="flash"></div>` في `base.html` داخل `<main>` قبل `{% block content %}`.

**C-0.5 U4**: `ar.json` + `severity.info` · شريط الباقة: `{{ t("plan.posts") }}: X / Y · {{ t("plan.conversations") }}: Z / W` من `plan.usage.monthly_wa_conversations` / `plan.limits.monthly_wa_conversations` (أضف `plan.conversations`). **اختبار:** مفاتيح `severity.*` في `ar.json` ⊇ قيم `Severity`؛ مفاتيح `stage.*` ⊇ `LeadStage`؛ `objection.*` ⊇ `ObjectionCode`؛ `alert.*` ⊇ `ALERT_KINDS`؛ `delivery.*` ⊇ `DeliveryStatus` (اختبار واحد مُعمَّم يمنع تسرّب مفتاح خام مستقبلاً).

**C-0.6 static**: `app.mount("/app/static", StaticFiles(...))` يبقى؛ أضف middleware خفيفاً **داخل `src/ui`** (أو `Response` wrapper في `main.py` بسطرين) يضبط `Cache-Control: public, max-age=86400, immutable` على `/app/static/vendor/*` فقط.

### C — شاشات المشغّل الثلاث (كلها عبر `ApiClient`؛ أدوار API هي المرجع؛ أخطاء ⇒ flash مترجَم)

**C-1 `/app/b/{brand_id}/content`**: قائمة `GET …/content/items` مع مرشّح `status` (tabs: draft/pending_approval/approved/scheduled/published/failed) · أزرار لكل عنصر حسب حالته (`submit`/`withdraw`/`approve`/`reject`/`schedule`/`unschedule`/`publish/retry`) · **«وافق على المحدَّد»** ⇒ `POST …/approvals/batch` (checkboxes ≤ 50؛ النتيجة تُعرض لكل عنصر `approved`/`skipped:<code مترجَم>`) · نموذج «توليد» ⇒ `POST …/items/{id}/generate` (402/429 ⇒ flash) · نموذج جدولة بتاريخ/وقت ⇒ `schedule`. **لا ينشر إلا ما وافق عليه إنسان** (D-055 — الأزرار تعكس آلة الحالات القائمة؛ لا اختصار).

**C-2 `/app/b/{brand_id}/diagnosis`**: زر «شخّص الآن» ⇒ `POST …/brain/diagnose` (402/429/`consent` ⇒ flash) · قائمة `GET …/brain/recommendations` مع **`evidence_grade`** ظاهراً كشارة (`measured`/`inferred`/`estimated` مترجَمة) + عدد الأدلة · لكل توصية `accept`/`dismiss` · لكل توصية غير `dismissed` زر «مشاركة رابط عام» ⇒ `POST …/brain/diagnoses/{id}/share` ⇒ الرابط يُعرض **مرة واحدة** في `<input readonly>` + زر نسخ (JS سطر واحد inline `navigator.clipboard`) + زر «إلغاء المشاركة» ⇒ `DELETE …/share` · 409 `recommendation_dismissed` ⇒ flash · تكلفة اليوم من `GET …/brain/cost` كسطر صغير.

**C-3 `/app/b/{brand_id}/settings`**: (أ) اتصالات Meta: `GET …/connections` بحالة كل اتصال + زر `resync` + رابط `connections/start` (يفتح OAuth في نافذة جديدة) · (ب) واتساب: `GET …/whatsapp/account` (الرقم، `quality_rating`، `daily_reply_cap`، `kill_switch` للقراءة فقط) · (ج) الباقة: `GET …/entitlement` كاملة (الخطة، الحدود، الاستهلاك، `active_until`) · (د) موافقة التعلّم: `GET/PUT …/learning-consent` كمفتاح تبديل · (هـ) الأعضاء: `GET …/grants` + إضافة (`POST`) بالبريد والدور + حذف (`DELETE`) — OWNER فقط (API يفرض؛ الواجهة تُخفي الأزرار لغيره عبر `brand.role`).

### D — تبويبا المشرف + Umami + Playbooks

**D-0 الحراسة**: مسارات `/app/admin/*` تتحقق محلياً `user.user_id in settings.platform_admin_user_ids` **قبل** أي نداء ⇒ غير المشرف 404 صفحة (لا 403 — لا نكشف وجود اللوحة). رابط «الإدارة» يظهر في `base.html` فقط إذا `is_admin`. API تفرض `PlatformAdmin` بنفسها على كل نداء.

**D-1 `/app/admin/ai`** (D-077) — 4 تبويبات بـhtmx `hx-get` لكل تبويب:
- **المزودون**: جدول `GET /admin/ai/providers` (slug, kind, base_url, `has_api_key`/`credential_ref`, timeout, enabled, عدد النماذج) · نموذج إضافة (`POST`) بحقول `ProviderCreate` — `api_key` حقل `type=password` **يُرسَل ولا يُعاد أبداً**؛ بعد الحفظ يُعرض `has_api_key=true` فقط · تعديل (`PATCH`) + تعطيل/تفعيل · حذف (`DELETE`) مع تأكيد `confirm()` · أخطاء `provider_host_not_allowed`/`provider_in_use` ⇒ flash مترجَم.
- **النماذج**: جدول `GET /admin/ai/models` مع `provider`, `model_name`, `capability` (text/audio/…), تسعير لكل 1k (إدخال `0` صالح), `max_tokens_cap`, enabled · إضافة/تعديل/حذف.
- **التوجيه**: لكل `purpose` (من `GET /admin/ai/routing`) قائمة مرتَّبة primary ⇒ fallbacks (select متعدد مرتَّب بسيط: صفوف `model_id` + ترتيب) ⇒ `PUT /admin/ai/routing/{purpose}` · عرض «المفعَّل فعلياً الآن» لكل غرض.
- **المختبر (probe)**: اختيار نموذج + prompt (≤ 500) + `max_tokens` (≤ 64) ⇒ `POST /admin/ai/probe` ⇒ بطاقة نتيجة: `ok`, `latency_ms`, `prompt_tokens`/`completion_tokens`, `estimated_cost_usd`, `preview`, و`error_category` **مترجَمة** (12 فئة ⇒ 12 مفتاح `ai_error.*` في `ar.json`). **بلا إعادة محاولة تلقائية.**
- **قواعد عرض الأخطاء:** كل كود D-069 من `/admin/ai/*` يحتاج مفتاح `error.<code>`؛ الاختبار المُعمَّم C-0.5 يُوسَّع ليشمل أكواد `catalog/exceptions.py`.

**D-2 `/app/admin/entitlements`**: نموذج بحث بـ`brand_id` ⇒ `GET /admin/entitlements/{brand_id}` ⇒ بطاقة الخطة + نموذج `POST /admin/entitlements/activate` (plan, months/active_until, ملاحظة InstaPay) + **wa-caps**: `PATCH /admin/entitlements/wa-caps/{account_id}` (`daily_reply_cap`, `kill_switch` كمفتاح تبديل أحمر مع تأكيد).

**D-3 Umami**: إعداد جديد `umami_website_id: str | None = None` + `umami_script_url: str = "https://cloud.umami.is/script.js"` في `Settings` (قراءة فقط — لا يغيّر سلوكاً آخر). `base.html`: `{% if umami_website_id %}<script defer src="{{ umami_script_url }}" data-website-id="{{ umami_website_id }}"></script>{% endif %}` — **لا وسم إطلاقاً عند الغياب** (اختبار). يُستثنى من grep الـCDN بتعليق صريح في الاختبار (الرابط من الإعدادات لا من القالب).

**D-4 Playbooks**: `src/modules/brand_brain/playbooks/restaurants.json` و`clinics.json` (POST_M7 §1-ج: أعمدة محتوى، عتبات Pulse، قوالب ردود أولية، مقاييس، نبرة، تقويم مناسبات) + `playbooks.py` قارئ عند الإقلاع (`load_playbooks()` يُحقّق المخطط بـPydantic ويفشل الإقلاع عند ملف تالف) + `GET /api/v1/playbooks` **عام-للمصادَق** (`CurrentUser`، بلا علامة ⇒ **يرفع `PROTECTED_ROUTE_COUNT` إلى 85** — سجّله) يعيد القائمة · بطاقة «Playbook» في «اليوم» تعرض قطاع العلامة (من `brand_profiles.sector` إن وُجد وإلا select) و3 نصائح اليوم من الـJSON. **تطبيق الـPlaybook على `brand_profiles` كقيمة أولية ⇒ SPRINT-13** (يحتاج قرار على حقول الملف).

---

## 3. معايير القبول (المخطط يتحقق منها حرفياً على PG 17 UTF8 ببيئة مُعاد بناؤها + مِجسّ حي بالمحدِّد الحقيقي)

### 3-1 C-0 (كلها اختبارات جديدة في `tests/ui/`)
1. `test_u1a_thirty_requests_in_a_minute_no_kick`: بالمحدِّد الحقيقي `InMemoryRateLimiter` (override صريح) — دخول ثم **≥ 30 طلب GET/POST على `/app/*` خلال النافذة** ⇒ كلها 200 (لا 303)؛ عدّاد نداءات `/auth/refresh` الفعلية **≤ 1**.
2. `test_u1b_twin_requests_same_cookies_keep_all_sessions`: طلبان بنفس الكوكيات (بما فيها حالة `app_access` منتهٍ مُزوَّر بـ`ttl=0`) ⇒ كلاهما 200 ⇒ جلسات المستخدم الحيّة **= 1** (لا `revoke_all`).
3. `test_refresh_429_keeps_cookies_and_shows_rate_limited`: محاكاة 429 ⇒ لا `Max-Age=0` في `Set-Cookie`؛ الجسم يحوي `error.rate_limited`.
4. `test_api_401_mid_page_triggers_one_refresh_then_denied`: إبطال الجلسة في المستودع الوهمي ⇒ الصفحة ⇒ 303 + مسح الثلاث كوكيات؛ مع `HX-Request` ⇒ 200 + `HX-Redirect`.
5. `test_logout_requires_csrf`: بلا/خاطئ ⇒ 403 والجلسة حيّة؛ صحيح ⇒ 303 + مُبطَلة + الكوكيات الثلاث `Max-Age=0`.
6. `test_csrf_nonce_differs_per_login` + `test_csrf_stamp_from_other_user_rejected`.
7. `test_htmx_action_error_shows_flash_in_partial` (today + thread) · `test_htmx_csrf_403_retargets_flash`.
8. `test_i18n_covers_all_enums` (C-0.5) · شريط الباقة يحوي `Z / W` للمحادثات.
9. `test_vendor_cache_control_immutable`.
10. `tests/test_auth_guard.py::test_app_routes_redirect_without_session` ما زال أخضر (303 أو `HX-Redirect` حسب الترويسة).

### 3-2 C + D
11. كل شاشة: اختبار عرض + اختبار إجراء واحد ناجح + اختبار خطأ ⇒ flash مترجَم (لا 500، لا نص خام).
12. `content`: batch بـ3 عناصر مختلطة ⇒ 3 أسطر نتيجة بترجمة الأكواد · `diagnosis`: الرابط العام يظهر مرة ويُلغى؛ 409 مترجَم؛ `evidence_grade` ظاهر · `settings`: VIEWER لا يرى أزرار المنح.
13. `/app/admin/*` لغير المشرف ⇒ **404** (ليس 403)؛ المشرف ⇒ 200 · إضافة مزود بمفتاح ⇒ الاستجابة والصفحة التالية **لا تحويان المفتاح** (اختبار يبحث عن القيمة حرفياً) · probe بفشل مزوّد ⇒ `error_category` مترجَمة بلا 500 · كل `ErrorCategory` الـ12 لها مفتاح.
14. Umami: بلا `UMAMI_WEBSITE_ID` ⇒ لا `<script` خارجي في أي صفحة؛ معه ⇒ وسم واحد بالـid.
15. Playbooks: ملفان يُحمَّلان؛ ملف تالف ⇒ الإقلاع يفشل برسالة واضحة؛ `GET /api/v1/playbooks` 401 بلا Bearer.
16. `PROTECTED_ROUTE_COUNT == 85` (84 + `GET /api/v1/playbooks`) — أو 84 إن أُجِّل D-4 بالصمّام (سجّل).
17. وحدات بلا DB ≥ **95.00%** (نصفان إن لزم) · تكامل PG **0 فشل** · ruff 0/0 · greps §4 كلها 0 · sha256 vendor مطابق · `contract.py`/`public_fetch.py`/`alembic/`/`src/modules/auth/**` بلا تغيير.
18. الحجم الفعلي C-0+C+D ≤ 1,600 أو صمّام §1-ج مطبَّق ومسجَّل.

---

## 4. أوامر

```bash
# 0) البداية
git checkout work/implementation && git fetch origin && git merge --ff-only origin/main

# 1) بيئة PG 17 UTF8 — كما في SPRINT-12 §4 ثم:
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_growth_os_test pytest -m integration -q

# 2) بعد كل Part
ruff check src tests alembic scripts && ruff format --check src tests alembic scripts
env -u TEST_DATABASE_URL pytest -m "not integration" -q --cov=src --cov-fail-under=95 --cov-precision=2
# OOM؟ نصفان بـ--cov-append ثم: coverage report --precision=2 --fail-under=95

# 3) greps الملزمة (0 نتائج) — الثمانية من SPRINT-12 §4-3 كما هي + :
grep -rn "sqlalchemy\|\.infrastructure" src/ui --include=*.py                 # 0
grep -rn "import httpx\|from httpx" src/ui --include=*.py                       # 0 (D-068)
grep -rn "cdn\.\|unpkg\|jsdelivr\|googleapis" src/ui --exclude=LICENSES.md      # 0 (Umami من Settings لا من القالب)
grep -rnP "[\x{0600}-\x{06FF}]" src/ui/templates                                 # 0
grep -rn "api_key\|get_secret_value" src/ui/templates                            # 0 — لا سرّ يصل قالباً
git diff origin/main -- src/modules/auth/ src/modules/ai_gateway/contract.py src/modules/brand_brain/public_fetch.py alembic/ | wc -l   # 0
sha256sum src/ui/static/vendor/*.js src/ui/static/vendor/*.css                  # يطابق LICENSES.md

# 4) الدفع بعد كل كوميت
git push origin work/implementation
```

---

## 5. التزامات المنفذ

1. **لا دمج** إلى `main` (D-063). PR واحد؛ **C-0 يُدفع وحده أولاً** ويُشغَّل عليه CI قبل C.
2. **لا** `ErrorCategory` جديد؛ **لا** هجرة؛ **لا** `# pragma: no cover` جديد؛ **لا** تبعية؛ **لا** CDN؛ **لا** تعديل في `src/modules/auth/**` (الحل في `src/ui` فقط).
3. **لا سرّ في قالب أو سجل:** `api_key` يُرسَل إلى API ولا يُعاد؛ لا `print`/`logger` لحمولات النماذج.
4. **ادفع بعد كل كوميت** وحدّث §6 في كوميت docs.
5. إن تعارض هذا التوجيه مع الكود الفعلي، **الكود هو المرجع**: نفّذ المقصود وسجّل الفرق في §6.
6. **صمّام §1-ج ملزم بترتيبه**؛ تجاوز بلا تسجيل = رفض.
7. كل نص من العميل أو من المزوّد (`preview` في probe!) يمرّ عبر `autoescape` — اختبار يحقن `<script>` في `preview`.
8. **لا تجربة حيّة على `/app` قبل دمج C-0** (U1 سيطرد المستخدم).

---

## 6. تقرير المنفذ (يملؤه المنفذ)

| البند | القيمة |
|---|---|
| SHA الكوميتات | بداية: `main` @ `c5d9748` (= `f64fd4f` + تخطيط #51). **C-0** `d142e74` (دُفع وحده، CI 5/5 قبل C) · **C** `f6821c6` (CI 5/5) · **D** `52e7012` (CI 4/5 عند كتابة هذا السطر — `unit` قيد التشغيل؛ يُحدَّث في وصف الـPR) · **docs** هذا الكوميت. |
| نتائج `pytest` (العدد/التغطية) لكل كوميت — كامل **و** بلا DB | **CI بلا DB** (`--cov-precision=2`): C-0 `d142e74` **1,487/0 · 95.16%** · C `f6821c6` **1,512/0 · 95.09%** · D `52e7012` محلياً `tests/ui`+`tests/isolation`+`test_auth_guard`+`catalog`+`brain b1` = **378/0** (الكامل بلا DB على CI — الساندبوكس 985 MB يفشل OOM على التشغيل الكامل؛ القيمة النهائية في الـPR). **تكامل PG 17 (CI)**: ✅ على الكوميتات الثلاثة (0 فشل). محلياً لم يُشغَّل PG (الساندبوكس يُعاد ضبطه كل 1–2 دور؛ 15+ مرة في هذا السبرنت). |
| `PROTECTED_ROUTE_COUNT` / العامة / عدد مسارات `/app/*` | **85** (84 + `GET /api/v1/playbooks` بـ`CurrentUser` — D-4 لم يُؤجَّل) / العامة **7** بلا تغيير / `/app/*` **27 مساراً** (11 ⇒ 23 بعد C ⇒ 27 بعد D)، **24 محروساً** بالجلسة (303 أو `HX-Redirect`)، 3 معفاة (GET/POST login + POST logout). |
| أسطر `src/` الفعلية لكل Part + حالة صمّام §1-ج | `git diff --numstat -- src` (أسطر مُضافة): **C-0 428** · **C 706** · **D 362** ⇒ **1,496 مُضافاً** (صافي 1,340 بعد الحذف) **≤ 1,600** ⇒ **الصمّام لم يُفعَّل**: البنود 1–7 كلها مُسلَّمة (admin/entitlements كامل: بحث + تفعيل/تمديد + wa-caps؛ قطاعان؛ Umami). |
| نتائج greps §4 + sha256 vendor | `sqlalchemy\|.infrastructure` في `src/ui` = **0** · `httpx` في `src/ui` = **0** · CDN في `src/ui` (بلا LICENSES) = **0** (رابط Umami من `Settings.umami_script_url` لا من القالب — اختبار `test_umami_tag_absent_by_default_and_present_once_when_configured` يوثّق الاستثناء) · عربية في القوالب = **0** (regex بايثون — `grep -P \x{}` غير مدعوم في الساندبوكس) · `api_key\|get_secret_value` في القوالب = **0** (الحقل `credential` محايد؛ الاسم الفعلي يُركَّب في `_provider_body` بالراوتر) · `git diff origin/main -- auth/ contract.py public_fetch.py alembic/` = **0 سطر** · `sha256sum` htmx/Pico **مطابق** لـ`LICENSES.md` · `# pragma: no cover` = **20** (خط الأساس) · `ErrorCategory` = **12** · هجرات **16** بلا تغيير · `pyproject.toml` بلا تغيير. |
| نتيجة اختبارات U1–U4 (3-1) بالأرقام (عدد الطلبات بلا طرد، عدد نداءات refresh) | `tests/ui/test_ui_hardening.py` (15 اختباراً) بالمحدِّد الحقيقي `InMemoryRateLimiter`: **U1-a** 32 طلباً GET/POST على `/app/*` خلال النافذة ⇒ **32 × 200، 0 طرد، نداءات `/auth/refresh` = 0** (وصول محلي بـ`app_access`؛ ≤ 1 مطلوب) · **U1-b** توأمان بنفس الكوكيات مع `app_access` منتهٍ (`ttl=0`) ⇒ 200 + 200، **جلسات المستخدم الحيّة = 1**، refresh واحد (قفل `asyncio.Lock` + ذاكرة 30 ث) · 429 محاكى ⇒ لا `Max-Age=0`، الجسم يحوي `error.rate_limited`، 503 + `Retry-After` · إبطال من جهاز آخر ⇒ refresh واحد ثم 303 + مسح الثلاث؛ مع `HX-Request` ⇒ 200 + `HX-Redirect` · **U2** logout بلا/بختم خاطئ ⇒ 403 والجلسة حيّة؛ صحيح ⇒ 303 + إبطال + 3 × `Max-Age=0`؛ ختمان من دورتي دخول **مختلفان**؛ ختم مستخدم آخر مرفوض · **U3** flash في جزئيتي today/thread؛ 403 htmx ⇒ `HX-Retarget: #flash` · **U4** `test_i18n_covers_all_enums` (severity/stage/objection/alert/delivery) + شريط الباقة `X / Y · Z / W` · `Cache-Control: public, max-age=86400, immutable` على vendor فقط. |
| انحرافات عن §2 (مع السبب) | **(1)** C-2 «`evidence_grade` ظاهر»: `RecommendationPublic` لا يحمل `evidence_grade`؛ الشارة تُشتق في `_grade_of` من `evidence_kinds_summary` بنفس ترتيب `share.evidence_grade` (D-074) + اختبار تطابق. **(2)** C-3 «`GET/PUT …/learning-consent`»: الـAPI `PATCH` فقط والقيمة تُقرأ من `GET /brands/{id}` ⇒ الواجهة PATCH. **(3)** C-3 «إضافة عضو بالبريد»: `GrantCreate` بـ`grantee_tenant_id` فقط (لا بحث بالبريد في الـAPI) ⇒ حقل UUID؛ `tenant_not_found` مترجَم. **(4)** C-3 واتساب «`quality_rating`/`daily_reply_cap`/`kill_switch`»: `WhatsAppAccountPublic` يحوي الرقم والحالة فقط؛ الأسقف تُدار عبر أدمن wa-caps (D-2) — الواجهة تعرض الرقم + `status` فقط. **(5)** C-3 `GET …/whatsapp/account` و`GET …/grants` و`GET /brain/cost` مقيَّدة بالدور ⇒ `_optional`/شرط `role == owner` يتجنّب 403 لـVIEWER بدل صفحة خطأ. **(6)** C-1 `publish` ليس إجراء مستخدم (SYSTEM_TRANSITIONS) ⇒ لا زر؛ `retry` ⇒ `publish/retry`؛ `publish_failed` يتيح `unschedule` + `archive` كما في `TRANSITIONS`. **(7)** C-0/C: إجراء مجهول (`/{action}`) ⇒ **بوابة الجلسة + CSRF أولاً** ثم 404 (لا تسريب). **(8)** D-0: التحقق المحلي `platform_admin_user_ids` قبل أي نداء ⇒ 404 صفحة؛ الراوتر يقرأ `get_settings()` مباشرة (lru) ⇒ اختبارات الأدمن تضبط `PLATFORM_ADMIN_USER_IDS`/`UMAMI_WEBSITE_ID` بـ`monkeypatch.setenv` + `cache_clear`. **(9)** D-1 الأخطاء: الكتالوج يعرّف أكواده في `catalog/service.py` (لا `exceptions.py`) ⇒ الاختبار المُعمَّم يمسح فئات `CatalogError` هناك (8 أكواد). **(10)** D-1 التوجيه: `<select multiple>` بترتيب الاختيار (لا سحب/إفلات) ⇒ `PUT /routing/{purpose}`. **(11)** D-4 «قطاع العلامة من `brand_profiles.sector`»: لا حقل `sector` في `BrandWithRoleOut`/`brand_profiles` ⇒ `?sector=` اختيار غير مُخزَّن (select) حتى SPRINT-13؛ النصائح 3 يومياً بتدوير `tm_yday`. **(12)** D-4 `GET /api/v1/playbooks` يعيد `today_tips` إضافة إلى الحقول. **(13)** `_optional` يبتلع 403/404 فقط (لا 5xx). |
| ما يحتاج قرار المالك | **(أ)** هل تُضاف `sector` إلى `brand_profiles` في SPRINT-13 (هجرة D-019) لتثبيت اختيار الـPlaybook؟ **(ب)** هل يُضاف `quality_rating` وقراءة الأسقف إلى `WhatsAppAccountPublic` (OWNER) أم تبقى أدمن فقط؟ **(ج)** منح الأعضاء بالبريد يحتاج مسار بحث مستخدم ⇒ `tenancy` (قرار منتج/خصوصية). **(د)** ضبط `PLATFORM_ADMIN_USER_IDS` و`UMAMI_WEBSITE_ID` في بيئة التشغيل. **(هـ)** 🔑 تدوير التوكن (ظهر ≥ 76 مرة). |

---

## 7. ما ينتظر المالك

1. 🔑 **تدوير التوكن** (ظهر ≥ 21 مرة في محادثة المخطط وحدها) + حماية `main`.
2. **بعد دمج 12.1-ب:** 3 علامات تجريبية (مطعم/كافيه ×2 + عيادة ×1) بحسابات Meta/WABA حقيقية لقياس TTFV ≤ 30 دقيقة + تصوير فيديوهات App Review (الرحلة: دخول ⇒ اليوم ⇒ صندوق الوارد ⇒ ردّ ⇒ تشخيص ⇒ مشاركة رابط).
3. ضبط `PLATFORM_ADMIN_USER_IDS` لحساب المالك فقط (يفعّل `/app/admin/*`).
4. اختيار: `UMAMI_WEBSITE_ID` (Umami Cloud أو self-host MIT) أو تركه فارغاً.
5. مراجعة `FEATURE_PULSE` / `FEATURE_LEAD_LOOP` / `PULSE_INTERVAL_SECONDS` / السقف 300 قبل التشغيل الحي.
