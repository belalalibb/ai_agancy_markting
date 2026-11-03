# 📋 SPRINT-12-CLOSURES-LEAD-LOOP-PULSE — تقرير مراجعة المخطط المستقلة

> **PR [#45](https://github.com/belalalibb/ai_agancy_markting/pull/45)** (`work/implementation @ 6b512a8` → `main @ e66e561`) · الكوميتات: Part A `8ed1346` · Part B `5233aaa` · Part C `38bd73d` · C-fix `f606350` (اختبار فقط) · docs `6b512a8`.
> **القرار (D-063): Path 1 — قُبل ودُمج ⇒ `main @ b98cffe`.** كل بوابات §3 خضراء على PG 17.11 UTF8 ببيئة أُعيد بناؤها (×2 خلال هذه المراجعة؛ ≥ 7 إعادات ضبط للساندبوكس في الدورة كلها). **الفرضيات الست H1–H6 من مراجعة SPRINT-11.1 مُغلقة فعلاً** (اختبارات PG حقيقية لكل واحدة + تحقّق المخطط من الكود). المِجسّ المستقل كشف **ملاحظتين غير مانعتين** (§3: S-1 التوصيات «المرفوضة» تظهر في البطاقة العامة؛ S-2 إعادة `ack` تعيد 200 لا 404/409) تُغلقان في SPRINT-12.1.
> **يُرفع الحظر التشغيلي** المفروض منذ SPRINT-11.1 (كان: لا كتالوج، لا `PLATFORM_ADMIN_USER_IDS`، لا `suspend`). يبقى تحذير واحد: راجع افتراضات `FEATURE_PULSE=true` / `FEATURE_LEAD_LOOP=true` / `PULSE_INTERVAL_SECONDS=900` / سقف D-082 = 300 قبل الربط الحي.

---

## 1. ما تحقق منه المخطط فعلياً (PostgreSQL 17.11 UTF8 + pgvector من حزم PGDG على Debian 13؛ `initdb -E UTF8 --locale=C.UTF-8`؛ `pip install -e ".[dev]"`؛ Python 3.13)

| # | الفحص | النتيجة على `6b512a8` | الحكم |
|---|---|---|---|
| 1 | `ruff check src tests alembic scripts` + `ruff format --check …` | 0 / 0 على `6b512a8` **و** على أشجار `8ed1346` / `5233aaa` / `38bd73d` (worktrees مستقلة) | ✅ |
| 2 | بوابة الوحدات **بلا DB** `pytest -m "not integration" --cov=src --cov-fail-under=95 --cov-precision=2` | **1,421 passed / 0 failed · 95.28%** · exit 0. (نُفِّذت على نصفين بـ`--cov-append` + `coverage report --fail-under=95` لأن التشغيل الواحد استهلك 86% من ذاكرة الساندبوكس (985 MB) وقُتل OOM عند 86% — ليس فشل اختبار؛ المجموع = 624 + 797 = 1,421 مطابق لتقرير المنفذ حرفياً) | ✅ |
| 3 | بوابة التكامل `pytest -m integration` على PG 17 (`ai_growth_os_test`) | **140 passed / 0 failed** · 1,421 deselected (4:01) — مطابق لتقرير المنفذ | ✅ |
| 4 | `alembic upgrade head ⇒ downgrade 0015 ⇒ upgrade head` على قاعدة نظيفة (`ai_growth_os_test3`, `server_encoding=UTF8`) | `0016` ⇒ `0015` ⇒ `0016 (head)`: **3 جداول** (`lead_attributions`, `pulse_alerts`, `wa_number_caps`) 3 ⇒ 0 ⇒ 3 · **5 أعمدة** (`wa_conversations.lead_stage/_set_by_user_id/_at`, `brand_recommendations.share_token_hash/shared_at`) 5 ⇒ 0 ⇒ 5 · **11 قيداً مسمّى** 11 ⇒ 0 ⇒ 11 · فهرسان جزئيان · جداول أخرى 30 لم تُمسّ (33 بعد الرجوع) | ✅ |
| 5 | greps الملزمة §4-3 **الثمانية** (بأوامر التوجيه حرفياً `--include=*.py`) | (1) `learning-consent` في entitlements = **0** · (2) `tenancy.infrastructure` خارج القائمة = **0** · (3) `src.modules.entitlements` خارج القائمة البيضاء = **0** · (4) `ai_gateway.catalog` خارج ai_gateway/main = **0** · (5) `get_secret_value()` غير credential = **0** · (6) `src.modules.pulse` خارج pulse/main = **0** · (7) `brand_brain.public_fetch` في ai_gateway = **0** · (8) diff `contract.py`/`public_fetch.py`/`pyproject.toml` = **0 سطر** · `# pragma: no cover` جديد = **0** | ✅ |
| 6 | `ErrorCategory` | **12** (استيراد مباشر) | ✅ |
| 7 | `PROTECTED_ROUTE_COUNT` / العامة | **83** (`tests/test_auth_guard.py:89`) · `PUBLIC` = **7** تحت `/api/v1` (+2 health). الفرق عن توقّع التوجيه (82): +2 `POST`/`DELETE …/share` −1 `approvals/batch` المؤجَّل — **الكود هو المرجع (§5-5)** | ✅ |
| 8 | حجم `src/` لكل Part (`git diff --numstat -- src`) | A **+510 −89** · B **+1,636 −14** · C **+283 −0** ⇒ المجموع **+2,429 −103** (التوجيه قدّر ≈ 1,600) · A+B = 2,043 > 1,300 ⇒ **صمّام §4-6 أُطلق بحق** — C-1 فقط، C-2 مؤجَّل | ✅ مقبول (المالك طلب كل بنود §2 نصاً؛ التجاوز في وحدة `pulse` الكاملة ≈ 700) |
| 9 | كوميت docs `6b512a8` يمس الكود؟ | `CURRENT_DIRECTIVE.md` + `WORK_STATE.md` فقط · `git diff f606350 6b512a8 -- src tests alembic` = 0 · `f606350` يمس `tests/integration/test_sprint12_b_pg.py` فقط (0 في `src/`) | ✅ |
| 10 | CI على GitHub | `6b512a8`: **5/5 ×2** · `8ed1346`: **5/5** · `5233aaa`/`38bd73d`: 4/5 + `unit` **cancelled** (concurrency) · `f606350`: 3/5 + `unit`/`integration` cancelled. عُوِّض بالبند 1 (ruff لكل شجرة) + البندين 2/3 على الرأس | ✅ |
| 11 | PR #45 | `mergeable=true`, `mergeable_state=clean` · دُمج `b98cffe` | ✅ |

---

## 2. تقييم تقرير المنفذ (§6 @ `6b512a8`) — الانحرافات الـ12

| # | الانحراف | حكم المخطط |
|---|---|---|
| 1 | الصمّام §4-6: A+B = 2,043 > 1,300 ⇒ C-1 فقط؛ **C-2 Batch Approval ⇒ SPRINT-12.1** (عنوان الكوميت أُبقي حرفياً، الجسم يصرّح) | ✅ **مقبول — تطبيق صحيح للصمّام.** تحققتُ أن `38bd73d` لا يحوي أي `approvals/batch` (grep 0). Batch Approval يُدرج في SPRINT-12.1 كـPart D |
| 2 | لا جدول `brand_diagnoses` ⇒ `{diag_id}` = معرّف توصية؛ ختم المشاركة على `brand_recommendations.share_token_hash` (UNIQUE) + `shared_at` | ✅ مقبول (§5-5). تحققتُ: `set_recommendation_share` مقيَّد بـ`brand_id AND id` ⇒ `diag_id` أجنبي ⇒ 404 و0 صفوف (مِجسّ P1) |
| 3 | `POST`/`DELETE …/share` بدل `PATCH …/diagnoses/{id}` ⇒ 83 لا 82 | ✅ مقبول — §2-C-1 نصّ على POST/DELETE؛ بطاقة المهمة كانت تقديرية |
| 4 | البطاقة العامة: عنوان مُنظَّف regex ≤ 80 + ≤ 5 توصيات (`kind`/`title`/`evidence_grade`) + درجة إجمالية = الأقوى؛ «3–5» ⇒ الحد الأدنى 1 لا اختلاق | ✅ مقبول. المِجسّ أكد المفاتيح الستة فقط (`cta_url, evidence_grade, generated_at, recommendations, title, watermark`) ولا `rationale`/`provenance`/`evidence_ids`/معرّفات. **لكن انظر S-1** |
| 5 | استثناء واحد في حارس OpenAPI لـ`/api/v1/d/{token}` | ✅ مقبول — الاسم من التوجيه؛ القيمة قدرة قابلة للإلغاء لا اعتماد حساب. `SHARE_PATH_RE` يرفض كل ما ليس 40–64 حرف urlsafe (مِجسّ P6: `/d/short` ⇒ **422**) |
| 6 | رموز الاعتراض ⇒ `lead_attributions(grade='owner_input')` بالرمز فقط لا `wa_learning_signals` | ✅ مقبول — متسق مع D-069 و`draft_id` الإلزامي في إشارات التعلّم |
| 7 | فحص Pulse (7): لا `quality_rating`/`messaging_limit` في `whatsapp_accounts` ⇒ `[]` | ✅ مقبول — التوجيه طلب تسجيله انحرافاً. يُضاف العمودان مع webhook `account_update` لاحقاً (ليس 12.1) |
| 8 | عالم الاختبار أنتج 7 تنبيهات لا 6 (`publish_failed` لعلامتين) | ✅ مقبول — سلوك صحيح |
| 9 | `tests/conftest.py` يثبّت `get_published_post_lookup ⇒ None`؛ `test_main` يتوقع خطوتين في `extra_steps` | ✅ مقبول — `FEATURE_PULSE` افتراضه `true` بنص التوجيه |
| 10 | منفذ العدّادات يملكه `pulse` ويُربط في `main.py` عبر `PgPlanCounterUsageReader`؛ `entitlements` لا يستورد `pulse` (اختبار AST) | ✅ مقبول — D-016 صحيح؛ `tests/isolation/test_pulse_removable.py` 5 اختبارات + `REMOVABILITY.md` |
| 11 | CI لشجرة B: `unit` أُلغيت بالتزامن | ✅ مقبول — عُوِّض بالبند 1-2 أعلاه (1,421/95.28% على الرأس الحاوي لشجرة B كاملة) |
| 12 | عشوائية ساعة في `test_b3_pulse_…` قرب منتصف الليل UTC ⇒ `f606350` اختبار فقط | ✅ مقبول — تحققتُ `git show f606350 --stat` = ملف اختبار واحد؛ السبب الجذري (حدّ اليوم UTC في `daily_cap_near` = نفس تعريف `inbox.py`) صحيح تصميمياً |

---

## 3. ما أثبته المخطط بالتجربة (مِجسّ مستقل `tests/integration/_probe_s12.py` على PG 17 UTF8 — **حُذف قبل أي `git add`**؛ `git status` = 0 قبل الدمج)

| # | الفرضية | النتيجة الحية | الحكم |
|---|---|---|---|
| P1 | `diag_id` من علامة أخرى تحت `/brands/{BRAND_ID}/…/share` | **404 `recommendation_not_found`** · 0 صفوف مختومة | ✅ |
| P2 | شكل البطاقة العامة | المفاتيح الستة فقط؛ كل توصية = `evidence_grade/kind/title` | ✅ |
| **S-1** | **التوصيات `dismissed` تظهر في البطاقة العامة** | `list_recommendations(brand_id, status=None)` في `share.py:229` يجلب **كل** الحالات ⇒ توصية رفضها المالك ظهرت عنواناً في الرابط العام (`P2_dismissed_sibling_in_card = true`)؛ ومشاركة توصية `dismissed` نفسها ⇒ 201 | ⚠️ **غير مانع** (لا PII؛ العنوان مُنظَّف؛ الرابط يُنشئه المالك بوعي) — **يُغلق في SPRINT-12.1 A-1:** استبعاد `dismissed` من البطاقة ورفض مشاركتها (409 أو 404) |
| P4 | الحدّ 60/دقيقة/IP بالمحدِّد **الحقيقي** (أُزيل `FakeRateLimiter`) | بعد استنفاد 60 ⇒ **429 + `Retry-After: 59`**؛ الدلو مشترك عبر الاختبار كله (process-wide) | ✅ |
| P5 | VIEWER يشارك/يلغي | **403 / 403** | ✅ |
| P6 | رمز قصير / `POST /d/{token}` | **422 / 405** (لا 401 — المسار عام) | ✅ |
| P7 | رمز مجهول | **404** بلا `Cache-Control` (الترويسة العامة على 200 فقط) | ✅ |
| P8 | إعادة المشاركة تدوّر | رمز جديد ≠ القديم؛ القديم **404**؛ الجديد **200** | ✅ |
| Q1/Q2 | Pulse: VIEWER يقرأ / يُقرّ | **200 / 403** | ✅ |
| Q3 | `ack` لتنبيه علامة أخرى عبر مسار علامتي | **404 `pulse_alert_not_found`** · صف العلامة الأخرى لم يُمسّ | ✅ |
| Q4 | MANAGER يُقرّ | **200** | ✅ |
| **S-2** | **`ack` ثانٍ لتنبيه مُقَرّ** | **200** بنفس `acknowledged_at` الأول (`PgPulseRepository.acknowledge` يعيد الصف الموجود؛ لا كتابة ثانية — `Q9_ack_rows = 1`) | ℹ️ **مقبول كـidempotent؛ غير مانع.** التوجيه لم يحدّد. يُوثَّق في 12.1 (لا تغيير مطلوب) |
| Q7 | مستأجر بلا منحة على BRAND2 | **404 `brand_not_found`** (لا 403 — لا تسريب وجود) | ✅ |
| Q8 | `FEATURE_PULSE=false` وقت الطلب | **404 `pulse_disabled`** | ✅ |

**تحققات كود إضافية (قراءة):** `_ensure_number_cap` يُستدعى في مسارَي الإرسال (`inbox.py:198, 236`) قبل أي اتصال Graph؛ `count_outbound_since` يعدّ **كل** الصادر (بما فيه `failed`/`ambiguous`) منذ 00:00 UTC — محافظ، صحيح لـD-082؛ `pinned_http_client` يعيد الحلّ عند كل اتصال (DNS rebinding)؛ `_one_credential` على `ProviderCreate` **و** `ProviderPatch`؛ `plan_inactive_http` ⇒ 402 بدون `Retry-After`.

---

## 4. القرار وما يُفتح الآن

1. **PR #45 مدموج ⇒ `main @ b98cffe`.** H1–H6 ✅ مُغلقة. `SECURITY_BASELINE.md` يُحدَّث: صف «SPRINT-11.1 — H1–H6» ⇒ ✅ `b98cffe`؛ «catalog SSRF ⚠️ partial» ⇒ ✅؛ صف جديد «D-082 WA caps ✅ (`quality_rating` مؤجَّل)».
2. **يُرفع الحظر التشغيلي** (كتالوج / مشرف منصة / suspend). يُشترط قبل الربط الحي: مراجعة افتراضات `FEATURE_PULSE`/`FEATURE_LEAD_LOOP`/`PULSE_INTERVAL_SECONDS`/سقف 300.
3. **SPRINT-12.1** (يُصدر الآن في `CURRENT_DIRECTIVE.md`): L1 UI (6 شاشات Jinja2/htmx/Pico RTL) + Umami + Playbooks + تبويب AI للمشرف + **Batch Approval C-2 المؤجَّل** + إغلاق S-1 — مع بوابة G6 (تقسيم صريح إلى 12.1-أ/12.1-ب).
4. فروع `wip/*`: لا يوجد (المنفذ سحق قبل الدفع). `work/implementation` يبقى ليستأنف منه المنفذ.

## 5. ملاحظة حوكمة

- الساندبوكس أُعيد ضبطه مرتين أثناء هذه المراجعة (≥ 7 في الدورة). التشغيل الواحد لبوابة الوحدات (1,421 اختباراً + coverage branch) تجاوز ذاكرة الصندوق (985 MB) ⇒ اعتُمد التقسيم على نصفين مع `--cov-append`؛ الرقم النهائي (95.28%) مطابق لـCI ولتقرير المنفذ. **توصية:** في SPRINT-12.1 يضيف المنفذ هدف `make unit-split` موثَّقاً (لا تغيير في CI).
- 🔑 توكن GitHub ظهر في المحادثة **≥ 18 مرة** في جلسة المخطط — **التدوير فوري** + حماية `main`.
