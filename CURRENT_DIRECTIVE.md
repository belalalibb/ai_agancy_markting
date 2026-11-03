# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-12 رُوجع مستقلاً على PostgreSQL 17.11 UTF8 و**قُبل ودُمج** (PR #45 ⇒ `main @ b98cffe`؛ التقرير [`sprints/SPRINT-12_REVIEW.md`](./sprints/SPRINT-12_REVIEW.md)). H1–H6 مُغلقة؛ **الحظر التشغيلي مرفوع**. هذا التوجيه = **SPRINT-12.1**: الواجهة L1 (ست شاشات Jinja2 + htmx + Pico، RTL) فوق الخلفية المكتملة + ما أُجِّل من SPRINT-12 (Batch Approval C-2، ملاحظة S-1) + Umami + Playbooks + تبويب AI للمشرف. **بوابة G6 تفرض تقسيماً صريحاً إلى نصفين (12.1-أ ثم 12.1-ب) — انظر البطاقة.**

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-12.1-L1-UI-AND-DEFERRED` |
| **النوع** | **12.1-أ (هذا الكوميتان A+B):** Part A إغلاقات صغيرة (S-1 + Batch Approval C-2) ⇒ Part B أساس الواجهة (قوالب أساسية + جلسة cookie + 3 شاشات: تسجيل الدخول/الاختيار، لوحة العلامة «اليوم»، صندوق واتساب) · **12.1-ب (كوميتان C+D بعد قبول 12.1-أ ودمجه):** Part C الشاشات الثلاث الباقية (المحتوى والموافقات، التشخيص والتوصيات، الإعدادات/الاتصالات) ⇒ Part D تبويب `/admin/ai` + `/admin/entitlements` + Umami + Playbooks JSON |
| **الحالة** | `ISSUED` — جاهز فور دمج هذا التوجيه في `main` (المخطط يدمجه — D-063) |
| **يبدأ من** | `main` @ **SHA دمج هذا التوجيه** (يسجَّل في `WORK_STATE.md` §6). أول أمر: `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` |
| **الفرع المستهدف** | `work/implementation` ⇒ **PR أول** بعنوان `SPRINT-12.1-A: S-1 + batch approval + L1 base (login, today, inbox)` ⇒ بعد دمجه **PR ثانٍ** `SPRINT-12.1-B: L1 content/diagnosis/settings + admin tabs + Umami + playbooks` |
| **الكوميتات** | **A** `fix(sprint-12.1): exclude dismissed from public card + batch approval (D-055)` ⇒ **B** `feat(sprint-12.1): L1 UI base — session cookie, layout, login/today/inbox (RTL)` ⇒ docs ⇒ **[مراجعة ودمج]** ⇒ **C** `feat(sprint-12.1): L1 content/approvals, diagnosis, settings screens` ⇒ **D** `feat(sprint-12.1): admin AI/entitlements tabs + Umami + playbooks seeds` ⇒ docs. كل كوميت يُدفع فور اكتماله. |
| **تبعيات جديدة (D-054 مُتحقَّق 2026-11-02)** | `jinja2>=3.1.6` (**BSD-3-Clause**، مثبَّت ضمنياً عبر starlette — يصبح صريحاً في `pyproject.toml`) · `python-multipart` (**Apache-2.0**؛ للنماذج HTML — تحقق أنه غير موجود أصلاً) · **ملفات ثابتة مُلزَمة في المستودع** (لا CDN، لا npm): `htmx.min.js` 2.x (**0BSD**) + `pico.min.css` 2.x (**MIT**) تحت `src/ui/static/vendor/` مع ملف `LICENSES.md` يذكر الإصدار وsha256. Umami **MIT** يُشغَّل خارجياً (خدمة منفصلة) — الكود يُضمّن `<script>` اختيارياً فقط عند `UMAMI_WEBSITE_ID` مضبوطاً. أضف الرخص الثلاث إلى `scripts/license_allowlist.txt` إن لزم. |
| **الحجم المتوقع (G6)** | **12.1-أ:** A ≈ 150 سطر `src/` · B ≈ 900 (قوالب HTML تُحتسب ضمن `src/ui/templates/`) ⇒ **≈ 1,050**. **12.1-ب:** C ≈ 800 · D ≈ 600 ⇒ **≈ 1,400**. **صمّام 12.1-أ:** إن تجاوز A+B معاً **1,400** سطر فعلياً، سلّم B بشاشتين (تسجيل الدخول + «اليوم») وانقل صندوق واتساب إلى C وسجّل. **صمّام 12.1-ب:** إن تجاوز C+D **1,600**، أجّل Umami + Playbooks إلى SPRINT-13 وسجّل. |
| **المسارات المحمية بعد 12.1-أ** | **`PROTECTED_ROUTE_COUNT == 84`** = 83 + **1** (`POST /brands/{id}/content/approvals/batch`) · العامة **7** بلا تغيير. **مسارات HTML للواجهة لا تُحتسب في العدّاد** لأنها تحت بادئة `/app` (ليست `/api/v1`) وتُحمى بجلسة cookie؛ عدّل حارس `tests/test_auth_guard.py` ليستثني `/app/*` **صراحةً** ويُثبت أن كل مسار `/app/*` (عدا `/app/login` و`/app/logout`) يعيد **303 ⇒ `/app/login`** بلا cookie (اختبار جديد `test_app_routes_redirect_without_session`). **12.1-ب:** لا مسارات API جديدة (84 يبقى) — تبويبا الأدمن يستهلكان `/api/v1/admin/*` القائمة. |
| **الهجرات** | **لا هجرة** في 12.1-أ ولا 12.1-ب (الخلفية مكتملة بـ0016). إن احتجت عموداً، توقّف وسجّل توصية D-019 في §6 بدل الإضافة. |
| **تاريخ الإصدار** | 2026-11-03 |

---

## 0. لماذا؟ (ما يجب أن يفهمه المنفذ قبل أي سطر)

1. **الخلفية جاهزة للعرض:** `lead_stage` + `pulse_alerts` + أسقف الرقم + `/d/{token}` + الكتالوج كلها على `main`. L1 **لا تُنشئ منطقاً جديداً** — كل شاشة تستدعي مسارات `/api/v1` القائمة من الخادم (server-side) أو عبر htmx. **ممنوع** أي استعلام SQL أو استيراد مستودع داخل `src/ui/` (اختبار عزل AST: `src/ui` يستورد `fastapi`, `jinja2`, `starlette`, `src.core.config`, `src.modules.auth.*` **فقط**؛ البيانات عبر `httpx.AsyncClient(app=…)` الداخلي أو عبر استدعاء دوال الخدمة من خلال منافذ `main.py` — اختر **الأول** لبساطة العزل وسجّل).
2. **TTFV ≤ 30 دقيقة** (POST_M7 §1-د) يُقاس في نهاية 12.1-ب على 3 علامات تجريبية: من تسجيل الدخول إلى أول توصية مقبولة أو أول ردّ واتساب مُرسَل. شاشة «اليوم» هي القلب: تنبيهات Pulse المفتوحة + محادثات تنتهي نافذتها + عناصر تنتظر الموافقة + استهلاك الباقة.
3. **S-1 (مراجعة SPRINT-12 §3):** البطاقة العامة تعرض التوصيات `dismissed` ومشاركتها تعيد 201. يُغلق **أولاً** في Part A لأنه يمس سطحاً عاماً.
4. **Batch Approval (C-2 المؤجَّل):** `POST /brands/{id}/content/approvals/batch` ≤ 50 عنصراً؛ **صف `approval_events` مستقل لكل عنصر** (D-055)؛ فحص الحصة `plan_posts` مرة لكل عنصر عبر `_ensure_post_quota` القائم؛ استجابة لكل عنصر `{item_id, status: approved|skipped, error?}` — **لا يفشل الكل بسبب عنصر** (207-style داخل 200)؛ 402/404 لعنصر تُسجَّل في `error` بالرمز. OWNER/MANAGER (نفس `approve_item`).
5. **الجلسة في الواجهة:** cookie `HttpOnly; Secure; SameSite=Lax` يحمل **refresh token القائم** (لا آلية جديدة)؛ `/app/login` يستدعي `POST /api/v1/auth/login` ثم يضبط الـcookie؛ كل طلب HTML يحصل على access token قصير عبر `/auth/refresh` القائم. **CSRF:** كل نموذج `POST` في `/app` يحمل حقل `csrf` = HMAC(`token_encryption_key`, session id) ويُتحقق منه. لا تخزين جديد.
6. **RTL/عربية أولاً:** `<html lang="ar" dir="rtl">`؛ النصوص من قاموس `src/ui/i18n/ar.json` (مفتاح ⇒ نص) — **لا نص عربي مُضمَّن في القوالب** (يسهّل الإنجليزية لاحقاً). الأخطاء تُعرض بالرموز المُترجمة (D-069): `entitlement_plan_inactive` ⇒ «الباقة موقوفة — جدّد للمتابعة» إلخ.

---

## 1. النطاق

### 1-أ. داخل النطاق
- **Part A:** S-1 (استبعاد `dismissed` من `build_public_share` + رفض مشاركتها 409 `recommendation_dismissed` **بلا** ErrorCategory جديد — خطأ HTTP محلي كما في `catalog_invalid_pricing`) · Batch Approval كما في §0-4 · اختبار PG لكل منهما.
- **Part B:** `src/ui/` (router `/app`, templates, static/vendor, i18n) · جلسة cookie + CSRF · الشاشات: `/app/login` (+ اختيار العلامة إن تعددت) · `/app/b/{brand_id}/today` · `/app/b/{brand_id}/inbox` (قائمة المحادثات + محادثة واحدة + ردّ نصي + اقتراح ردّ + تعيين `lead_stage`) · `REMOVABILITY.md` لـ`ui` + اختبار عزل AST.
- **Part C:** `/app/b/{brand_id}/content` (المسودات، توليد، موافقة فردية **وجماعية** عبر Batch، جدولة) · `/app/b/{brand_id}/diagnosis` (تشغيل التشخيص، قبول/رفض التوصيات، **مشاركة `/d/{token}` ونسخ الرابط وإلغاؤه**) · `/app/b/{brand_id}/settings` (الاتصالات Meta/WA، الباقة والاستهلاك، أعضاء العلامة).
- **Part D:** `/app/admin/ai` (مزودون/نماذج/توجيه/probe — يُعرض فقط لـ`PLATFORM_ADMIN_USER_IDS`) · `/app/admin/entitlements` (تفعيل/تمديد/تعليق + wa-caps) · Umami snippet اختياري · `playbooks/*.json` (3 بذور: «إطلاق منتج»، «موسم عروض»، «إعادة تنشيط العملاء») تُعرض في «اليوم» كاقتراحات **ثابتة** (لا نموذج) — تُقرأ من القرص عند الإقلاع.

### 1-ب. خارج النطاق (مؤجَّل صراحةً)
- أي React/Next/Tailwind build step؛ أي CDN؛ WebSocket/SSE (htmx polling كل 30s يكفي).
- **SPRINT-13:** تنبيه 800 رسالة خدمة/شهر، Day-2 Ops، الاحتفاظ D-081، تفريغ الصوت، `quality_rating` webhook، فيديوهات App Review (تُسجَّل على L1 بعد 12.1-ب).
- أي تغيير في `ErrorCategory` (12)، `contract.py`، `public_fetch.py`، D-075–D-082 (إلا عبر توصية D-019 في §6)، أي هجرة.

---

## 2. المواصفات (مقتضبة — الكود القائم هو المرجع §5-5)

| البند | المطلوب | أين |
|---|---|---|
| **A-1 (S-1)** | `public_diagnosis`: `list_recommendations(..., status=None)` ⇒ تصفية `status in ("proposed","accepted","materialized")` (في الذاكرة أو عبر المستودع — لا توسيع للبروتوكول إن أمكن)؛ `share_diagnosis` على توصية `dismissed` ⇒ **409** `recommendation_dismissed`؛ اختبار PG يعيد سيناريو المِجسّ P2/P3 | `brand_brain/share.py:160–180, 226–232` |
| **A-2 (Batch)** | `POST {BRAND_PREFIX}/content/approvals/batch` body `{item_ids: list[UUID] (1–50, unique)}` ⇒ `{results: [{item_id, status, error}], approved: n, skipped: m}`؛ كل عنصر يمرّ بـ`_authorise` مرة (خارج الحلقة) ثم `_ensure_post_quota` + `_act(approve)` داخل حلقة مع التقاط `ContentError`/`CounterQuotaExceededError`/`CounterPlanInactiveError` ⇒ `error` بالرمز؛ عنصر غير موجود ⇒ `item_not_found`؛ صف `approval_events` لكل عنصر ناجح؛ `counters.increment("content_batch_approved", n)` | `content/router.py:487–524` |
| **B-1 الجلسة** | `src/ui/session.py`: `set_session(resp, refresh_token)`, `current_app_user(request) -> CurrentUser | RedirectResponse(303 /app/login)`, `csrf_token(session_id)`/`verify_csrf` | — |
| **B-2 القالب الأساسي** | `base.html`: Pico + htmx محليان، شريط علوي (العلامة الحالية، التبديل، خروج)، تنقّل جانبي (اليوم/الصندوق/المحتوى/التشخيص/الإعدادات/[أدمن])، منطقة `hx-target="#main"`، شريط «الباقة: X/Y منشورات · Z/W محادثات» من `/api/v1/brands/{id}/entitlements/usage` القائم (أو ما يقابله — تحقق) | `src/ui/templates/` |
| **B-3 اليوم** | 4 بطاقات: تنبيهات Pulse المفتوحة (`GET …/pulse/alerts?open=true` + زر ack) · محادثات تنتهي ≤ 4h · عناصر `pending_approval` · اقتراحات Playbooks (12.1-ب) ؛ polling 30s | — |
| **B-4 الصندوق** | قائمة المحادثات (مرتبة بآخر وارد؛ شارة `lead_stage`) ⇒ تفاصيل: الرسائل + نموذج ردّ (`POST …/messages`) + «اقترح ردّاً» (`POST …/suggest`) + قائمة `lead_stage` (`PATCH …/lead-stage`) ؛ أخطاء 402/423/429 تُعرض بالرموز المُترجمة | `whatsapp/router.py` |
| **C-1/2/3** | كما في §1-أ؛ شاشة التشخيص تعرض درجة الأدلة لكل توصية (`evidence_kinds_summary`) وزر مشاركة يُظهر الرابط **مرة واحدة** (كما يعيده API) | `brand_brain/router.py`, `share.py` |
| **D-1 الأدمن** | يظهر الرابط في التنقّل فقط إن `actor.user_id in settings.platform_admin_user_ids`؛ الشاشتان تستهلكان `/api/v1/admin/ai/*` و`/api/v1/admin/entitlements/*`؛ **لا** عرض لأي سر (API يعيد `has_credential` فقط) | `ai_gateway/catalog/router.py`, `entitlements/router.py` |
| **D-2 Umami** | `UMAMI_SCRIPT_URL` + `UMAMI_WEBSITE_ID` اختياريان في `Settings`؛ عند ضبطهما يُحقن `<script defer data-website-id=…>` في `base.html`؛ لا PII في الأحداث (أسماء الصفحات فقط) | `core/config.py` |
| **D-3 Playbooks** | `src/ui/playbooks/*.json` ⇒ `{id, title_key, steps: [{kind: content|whatsapp|diagnosis, action_key}]}`؛ تُحمَّل مرة عند الإقلاع؛ تُعرض في «اليوم» | — |

---

## 3. معايير القبول (المخطط يتحقق منها حرفياً على PG 17 UTF8 ببيئة مُعاد بناؤها)

**12.1-أ:**
1. توصية `dismissed` **لا تظهر** في `/d/{token}` ومشاركتها ⇒ 409 (اختبار PG).
2. Batch: 50 عنصراً مختلطاً (موجود/غير موجود/فوق الحصة) ⇒ 200 مع نتائج لكل عنصر؛ `approval_events` = عدد الناجحين **فقط**؛ 51 عنصراً ⇒ 422؛ VIEWER ⇒ 403؛ `plan_inactive` ⇒ كل عنصر `error=entitlement_plan_inactive` (لا 402 للطلب كله).
3. `/app/*` بلا cookie ⇒ 303 إلى `/app/login` (كل مسار؛ اختبار يعدّها من `app.routes`)؛ cookie منتهٍ ⇒ 303 أيضاً.
4. تسجيل دخول صحيح ⇒ cookie `HttpOnly; Secure; SameSite=Lax` ⇒ `/app/b/{id}/today` 200 مع `dir="rtl"` و4 بطاقات؛ علامة بلا منحة ⇒ 404.
5. الصندوق: ردّ على محادثة ⇒ 201 عبر الواجهة (Graph مُحاكى)؛ 423 kill switch ⇒ رسالة مترجمة لا 500؛ تعيين `lead_stage` يظهر فوراً (htmx swap).
6. نموذج `POST` بلا `csrf` صحيح ⇒ 403.
7. عزل: `src/ui` لا يستورد أي `src.modules.*.infrastructure` ولا `sqlalchemy` (اختبار AST) + `REMOVABILITY.md`.
8. `PROTECTED_ROUTE_COUNT == 84`؛ العامة 7؛ `ErrorCategory` 12؛ greps الثمانية 0 + جديد: `grep -rn "sqlalchemy\|infrastructure" src/ui --include=*.py` = 0.
9. `pip install .` في venv نظيف ⇒ `scripts/check_licenses.py` أخضر مع jinja2/python-multipart؛ `src/ui/static/vendor/LICENSES.md` يطابق sha256 الملفات.
10. البوابات: ruff 0/0 · وحدات بلا DB ≥ 95.00% · تكامل PG 0 فشل · CI 5/5.

**12.1-ب (تُفصَّل في توجيه قصير بعد دمج 12.1-أ):** الشاشات الثلاث + أدمن + Umami اختياري + Playbooks؛ TTFV ≤ 30 دقيقة على 3 علامات تجريبية (يقيسه المالك ويسجّله).

---

## 4. أوامر

```bash
# 0) البداية
git checkout work/implementation && git fetch origin && git merge --ff-only origin/main

# 1) بيئة PG 17 UTF8 — كما في SPRINT-12 §4 (initdb -E UTF8 --locale=C.UTF-8 … CREATE EXTENSION vector) ثم:
pip install -e ".[dev]"

# 2) بعد كل Part
ruff check src tests alembic scripts && ruff format --check src tests alembic scripts
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_growth_os_test pytest -m integration -q
# بوابة الوحدات بلا DB — إن قُتلت OOM (ساندبوكس 1 GB) شغّلها على نصفين بـ--cov-append ثم coverage report --fail-under=95 (انظر sprints/SPRINT-12_REVIEW.md §5)
env -u TEST_DATABASE_URL pytest -m "not integration" -q --cov=src --cov-fail-under=95 --cov-precision=2

# 3) greps الملزمة (0 نتائج) — الثمانية من SPRINT-12 §4-3 كما هي + :
grep -rn "sqlalchemy\|\.infrastructure" src/ui --include=*.py                 # 0 — الواجهة لا تلمس البيانات مباشرة
grep -rn "cdn\.\|unpkg\|jsdelivr\|googleapis" src/ui                             # 0 — لا CDN
grep -rn "[\u0600-\u06FF]" src/ui/templates                                       # 0 — لا عربية مضمَّنة (كلها في i18n/ar.json)
sha256sum src/ui/static/vendor/*.{js,css} && cat src/ui/static/vendor/LICENSES.md # تطابق
git diff origin/main -- src/modules/ai_gateway/contract.py src/modules/brand_brain/public_fetch.py alembic/ | wc -l   # 0

# 4) الدفع بعد كل كوميت
git push origin work/implementation
```

---

## 5. التزامات المنفذ

1. **لا دمج** إلى `main` (D-063). PR أول لـ12.1-أ؛ **توقّف** بعده حتى مراجعة المخطط ودمجه؛ ثم PR ثانٍ لـ12.1-ب.
2. **لا** `ErrorCategory` جديد؛ **لا** هجرة؛ **لا** `# pragma: no cover` جديد؛ **لا** تبعية غير المذكورة في البطاقة؛ **لا** CDN.
3. **Part A أولاً ويُدفع وحده** (S-1 سطح عام).
4. **ادفع بعد كل كوميت** وحدّث §6 في كوميت docs لكل PR.
5. إن تعارض هذا التوجيه مع الكود الفعلي، **الكود الفعلي هو المرجع**: نفّذ المقصود وسجّل الفرق في §6.
6. **صمّاما الحجم** في البطاقة ملزمان؛ تجاوزهما بلا تسجيل = رفض.
7. لا نص خام من العميل يُعاد في أي قالب دون `autoescape` (Jinja2 افتراضياً للـ`.html` — **تحقق أنه مفعَّل** واكتب اختباراً يحقن `<script>` في اسم محادثة ويؤكد الهروب).

---

## 6. تقرير المنفذ (يملؤه المنفذ)

> **12.1-أ فقط** (A ⇒ B ⇒ docs). 12.1-ب (C + D) تنتظر التوجيه القصير بعد المراجعة.

| البند | القيمة |
|---|---|
| SHA الكوميتات | على `work/implementation` فوق `main` @ `08e1258` (ff من `b266b95`): **A** `85b9fcd` `fix(sprint-12.1): exclude dismissed from public card + batch approval (D-055)` — دُفع وحده · `ff7f893` `test(sprint-12.1): content route inventory …` (اختبار فقط — جرد مسارات content كان يُحصي 13/14 حرفياً) · **B** `a0d3d10` `feat(sprint-12.1): L1 UI base — session cookie, layout, login/today/inbox (RTL)` · `e1ae039` `fix(sprint-12.1): UI keeps D-068 No-Bypass (httpx only in core/http) + session check precedes form validation` (إصلاح فشلَي CI unit على `a0d3d10`) · **docs** (هذا الكوميت). |
| نتائج `pytest` (العدد/التغطية) لكل كوميت — كامل **و** بلا DB | **CI (GitHub, PG 17 خدمة):** `85b9fcd` 4/5 (unit ❌ جرد content فقط) ⇒ `ff7f893` **5/5** · `a0d3d10` 4/5 (unit ❌: `test_b0_u1_no_bypass_imports` بسبب `import httpx` في `src/ui/api.py` + `test_app_routes_redirect_without_session` لأن `POST …/reply` بلا جسم أعاد 422 قبل فحص الجلسة) ⇒ `e1ae039` **5/5** — بلا DB **1,472 ناجحاً / 0 فشل / 1 متخطى · 95.25%** (`--cov-fail-under=95`)؛ تكامل ✅. **محلياً PG 17 UTF8+vector (5433، بيئة مُعاد بناؤها بعد كل إعادة ضبط):** تكامل **142/0** على `a0d3d10` و`e1ae039` (140 من SPRINT-12 + 2 جديدان: `test_sprint12_1_a_pg.py` S-1 + batch 50 مختلطاً) · وحدات الواجهة `tests/ui` + `tests/isolation/test_ui_removable.py` **39/39** · A-1 12/12 · A-2 5/5 · حارس المسارات + `/app/*` ✅. **بلا DB محلياً:** الساندبوكس 985 MB يقتل المجموعة الكاملة مع التغطية حتى على نصفين (OOM عند ~930 MB) ⇒ رقم التغطية المعتمد هو رقم CI (95.25%). |
| `PROTECTED_ROUTE_COUNT` / العامة / عدد مسارات `/app/*` | **84** (83 + `POST /brands/{id}/content/approvals/batch`) / **7** (بلا تغيير) / **11** مساراً `/app/*` (`GET+POST /login`, `POST /logout`, `GET /`, `GET /b/{id}/today`, `POST …/today/alerts/{alert_id}/ack`, `POST …/today/items/{item_id}/approve`, `GET /b/{id}/inbox`, `POST …/inbox/{cid}/reply`, `…/suggest`, `…/stage`) — **8** منها محروسة بالجلسة (303 ⇒ `/app/login` بلا cookie أو بـcookie منتهٍ/تالف — مُثبَت في `tests/ui/test_ui_session.py` **و** `tests/test_auth_guard.py::test_app_routes_redirect_without_session`)؛ `include_in_schema=False` + استثناء صريح لـ`/app` في `_protected_operations()`. `ErrorCategory` 12 (بلا تغيير). |
| أسطر `src/` المضافة لكل Part (خام/فعلي) + حالة الصمّام | **A:** 156 مضافاً / 8 محذوفاً (`share.py` 42 · `content/schemas.py` 36 · `content/router.py` 78) ≈ التقدير 150. **B (+إصلاح CI):** **729** سطر Python فعلي (`src/ui/*.py` 694: `router.py` 497 · `session.py` 103 · `api.py` 86 · `__init__.py` 8 + `main.py` 8 + `core/http.py` 27) + 356 سطراً غير Python (8 قوالب 190 · `i18n/ar.json` 104 · `REMOVABILITY.md` 31 · `LICENSES.md` 18) + أصلان مُلزَمان (`htmx.min.js` 50,917 B · `pico.min.css` 82,194 B — ليسا كوداً مكتوباً). **A + B Python = 885 ≤ 1,400 ⇒ الصمّام لم يُفعَّل؛ inbox بقي في B.** حتى مع احتساب القوالب/i18n/md (1,241) يبقى تحت السقف. اختبارات: A +490 سطراً (11 وحدة + 2 تكامل)، B +686 سطراً (39 وحدة + حارس). |
| نتائج grep الملزمة (§4-3) + sha256 vendor | الثمانية من SPRINT-12 §4-3 = **0** (بلا تغيير في نطاقها) · `grep -rn "sqlalchemy\|\.infrastructure" src/ui --include=*.py` = **0** · `grep -rn "cdn\.\|unpkg\|jsdelivr\|googleapis" src/ui` = **0** (الـURLs الوحيدة في `LICENSES.md` كمصادر موثّقة فقط؛ لا قالب ولا Python) · عربية في `src/ui/templates` = **0** (كلها في `i18n/ar.json`؛ اختبار `test_templates_have_no_cdn_and_no_arabic_literals`) · `import httpx` في `src/ui` = **0** (بعد `e1ae039`) · `# pragma: no cover` في `src/` = 20 (بلا تغيير) · `git diff origin/main -- contract.py public_fetch.py alembic/` = **0** · ملفات ممنوعة (`ARCHITECTURE_LOCK`, `SESSION_RECOVERY`, `imaster_planner`, `README`, `Hub/**`, `sprints/**`) = **0** فرق · `wip/*` = 0. **sha256 vendor** (مطابق لـ`LICENSES.md`، مُختبَر في `test_vendor_sha256_matches_licenses_md`): `htmx.min.js` 2.0.4 0BSD `e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447` · `pico.min.css` 2.0.6 MIT `dd5fd5591afd81ee21dcc117ad85c014dc3f1f19dc2d7b7d101ea0acc29274c2`. `scripts/check_licenses.py`: Jinja2 3.1.6 BSD ✅ · MarkupSafe 3.0.3 BSD-3 ✅ · python-multipart 0.0.33 Apache-2.0 ✅ (CI licenses ✅). |
| انحرافات عن §2 (مع السبب) | **(1) CSRF مربوط بـ`user_id` لا `sid`:** §2 يقول `HMAC(token_encryption_key, session id)`، لكن وحدة auth الفعلية تُدوِّر `sid` عند **كل** `/auth/refresh` (كشف إعادة الاستخدام I-A2) — والواجهة تستدعي refresh في كل طلب `/app/*`، فختم مربوط بـsid لن يطابق أبداً الطلب الذي يقدّمه. الختم = `HMAC(token_encryption_key ∥ fallback auth_jwt_secret, user_id)[:32]` بمقارنة زمن ثابت؛ اختبار `test_csrf_stamp_survives_refresh_rotation`. **(2) cookie الجلسة نسخة من قيمة refresh بمسار `/app` و`SameSite=lax`:** cookie auth الأصلية محصورة بـ`Path=/api/v1/auth` و`strict`، فالمتصفح لا يرسلها لـ`/app/*`؛ الواجهة تنسخ القيمة ذاتها إلى `app_session` (`HttpOnly`، `Secure` + بادئة `__Secure-` عند `auth_cookie_secure`، `Max-Age=auth_refresh_ttl_seconds`)، وتستبدلها بالقيمة المُدوَّرة في كل استجابة (بما فيها 403 CSRF)؛ `lax` لأن روابط `GET` الواردة من البريد/واتساب إلى `/app/...` يجب أن تعمل — الكتابة محمية بالختم. **(3) `httpx` داخل `src/ui` — مسحوب:** `a0d3d10` استورد `httpx` مع `# noqa: TID251`؛ حارس `test_b0_u1_no_bypass_imports` رفضه في CI، فنُقل النقل الداخلي إلى `src/core/http.py::build_in_process_client()` (الوحدة المسموح لها وحدها) في `e1ae039`؛ `src/ui` لا يستورد `httpx` إطلاقاً، واختبار العزل يمنع ذلك صراحة. **(4) حقول النماذج اختيارية:** FastAPI يُحقّق `Form()` قبل الدخول إلى الدالة ⇒ `POST /reply` بلا جسم أعاد 422 لمستخدم مجهول؛ جُعلت الحقول اختيارية بافتراض فارغ حتى يسبق فحص الجلسة (303) وCSRF (403) أي تحقق؛ الجسم الفارغ يصل إلى API ويعود 422 كـflash مترجَم للمستخدم المصادق. **(5) `pyproject.toml` عُدِّل** (استثناء تصريح البطاقة): `jinja2>=3.1.6` + `python-multipart>=0.0.18` فقط. **(6) قيم `LeadStage`/`ObjectionCode` الفعلية:** القوائم في الواجهة = `new/qualified/quoted/won/lost/not_lead` و`price/timing/trust/competitor/no_response/other` (الكود هو المرجع). **(7) S-1 ⇒ 409 `recommendation_dismissed` لا 404:** مشاركة توصية `dismissed` تُرفَض بـ409 (تمييز عن غير الموجود 404)؛ الرابط العام لتوصية صارت `dismissed` بعد مشاركتها ⇒ 404 والأشقاء غير العامين يُحذفون من البطاقة (`PUBLIC_STATUSES = {proposed, accepted, materialized}`). **(8) Batch ⇒ فحص الخطة يسبق دورة الحياة** (كما في المسار المفرد): بعد بلوغ سقف `monthly_posts` تُبلَّغ العناصر المفقودة أيضاً `entitlement_quota_exceeded` بدل `content_not_found`. **(9) 423 kill-switch في الواجهة ⇒ 200 + flash مترجَم** (`error.wa_number_disabled`)، ليس تمرير 423؛ أخطاء API الأخرى ⇒ flash؛ أخطاء التحميل ⇒ صفحة خطأ 403/404 كما هي وإلا 502. **(10) `test_auth_guard.py` كاملاً بطيء محلياً** (168 حالة × بناء التطبيق) — نُفِّذ كاملاً في CI؛ محلياً نُفِّذ اختبار `/app/*` + العدّاد. **(11) إعادات ضبط الساندبوكس ×4 خلال 12.1-أ** مسحت `src/ui` غير المُلتزَم مرتين (أُعيد بناؤه حرفياً) — لذلك دُفع B فور اكتمال اختباراته المحلية ثم أُصلح من CI. **(12) `concurrency: cancel-in-progress`:** دفعُ `ff7f893` ألغى تشغيل `85b9fcd` قبل اكتمال unit — النتيجة النهائية لكل شجرة مُثبَتة على الكوميت التالي. |
| ما يحتاج قرار المالك | **(أ)** 🔑 تدوير `ghp_…` — ظهر ≥ **66** مرة (يُرسَل في كل دور) + حماية `main`. **(ب)** قبول انحراف CSRF (`user_id` بدل `sid`) أو طلب بديل (ختم مُخزَّن في الجلسة = يتطلب عموداً ⇒ D-019 ⇒ ليس في 12.1). **(ج)** `SameSite=lax` لـ`app_session` (مقابل `strict` في auth) — مقبول؟ **(د)** `/app/static/vendor/*` يُقدَّم عبر `StaticFiles` بلا `Cache-Control` مخصص وبلا SRI — يكفي لـL1؟ **(هـ)** بدء 12.1-ب (C: content/diagnosis/settings + D: admin AI/entitlements + Umami + Playbooks) يحتاج التوجيه القصير الموعود. |

---

## 7. ما ينتظر المالك

1. 🔑 **تدوير التوكن** (`ghp_…` ظهر ≥ 18 مرة في جلسة المخطط وحدها) + حماية فرع `main`.
2. **الحظر التشغيلي مرفوع** (SPRINT-12 دُمج). قبل الربط الحي راجع: `FEATURE_PULSE`/`FEATURE_LEAD_LOOP` (افتراضهما `true`)، `PULSE_INTERVAL_SECONDS=900`، سقف D-082 = 300.
3. تجهيز **3 علامات تجريبية** + نشر Umami (اختياري) لقياس TTFV في نهاية 12.1-ب.
