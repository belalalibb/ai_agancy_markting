# 📋 SPRINT-11-FINANCE-AND-ENTITLEMENTS — تقرير مراجعة المخطط المستقلة

> **PR [#39](https://github.com/belalalibb/ai_agancy_markting/pull/39)** (`work/implementation @ 04e21ad` → `main @ 367fc6d`) · الكوميتات: Part A `f71ddfc` · Part B `6b371f0` · Part C `68b42b0` · docs `04e21ad`.
> **القرار (D-063): Path 1 — قُبل ودُمج** ⇒ `main @ 8fbbe8c`. النتائج E1–E5 (أدناه §3) **مؤكَّدة على PG 17 فعلياً** لكن **لا واحدة منها مانعة للدمج** (نطاقها ضيق، لا كشف أسرار، لا تجاوز لسقف المنصة المالي)؛ تُغلق كلها في **SPRINT-11.1 Part A** قبل كتالوج D-077.
> **فروع `wip/*` السبعة حُذفت** من GitHub بعد الدمج (نظافة G6).

---

## 1. ما تحقق منه المخطط فعلياً (بيئة أُعيد بناؤها ×2 خلال المراجعة بسبب إعادة ضبط الساندبوكس؛ PostgreSQL 17.11 UTF8 + pgvector من حزم Debian؛ `initdb -E UTF8 --locale=C.UTF-8`؛ `pip install -e ".[dev]"` كما في CI)

| # | الفحص | النتيجة على `04e21ad` | الحكم |
|---|---|---|---|
| 1 | `ruff check src tests alembic scripts && ruff format --check …` (نطاق CI) | All checks passed · 247 files formatted | ✅ |
| 2 | **المجموعة الكاملة** `pytest -q --cov=src --cov-fail-under=95` مع `TEST_DATABASE_URL` (PG 17) | **1,331 passed / 0 failed · 98.11%** (18:55 دقيقة) | ✅ مطابق لتقرير المنفذ |
| 3 | بوابة CI للتكامل `pytest -m integration` على PG 17 | **110 passed** / 1,221 deselected (1:40) | ✅ |
| 4 | بوابة CI للوحدات **بلا DB** `pytest -m "not integration" --cov-fail-under=95` | **1,221 passed / 1 skipped** · التغطية **94.57%** ⇒ pytest-cov يطبع `FAIL Required test coverage of 95% not reached` **لكن رمز الخروج 0** | ⚠️ انظر §3-F1 |
| 5 | `alembic upgrade head` ⇒ `downgrade 0012` ⇒ `upgrade head` على قاعدة ثانية نظيفة | 0014 ⇒ 0012: الجدولان `brand_entitlements`/`entitlement_events` + أعمدة `brands.referred_by_brand_id`/`learning_consent` + `wa_messages.referral` تختفي؛ `ai_cost_log` يعود إلى 14 عموداً (بلا `request_id`/`settled_at`)؛ الصعود مجدداً ⇒ 28 جدولاً، `version_num = 0014` | ✅ |
| 6 | greps الملزمة §4-3 (الأربعة) | 0 / 0 / 0 / 0 | ✅ |
| 7 | `git diff origin/main -- src/modules/ai_gateway/contract.py` و`pyproject.toml` | فارغان (ErrorCategory 12 ثابتة؛ لا تبعية جديدة) | ✅ |
| 8 | `PROTECTED_ROUTE_COUNT == 66` (`tests/test_auth_guard.py:77`, `tests/modules/content/test_router.py:1437`) | 66 | ✅ |
| 9 | CI على GitHub لكل كوميت | `f71ddfc` 5/5 · `6b371f0` 5/5 · `68b42b0` 4/5 (**unit = cancelled** بسبب تزامن الدفع، لا فشل) · `04e21ad` 5/5 ×2 | ✅ (الكوميت الأخير يغطي C) |
| 10 | كوميت docs `04e21ad` يمس الكود؟ | `CURRENT_DIRECTIVE.md` + `WORK_STATE.md` فقط | ✅ |
| 11 | قيود D-016: استيراد `tenancy.infrastructure` من وحدات أخرى | **جديد:** `whatsapp/webhook.py:57` يستورد `PostgresBrandConsentReader` مباشرة (كان الوحيد سابقاً `brand_brain/infrastructure/postgres_repository.py:40` لـ`BrandRow`) | 🟡 §3-F2 |
| 12 | B6 (عدّاد `before_cursor_execute`): `test_b6_single_query_before_hmac` + `test_b6_u1/u2` | يمرّ على PG | ✅ |
| 13 | B2-أ SSRF: `test_a2_private_address_refused_with_zero_network_calls` (4 مضيفين) + `mixed_public_and_private` | يمرّ؛ `_is_global_address` يرفض multicast/reserved أيضاً | ✅ |
| 14 | D-078 سباق الإحالة المزدوجة | `activate()` يبدأ بـ`lock_brand` (`SELECT … FOR UPDATE`) ثم `has_event(brand,'activated')` داخل نفس المعاملة ⇒ `referral_credit` واحد | ✅ |
| 15 | `PlatformAdmin` افتراضياً | `platform_admin_user_ids = []` ⇒ 403 `platform_admin_required` لكل `/admin/*`؛ مبني على `CurrentUserFresh` | ✅ |
| 16 | مفتاح القتل D-076 | `FEATURE_ENTITLEMENTS_ENFORCED=false` ⇒ `check() ⇒ False` ⇒ الإعداد العام كما قبل السبرنت (`test_kill_switch.py`) | ✅ |
| 17 | حجم Part C | `git diff --numstat 6b371f0 68b42b0 -- src` = **+1,448** خام (المنفذ أبلغ 1,389 لملفات `.py` فقط + `REMOVABILITY.md` 59) | ✅ قرار المالك: مقبول دون تقسيم |

---

## 2. تقييم تقرير المنفذ (§6 @ `04e21ad`)

- **دقيق وصادق:** الأرقام (1,331 / 98.11% / 66 / greps 0 / الهجرات ذهاباً وإياباً) تطابقت حرفياً. الانحرافات الثمانية مُعلَنة ومبرَّرة. إعلان «`check_counter` مكشوفة لكن غير مستدعاة» (C-2) **صريح** — وهو ما يحوّل E2 من «عيب مخفي» إلى «نطاق مؤجَّل معلن» (ما زال يُغلق في 11.1).
- **تحفظ واحد على C-8:** تشغيل `-m integration` وحدها 83% لا يهم فعلاً؛ لكن **تشغيل `-m "not integration"` وحدها (وهي بوابة CI الرسمية `unit`) = 94.57% < 95%** — والمنفذ كتب «≥95% ✅». الرقم خاطئ؛ البوابة تمرّ لسبب آخر (§3-F1).

---

## 3. ما أثبته المخطط بالتجربة (مِجسّ مستقل `tests/integration/_probe_e.py` على PG 17 UTF8 — **حُذف قبل أي `git add`**)

| الفرضية | المِجسّ | الناتج الفعلي على PG 17 | الخطورة | الإغلاق |
|---|---|---|---|---|
| **E1 — `content_plan` يكتب `learning_signals` رغم `consent=none`** | `PATCH /learning-consent {none}` ⇒ `POST /brain/content-plan` (3 عناصر عبر بوابة مُبرمَجة) ⇒ عدّ `learning_signals WHERE signal='materialized'` | **3 صفوف** (المتوقع 0). السبب: `brand_brain/advisor.py:445–448` حلقة غير مشروطة؛ `accept/dismiss` فقط يمرّان بـ`_record_signal` المحروس بـ`may_write_signal` | 🔴 خصوصية (D-080 / معيار 17) — لكن الصفوف **ميزات فقط** (قناة، إصدار prompt، نموذج) بلا نص، والمسار يتطلب OWNER/EDITOR | 11.1 Part A-1 |
| **E2 — حصص `monthly_posts`/`monthly_wa_conversations` غير مُطبَّقة** | `grep -rn check_counter src/` ⇒ تعريف واحد بلا مستدعٍ | مؤكَّد بالكود (المنفذ أعلنه C-2) | 🟡 مالي/منتجي: الباقة تُعرض ولا تُفرض على العدّادات الشهرية | 11.1 Part A-2 |
| **E2c — العلامة بلا سجل entitlement: العرض ≠ الفرض** | `GET /brands/{id}/entitlement` لعلامة بلا صف | العرض: `plan=trial, daily_tokens=20,000`؛ **الفرض الفعلي:** `check() ⇒ False` ⇒ الإعداد العام **200,000** | 🟡 مالي: العميل يرى 20k ويستهلك حتى 200k من مفتاح المنصة؛ لا يتجاوز سقف المنصة $25 (يبقى محروساً) | 11.1 Part A-2 (قرار المخطط: **unstored ⇒ تُحكم بحدود trial** لا بالإعداد؛ الإعداد يبقى سقفاً احتياطياً فقط عند مفتاح القتل) |
| **E3 — `PATCH /learning-consent` مركَّب في `entitlements/router.py`** | قراءة الكود `entitlements/router.py:107–125` ⇒ يستدعي `tenancy.set_learning_consent` | مؤكَّد: مسار ملكه `tenancy` (العمود على `brands`) يعيش في وحدة **قابلة للإزالة** ⇒ إزالة `entitlements` تُسقط التحكم بالموافقة (يناقض D-076 و`REMOVABILITY.md:11`) | 🟡 حدود (D-016/D-076) | 11.1 Part A-3 |
| **E4 — صفوف `pending`/`ambiguous` تُحتسب 0 رمزاً في ميزانية العلامة** | إدراج صف `pending` بـ`prompt_tokens=NULL` ⇒ `combined_usage_since` ⇒ `settle_stale` ⇒ إعادة القياس | **0 ثم 0** بعد `settle_stale` (الصف أصبح `ambiguous` والرموز بقيت NULL). السبب: `budget_guard.py:254–270` الحجز لا يكتب رموزاً؛ `pg_budget.py:34–38` يجمع `coalesce(tokens,0)`. **ملاحظة:** سقف المنصة بالدولار يجمع `estimated_cost_usd` فيحتسب الحجز صحيحاً — الخلل في **رموز العلامة فقط** | 🟡 مالي (تقدير ناقص خلال نافذة الحجز وبعد النتائج الغامضة) | 11.1 Part A-4 |
| **E5 — إحالة ذاتية من نفس المستأجر** | `POST /brands {referred_by_brand_id: <علامتي>}` | **201** — القيد `ck_brands_referred_by_not_self` يمنع فقط `referred = id` (مستحيل عند الإنشاء)؛ `tenancy/service.py:78–83` يتحقق من الوجود فقط | 🟡 مالي (D-078: مستأجر يُنشئ علامة ثانية ويُحيلها لنفسه ⇒ `referral_credit` 10%) | 11.1 Part A-5 |
| **F1 — جديد: بوابة التغطية في CI لا تفشل عند 94.57%** | قراءة `pytest_cov/plugin.py` (7.1.0) + `coverage.results.should_fail_under(94.57, 95, precision=0) ⇒ False` | `--cov-fail-under=95` بدقة 0 يقرّب 94.57 ⇒ 95 ⇒ «لا فشل» في `session.testsfailed` بينما `pytest_terminal_summary` يطبع «FAIL» بمقارنة خام. سجل CI لـ`04e21ad` (job 125021981575، Python 3.12) يُظهر **نفس** السطر `FAIL … 94.57%` مع نجاح الوظيفة | 🟡 حوكمة: البوابة «95%» فعلياً «≥94.5%»؛ المجموعة الكاملة 98.11% فلا خطر فوري | 11.1 Part A-6: `--cov-precision=2` في `ci.yml` **أو** رفع تغطية الوحدات ≥95.00 فعلياً (المسارات الناقصة: `ai_gateway/dependencies.py:99–105`, `tenancy/dependencies.py:73`, `whatsapp/client.py:86–87`, …) |
| **F2 — جديد: `whatsapp/webhook.py:57` يستورد `tenancy.infrastructure.postgres_repository`** | grep قبل/بعد | استيراد بنية تحتية لوحدة أخرى مباشرةً (brand_brain يستخدم `tenancy.dependencies.get_brand_consent_reader` — النمط الصحيح) | 🟢 حدود صغيرة | 11.1 Part A-3 (معاً مع E3: `tenancy/dependencies.py` يُصدّر `build_brand_consent_reader(session)`) |

**لماذا ليست مانعة (Path 1):** لا تسريب أسرار؛ لا مسار عام جديد؛ سقف المنصة المالي ($25) محروس بـ`estimated_cost_usd` الذي **يشمل** الحجوزات؛ E1 يكتب ميزات لا نصوصاً ولعلامة يملكها الفاعل؛ E2/E2c/E5 تقع تحت تفعيل يدوي من `PlatformAdmin` (القائمة فارغة إنتاجياً الآن). الإصلاحات الخمسة صغيرة (≈ 150–220 سطر `src/`) ومتجانسة مع D-077 فتُدمج في نفس السبرنت.

---

## 4. ما يُفتح الآن

- **SPRINT-11.1** (التوجيه النشط في `CURRENT_DIRECTIVE.md`): Part A = إغلاق E1–E5 + F1/F2؛ Part B/C = كتالوج المزودين D-077 (هجرة 0015، `/api/v1/admin/ai/*`، probe، `FEATURE_PROVIDER_CATALOG_ENABLED`, `REMOVABILITY.md`).
- **SECURITY_BASELINE** صفوف B1–B6 و D-075 و D-080 ⇒ ✅ مع SHA `8fbbe8c` (مُحدَّثة في PR التخطيط هذا).

## 5. ملاحظة حوكمة

- إعادة ضبط الساندبوكس حدثت **مرتين** أثناء هذه المراجعة؛ كل النتائج أعلاه أُعيد إنتاجها بعد آخر إعادة بناء (لا نتيجة منقولة من بيئة سابقة).
- توكن GitHub ظهر في المحادثة **≥ 15 مرة** — التدوير ما زال مطلوباً من المالك.
