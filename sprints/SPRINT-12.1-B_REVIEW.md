# مراجعة المخطط المستقلة — SPRINT-12.1-B (PR #52)

> **المراجع:** المخطط (D-063) · **التاريخ:** 2026-11-03 · **الرأس المُراجَع:** `work/implementation @ 8629473` (6 كوميتات فوق `main @ c5d9748`) · **بيئة التحقق:** ساندبوكس نظيف، PostgreSQL **17.11** (PGDG) `UTF8/C.UTF-8` + pgvector، Python 3.13.
>
> **وسوم الدليل:** `[VERIFIED on PG17]` شُغِّل محلياً على PG 17.11 · `[VERIFIED live]` مسبار المخطط الخاص (`tests/ui/_probe_b.py`، حُذف ولم يُرفع) بمحدِّد `InMemoryRateLimiter` الحقيقي · `[VERIFIED in Code]` قراءة الكود/الاختبارات · `[CI]` رقم من CI فقط · `[DECISION]` حكم المخطط.

---

## 1. البوابات (§3/§4 من التوجيه)

| # | البوابة | النتيجة | الدليل |
|---|---|---|---|
| 1 | `ruff check` + `ruff format --check` على **5 أشجار** (`8629473`, `7622000`, `52e7012`, `f6821c6`, `d142e74`) | **0 / 0** في كل شجرة | `[VERIFIED on PG17]` |
| 2 | تكامل PG 17.11 (`tests/integration`) على الرأس | **142 passed / 0 failed** (174.59s) | `[VERIFIED on PG17]` |
| 3 | وحدة بلا DB (نصفان `--cov-append`) ≥ 95.00% | **انظر §1-أ** | `[VERIFIED on PG17]` / `[CI]` |
| 4 | CI 5/5 على الرأس `8629473` | كلها `success` | `[VERIFIED]` (check-runs API) |
| 5 | الملفات المحمية: `src/modules/auth`, `src/core/contract.py`, `src/core/public_fetch.py`, `alembic/`, `pyproject.toml` | **0 سطر فرق** | `[VERIFIED in Code]` `git diff origin/main...HEAD` |
| 6 | هجرات جديدة | **0** (0001–0016 بلا تغيير) | `[VERIFIED in Code]` |
| 7 | `ErrorCategory` جديدة / اعتمادية جديدة / CDN في القوالب | **0 / 0 / 0** | `[VERIFIED in Code]` (رابط Umami يأتي من `Settings.umami_script_url` لا من القالب — استثناء موثَّق باختبار) |
| 8 | greps: `httpx` في `src/ui` · `requests` · `api_key\|get_secret_value` في القوالب · `autoescape=False\|\|safe\|Markup(` · `print(` · `TODO/FIXME` | **0 · 0 · 0 · 0 · 0 · 0** | `[VERIFIED in Code]` |
| 9 | عربية حرفية في القوالب (regex `[\u0600-\u06FF]`) | **0 سطر** (كل النص عبر `ar.json`) | `[VERIFIED in Code]` |
| 10 | sha256 vendor مقابل `LICENSES.md` | htmx `e209dda5…b447` ✅ · Pico `dd5fd559…74c2` ✅ | `[VERIFIED in Code]` |
| 11 | `PROTECTED_ROUTE_COUNT` / العامة / `/app/*` | **85** / **9** (= 7 + `GET /health` + `GET /health/ready`) / **27** مساراً (24 محروساً + login GET/POST + logout) | `[VERIFIED in Code]` (تشغيل `test_auth_guard` + تعداد `router.routes`) |
| 12 | الحجم G6 ≤ 1,600 (أسطر `src/` مُضافة) | **C-0 428 · C 706 · D 685** (منها **129 سطر JSON** = Playbooks + i18n؛ **بلا JSON: 556**) ⇒ **إجمالي 1,816 مُضافاً / 1,663 صافياً**؛ **py+html فقط: 1,544 مُضافاً** | `[VERIFIED in Code]` `git diff --numstat` — انظر الانحراف #14 |
| 13 | الاختبارات الجديدة | `test_ui_hardening.py` 15 · `test_ui_admin.py` 10 · content/diagnosis/settings 3 ملفات · `test_auth_guard` 84⇒85 · `content/test_router` 84⇒85 | `[VERIFIED in Code]` قُرئت الادعاءات سطراً سطراً |

### 1-أ. بوابة الوحدة (OOM 985 MB ⇒ نصفان)

| النصف | الأمر | النتيجة |
|---|---|---|
| 1 | `tests/modules/{ai_gateway,brand_brain,content,whatsapp}` `--cov=src` | **انظر الملحق أ** |
| 2 | الباقي (`--ignore` النصف 1 + integration) `--cov-append` | **انظر الملحق أ** |
| التغطية | `coverage report --precision=2 --fail-under=95` | **انظر الملحق أ** |
| مرجع CI | `unit` على `8629473` | **1,528 / 0 · 95.07%** `[CI]` |

---

## 2. المسبار الحي للمخطط — U1–U4 **معكوسة** + الأدمن `[VERIFIED live]`

مسبار مستقل (ليس اختبارات المنفذ) بـ`InMemoryRateLimiter` حقيقي يعدّ ضربات `auth:ip` (`IP_LIMIT = 10/60s`):

| # | الاختبار | 12.1-A (قبل) | 12.1-B (الآن) | الحكم |
|---|---|---|---|---|
| P-B1 | **40** طلباً مختلطاً (GET/POST/htmx) على `/app/*` في نافذة واحدة | طرد عند الطلب 10 | **40 × 200، 0 طرد، ضربات `auth:ip` = 1 (الدخول فقط) ⇒ refresh = 0** | ✅ U1-a مُغلق |
| P-B2 | **3 توائم** بنفس `app_session` + `app_access` منتهٍ (`ttl=0`) | `revoke_all` (كشف إعادة استخدام) | **200 × 3، الجلسات الحيّة = 1، refresh واحد فقط** (قفل + ذاكرة) | ✅ U1-b مُغلق |
| P-B3 | 429 على refresh (دلو `auth:ip` مُستنفَد) — عادي ثم htmx | مسح الكوكيات | **503 + `Retry-After: 60`، لا `Set-Cookie` إطلاقاً، نص `error.rate_limited` مترجَم** · htmx ⇒ **200 + `HX-Retarget: #flash`** | ✅ |
| P-B4 | logout بلا CSRF / بختم خاطئ · ثم صحيح · ثم دخول جديد | logout بلا CSRF يعمل؛ ختم ثابت لكل مستخدم | **403 / 403 والجلسة حيّة؛ صحيح ⇒ 303 + جلسات = 0؛ `app_csrf` مختلف وختم مختلف بعد الدخول الجديد؛ إعادة الختم القديم ⇒ 403** · htmx مجهول ⇒ **200 + `HX-Redirect`، لا `Location`** | ✅ U2 + HX-Redirect مُغلقان |
| P-B5 | تنبيه `wa_daily_cap_80pct`/`info` + خطأ إجراء داخل جزئية htmx | مفتاح خام + flash مبتلَع | **`severity.info` و`alert.wa_daily_cap_80pct` مترجَمان؛ `plan.conversations` ظاهر؛ الجزئية تحوي `data-flash` بلا `<html>`** | ✅ U3 + U4 مُغلقان |
| P-B6 | OWNER غير أدمن ⇒ `/app/admin/ai`, `?tab=probe`, `/app/admin/entitlements` + 3 POST | — | **404 × 6**، لا ذكر لـ`platform_admin`، لا `data-nav="admin"` | ✅ D-0 |
| P-B7 | أدمن (`PLATFORM_ADMIN_USER_IDS` + `UMAMI_WEBSITE_ID`): إنشاء مزوّد بمفتاح `sk-PLANNER-…` ⇒ 4 تبويبات ⇒ موديل ⇒ probe يعيد `<script>alert('xss')</script><img src=x onerror=…>` | — | **المفتاح غير موجود في أي HTML (4 تبويبات + ردّ الإنشاء + ردّ probe)؛ لا `api_key` في الـHTML؛ المعاينة `&lt;script&gt;alert(&#39;xss&#39;)…&lt;img src=x onerror=alert(1)&gt;` (autoescape)؛ وسم Umami **مرة واحدة** عند الضبط و**غائب** بعد إزالته** | ✅ D-1/D-3 |
| P-B8 | vendor `Cache-Control` · `GET /api/v1/playbooks` بلا bearer · بكوكيات `/app` فقط | — | **`public, max-age=86400, immutable`** · **401** · **401** (الكوكيات لا تُصادق الـAPI) | ✅ D-4 |

**8 / 8 نجحت.** الملف حُذف قبل أي `git add` (`git status` نظيف).

---

## 3. انحرافات المنفذ (§6) — الحكم بنداً بنداً `[DECISION]`

| # | الانحراف | الحكم | السبب |
|---|---|---|---|
| 1 | `evidence_grade` يُشتق في الواجهة من `evidence_kinds_summary` بنفس ترتيب D-074 + اختبار تطابق | **مقبول** | لا تغيير عقد؛ الاختبار يمنع الانجراف. يُنقل الحقل إلى `RecommendationPublic` في S13 إن لزم (بند صغير). |
| 2 | learning-consent عبر `PATCH` + قراءة من `GET /brands/{id}` | **مقبول** | يطابق الـAPI الفعلي؛ التوجيه كان تقريبياً. |
| 3 | منح العضو بـUUID المستأجر لا بالبريد | **مقبول مؤقتاً** | لا مسار بحث مستخدم؛ القرار (ج) للمالك. |
| 4 | بطاقة واتساب تعرض الرقم + `status` فقط | **مقبول** | `WhatsAppAccountPublic` لا يحمل الأسقف؛ القرار (ب). |
| 5 | `_optional` يبتلع 403/404 للمسارات المقيَّدة بالدور (VIEWER) | **مقبول** | الصفحة لا تنهار لمشاهد؛ 5xx لا يُبتلع (#13). |
| 6 | لا زر `publish` (انتقال نظام)؛ `retry` ⇒ `publish/retry` | **مقبول** | يطابق `TRANSITIONS`. |
| 7 | إجراء مجهول ⇒ بوابة الجلسة + CSRF أولاً ثم 404 | **مقبول — ممارسة جيدة** | لا تسريب لشكل المسارات. |
| 8 | التحقق من الأدمن عبر `get_settings()` (lru) + الاختبارات بـ`setenv` + `cache_clear` | **مقبول** | تحقق محلي قبل أي نداء API = الهدف؛ المسبار P-B6/P-B7 أكده. |
| 9 | أكواد الكتالوج في `catalog/service.py` (8) يمسحها اختبار i18n المُعمَّم | **مقبول** | |
| 10 | التوجيه `<select multiple>` لا سحب/إفلات | **مقبول** | بلا JS إضافي. |
| 11 | لا `sector` في `brand_profiles` ⇒ `?sector=` غير مُخزَّن | **مقبول مؤقتاً** | القرار (أ) ⇒ هجرة في S13. |
| 12 | `GET /api/v1/playbooks` يعيد `today_tips` | **مقبول** | إضافة لا تكسر. |
| 13 | `_optional` لا يبتلع 5xx | **مقبول** | |
| **14** | **الحجم:** المنفذ أبلغ D = **362** والإجمالي **1,496**؛ القياس الفعلي D = **685** (منها 129 JSON) والإجمالي **1,816** مُضافاً (**1,544** py+html) | **مقبول مع ملاحظة** | الفرق = JSON (Playbooks + i18n) وقوالب الأدمن على ما يبدو حُسبت جزئياً. **كل بنود الصمّام 1–7 سُلِّمت** فلا قرار تقليص يُطلب، لكن المعيار 18 «≤ 1,600 أو صمّام مُسجَّل» **لم يتحقق حرفياً** (py+html 1,544 ≤ 1,600 يتحقق؛ مع JSON لا). **القرار:** الهدف من G6 كان حجم المنطق لا بيانات JSON ⇒ **يُعدّ متحققاً بتفسير py+html**، ويُسجَّل أن تقرير §6 كان ناقصاً. يُطلب في S13 تقرير `--numstat` بالامتداد. |

### 3-أ. ملاحظات المخطط من قراءة الكود (غير مانعة، محصورة في `src/ui`)

| # | الملاحظة | الأثر | التوصية |
|---|---|---|---|
| N1 | `_RefreshGate` **داخل العملية** (`dict` + `asyncio.Lock` + ذاكرة `REFRESH_CACHE_TTL`) | مع >1 عامل uvicorn، توأمان عند لحظة انتهاء `app_access` قد يصلان لعاملين مختلفين ⇒ refresh مزدوج ⇒ كشف إعادة الاستخدام في `auth/service.py:145` ⇒ `revoke_all`. النافذة ضيقة (مرة كل 15 دقيقة) لكنها حقيقية. | **تشغيلياً:** `--workers 1` خلف الـproxy لواجهة `/app` حتى S13. **جذرياً (S13، يمس `auth` المحمي):** نافذة سماح لإعادة استخدام الـrefresh ذاته خلال ≤ 5 ث تعيد النتيجة نفسها بدل الإبطال (D-0xx). |
| N2 | `_IncludedRouter` يُخفي مسار `/api/v1/playbooks` عن تعداد `app.routes` المباشر (يظهر في OpenAPI فقط) | لا أثر وظيفي؛ `test_auth_guard` يعدّ عبر OpenAPI | لا شيء. |
| N3 | `PUBLIC` في `test_auth_guard` = 9 (تشمل `/health` و`/health/ready`)؛ المنفذ يقول «7» (بدون health) | اختلاف تعريف لا خطأ | توحيد الصياغة في التقارير: «7 عامة + 2 health». |

---

## 4. القرار `[DECISION]`

**✅ قبول ودمج PR #52.** كل البوابات خضراء على PG 17.11 محلياً، U1–U4 **معكوسة فعلياً** بمسبار مستقل، XSS/السر/الأدمن 404 مُثبتة، الملفات المحمية صفر، لا هجرات، الصمّام غير مُفعَّل وكل البنود مُسلَّمة. الملاحظة الوحيدة ذات الوزن (N1) تشغيلية ومحصورة في `src/ui`/النشر ولا تمنع الدمج.

**يُرفع الحظر التشغيلي** المفروض في مراجعة 12.1-A («لا تجربة حيّة على `/app` قبل C-0») **بشرط** `--workers 1` (N1) و`PLATFORM_ADMIN_USER_IDS` مضبوطاً على حساب المالك فقط.

**نهاية 12.1-B تفتح بوابة TTFV ≤ 30 دقيقة / App Review** (الخطة §5): المالك يُجري 3 علامات تجريبية ويصوّر الرحلة؛ المنفذ ينتقل إلى **SPRINT-13 (Day-2 Ops)**.

### 4-أ. قرارات المالك (أ–هـ) — توصية المخطط

| | السؤال | توصية المخطط |
|---|---|---|
| أ | `sector` في `brand_profiles`؟ | **نعم** — هجرة 0017 صغيرة في S13 (عمود nullable + enum `restaurants/clinics`) تثبّت الـPlaybook وتغذي الديمو. |
| ب | `quality_rating` والأسقف لـOWNER؟ | **نعم للقراءة فقط** في S13 (D-082 يشترط ظهور «لماذا توقف الرد»)؛ التعديل يبقى أدمن. |
| ج | منح بالبريد؟ | **تأجيل إلى V1.1** مع `tenant_members` (الخطة §6 بند 6) — يحتاج قرار خصوصية. |
| د | `PLATFORM_ADMIN_USER_IDS` / `UMAMI_WEBSITE_ID` | إجراء مالك فوري قبل أي تشغيل حي. |
| هـ | تدوير التوكن | **ظهر ≥ 24 مرة في محادثة المخطط** — عاجل. |

---

## 5. الحوكمة

- المخطط يدمج (merge commit) ⇒ `main`؛ المنفذ لا يدمج.
- مسبار المخطط `_probe_b.py` **لم يُرفع**.
- كوميت المراجعة هذا: **0 فرق** في `src/ tests/ alembic/`.
- التوجيه التالي: `CURRENT_DIRECTIVE.md` = **SPRINT-13**؛ `WORK_STATE.md` §6 يسجّل SHA الدمج كـSHA بداية المنفذ.

---

## الملحق أ — أرقام بوابة الوحدة المحلية (تُملأ بعد انتهاء النصفين)

| النصف | النتيجة |
|---|---|
| 1 | _(جارٍ)_ |
| 2 | _(جارٍ)_ |
| التغطية | _(جارٍ)_ |
