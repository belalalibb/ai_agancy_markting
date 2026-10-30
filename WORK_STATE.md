# WORK STATE & EXECUTION POINTER

## سجل حالة العمل ومؤشر التنفيذ النشط

> **الغرض:** نقطة الحقيقة اللحظية لأي وكيل ذكاء اصطناعي أو مهندس يعمل على المستودع. يُسجل بدقة المهمة الحالية، والفرع النشط، وآخر فحص، والخطوة التالية.

---

## 1. ملخص الحالة العامة للمشروع (Project Snapshot)

- **اسم المشروع:** AI Marketing Platform (AI Growth OS / BrandMind AI / QEVION)
- **الحالة الإجمالية:** `SPRINT_03_DELIVERED_AWAITING_REVIEW`
- **آخر تحديث للمؤشر:** 2026-10-30 — المنفذ سلّم **SPRINT-03** على `work/implementation` في كوميتين فوق `main` (Part A `a985d55` بوابات CI، Part B `b712df0` Tenancy + هجرة 0003 + اتصالات Meta بنطاق العلامة + اختيار الصفحات) ⇒ PR #7 مفتوح وCI خضراء بالفحوص الخمسة. 298 وحدة + 17 تكامل = **315 ناجحاً، 0 فشل، تغطية 99.68%** على PostgreSQL 17.11. ملاحظات المالك الثلاث مطبَّقة. §7 من `CURRENT_DIRECTIVE.md` مملوء. **في انتظار مراجعة المخطط.**
- **المرحلة الحالية:** Phase 1 — من «موصل Meta دائم» إلى «منصة متعددة المستأجرين بعلامات ومنح + بوابات CI» (SPRINT-03)
- **الفرع المعتمد المستقر:** `main`
- **فرع التطوير المستمر:** `work/implementation`
- **المرجع المعرفي المعتمد:** مجلد `AI_Marketing_Platform_Hub/` (57 وثيقة تخصصية)
- **الميثاق المعماري الحاكم:** وثيقة `imaster_planner.md`

---

## 2. خط الأساس البرمجي المُتحقَّق منه (Verified Codebase Baseline)

> ⚠️ **تصحيح:** الأصول القديمة (`ult_agent_agent/…`, `first_lunch/…`, `whatsapp_ai_v2/…`) **ليست في هذا المستودع** وتبقى مرجعاً تصميمياً فقط. خط الأساس الحقيقي الآن هو كود SPRINT-01 أدناه.

| المكون | المسار | الحالة (تحقق المخطط 2026-10-29) |
|---|---|---|
| نواة FastAPI + إعدادات + تشفير MultiFernet (D-058) + `state` موقَّع | `src/core/` | ✅ جزء من 215 اختباراً، تغطية 99% (main @ `df01260`) |
| وحدة ربط Meta (OAuth v26.0، هوية من `/me`، `/debug_token` مُتحقَّق، تخزين PostgreSQL، صفحات + IG) | `src/modules/social_connect/` | ✅ 11 اختبار تكامل على PG حقيقية؛ `tenant_id` نص حر ⇒ يُستبدل بـ `brand_id` في SPRINT-03 |
| محرك SQLAlchemy async كسول + `dispose()` عند الإغلاق + `/health/ready` | `src/core/database.py`, `src/main.py` | ✅؛ الجاهزية تنتقل إلى `engine.connect()` في SPRINT-03 A6 |
| هجرات Alembic async | `alembic/versions/0001`, `0002` | ✅ صعود/نزول مُتحقَّق على PG 17.11 |
| سكربت نسخ احتياطي | `scripts/backup_progress.sh` | ✅ مقبول |

**مراجع المراجعات:** [`SPRINT-01`](./sprints/SPRINT-01_REVIEW.md) · [`SPRINT-02`](./sprints/SPRINT-02_REVIEW.md) · [`SPRINT-02.1`](./sprints/SPRINT-02.1_REVIEW.md)

---

## 3. المهمة النشطة الحالية (Active Task)

- **معرف المهمة:** `SPRINT-03-CI-GATES-AND-TENANCY`
- **وثيقة التوجيه المعتمدة (المصدر الوحيد):** [`CURRENT_DIRECTIVE.md`](./CURRENT_DIRECTIVE.md)
- **حالة المهمة:** `DELIVERED_AWAITING_REVIEW` — مُنفَّذة بالكامل على `work/implementation` (`a985d55` + `b712df0`) ⇒ **PR #7** https://github.com/belalalibb/ai_agancy_markting/pull/7 — CI خضراء (push https://github.com/belalalibb/ai_agancy_markting/actions/runs/40979056739 · PR https://github.com/belalalibb/ai_agancy_markting/actions/runs/40979061696). تقرير المنفذ الكامل (الأرقام، I-B1، الانحرافات الثمانية، الأسئلة) في **§7 من `CURRENT_DIRECTIVE.md`**.
- **الهدف:** (Part A) بوابات CI خمس منفصلة على GitHub Actions — ruff، وحدة، تكامل على `pgvector/pgvector:pg16`، gitleaks، بوابة تراخيص D-054 — + `/health/ready` عبر `engine.connect()`. (Part B) جداول `tenants` / `brands` / `brand_access_grants`، هجرة `0003` آمنة تنقل `social_connections` إلى `brand_id` مع backfill، مسارات Meta تحت `/brands/{brand_id}/…`، **اختيار الصفحات صراحةً** لمنع خلط صفحات عملاء الوكالة، حارس `provider_user_name` (لا يُسجَّل / يُحذف عند disconnect وعند سحب المنحة).
- **خارج النطاق:** مصادقة/JWT، تغيير التشفير، LiteLLM/Temporal/WhatsApp، واجهات أمامية.
- **السبرنت السابق:** `SPRINT-02.1-HOTFIX` — **مقبول ودُمج** (PR #5 ⇒ `df01260`) — مراجعته في [`sprints/SPRINT-02.1_REVIEW.md`](./sprints/SPRINT-02.1_REVIEW.md).
- **قرارات المالك المطبَّقة في PR #6:** Tenant/Brand الخيار ج المبسّط؛ D-048/049 ⇒ **D-056**، D-051 ⇒ **D-057**؛ هوامش الربح ⇒ فرضيات غير مُتحقَّق منها؛ حذف `AI_Marketing_Platform_Hub/imaster_planner.md`؛ MultiFernet مُسجَّل كاستثناء مرحلي **D-058**.

---

## 4. معايير القبول للمهمة الحالية (Acceptance Criteria)

الكاملة في **§4 من `CURRENT_DIRECTIVE.md`**. ملخصها:
- [x] CI خضراء على PR #7 بخمسة فحوص منفصلة: `lint`, `unit`, `integration`, `secrets`, `licenses`.
- [x] 0 فشل (وحدة + تكامل)، تغطية ≥ 95%، ruff نظيف على `src tests alembic scripts`.
- [x] `grep "Depends(get_db_session)" src/main.py` فارغ؛ `grep "tenant_id: str" src` فارغ.
- [x] هجرة `0003` تصعد على بيانات قديمة وتنزل وتصعد مجدداً (I-B1).
- [x] لا مسار `/social/meta/connections` خارج `/brands/{brand_id}/`؛ لا حقل `*token*` في OpenAPI إلا `has_page_token`.
- [x] اختبارا حارس `provider_user_name` (B-U11, I-B5) موجودان وناجحان.
- [x] `scripts/check_licenses.py` في venv نظيف ⇒ exit 0 (37 حزمة).
- [x] كوميتان (Part A, Part B) على `work/implementation` + كوميت توثيق؛ لا ملف محظور مُسّ.

---

## 5. الخطوة الدقيقة التالية (Exact Next Step)

| الترتيب | الدور | الخطوة |
|---|---|---|
| 1 | ~~المالك~~ | ~~دمج PR #6~~ ✅ تم (`aae3600`). |
| 2 | ~~المنفذ~~ | ~~Part A ⇒ Part B ⇒ §7 ⇒ هذا الملف~~ ✅ تم: `a985d55` + `b712df0` + كوميت التوثيق ⇒ PR #7 خضراء. |
| 3 | **المالك** | بعد أول تشغيل أخضر (تم): تفعيل حماية `main` بالفحوص الخمسة `lint`, `unit`, `integration`, `secrets`, `licenses` (توجيه §8/1). |
| 4 | **المخطط** | عند *«راجع شغل المنفذ»*: مراجعة SPRINT-03 على PG حقيقية (`pytest -m integration` مع `TEST_DATABASE_URL`) + التحقق من CI + قراءة §7 (الانحرافات 1–8 والأسئلة 1–3) ⇒ اعتماد/تعديلات على PR #7 ⇒ نقل `provider_user_name`/D-058/نموذج Tenancy إلى Hub docs ⇒ توجيه SPRINT-04. |
| 5 | **المالك** | دمج PR #7 بعد اعتماد المخطط (المنفذ لا يدمج PR الخاص به). ثم **حذف/تدوير توكن GitHub** المستخدم للدفع. |
| 6 | **المنفذ** | ينتظر «تابع» أو تعليقات المراجعة على PR #7. |

> **قاعدتان:** (1) لا يبدأ المنفذ قبل دمج توجيه المخطط في `main`. (2) المخطط يفرّع `genspark_ai_developer` من `main` **فقط**، لا من `work/implementation`.

---

## 6. سجل نقاط الاستئناف (Checkpoint Log)

> يُضاف سطر جديد في الأعلى عند كل نقطة مؤكدة. **لا تُحذف السطور القديمة.**

| التاريخ | الوكيل | الفرع | الكوميت | ما تم تأكيده | الخطوة التالية |
|---|---|---|---|---|---|
| 2026-10-30 | المنفذ | `work/implementation` → PR [#7](https://github.com/belalalibb/ai_agancy_markting/pull/7) | `a985d55` (Part A) / `b712df0` (Part B) / كوميت التوثيق فوقهما | SPRINT-03 مُسلَّم. **Part A:** `.github/workflows/ci.yml` بخمس وظائف منفصلة (lint / unit cov≥95 / integration على `pgvector/pgvector:pg16` / secrets gitleaks مع `GITHUB_TOKEN` + `pull-requests: read` / licenses D-054 عبر `scripts/check_licenses.py` في venv نظيف)، `/health/ready` عبر `engine.connect()`، `alembic.ini path_separator=os`. **Part B:** وحدة `src/modules/tenancy/` (tenants / brands / brand_access_grants، `BrandAccessResolver`، أدوار owner/manager/viewer، 8 مسارات)، هجرة `0003` بـbackfill خام + تنظيف التكرارات عند النزول (ملاحظة المالك 3)، `OAuthStateSigner` بزوج UUID (tenant, brand)، مسارات Meta تحت `/brands/{brand_id}/social/meta/…`، اختيار الصفحات الصريح (B6 + ملاحظة المالك 2: إعادة الربط تحفظ المختار وتحدّث توكنه) + CHECK في DB، حارس B7 (لا تسجيل للاسم/المعرف؛ حذف عند disconnect وسحب المنحة B8). **الأرقام:** 298 وحدة + 17 تكامل = 315 ناجحاً، 0 فشل، تغطية 99.68%، ruff نظيف، تراخيص 37/0 مرفوضة، PG 17.11 محلياً. CI خضراء على `b712df0` (push + PR). إعادة تهيئة الساندبوكس ×7؛ الدفع بعد كل نقطة أنقذ العمل مجدداً؛ كوميتات WIP سُحقت في `b712df0`. تفاصيل الانحرافات (8) والأسئلة (3) في §7. | مراجعة المخطط لـ PR #7 ⇒ المالك يفعّل حماية `main` ويدمج ⇒ تدوير التوكن ⇒ SPRINT-04. |
| 2026-10-29 | المخطط | `genspark_ai_developer` (من `main` @ `df01260`) → PR #6 | `c2175a3` (+ كوميت تسجيل SHA) → PR [#6](https://github.com/belalalibb/ai_agancy_markting/pull/6) | مراجعة مستقلة لـ SPRINT-02.1 @ `4480309` في بيئة طازجة (PG 17.11 أُعيد تثبيتها): 204 وحدة + 11 تكامل = **215 ناجحاً، 99%**، F1 0/50 فشل، هجرة 0002 صعود/نزول على PG، `/health/ready` حيّاً 200/503/503 بلا تسريب، تراخيص شجرة التشغيل نظيفة (36 حزمة) ⇒ **مقبول** ⇒ فُتح PR #5 ودُمج في `main` @ `df01260` بتفويض المالك. طُبّقت قرارات المالك: `ARCHITECTURE_LOCK` (D-056/D-057 ترقيم، D-058 استثناء Fernet، الهوامش ⇒ فرضيات)، حُذف `Hub/imaster_planner.md`. أُصدر توجيه **SPRINT-03** (CI Gates + Tenancy + اختيار الصفحات + هجرة 0003). | المالك يدمج PR #6 ⇒ يطلق المنفذ ⇒ PR #7. |
| 2026-10-29 | المنفذ | `work/implementation` | `7cf29df` | SPRINT-02.1-HOTFIX مُسلَّم في كوميت واحد: الهوية من `GET /me` (إلزامي) + فحص `is_valid`/`app_id`/`user_id` من `/debug_token` (`MetaTokenInvalidError`/`MetaTokenForeignAppError` ⇒ 401 reconnect) + حذف `_fallback_identity` واختباريه + `encrypted_page_token` NULLable (هجرة 0002 صعوداً/نزولاً مع صفوف NULL) + `has_page_token` + `engine.dispose()` مشروط + `/health/ready` (200/503 حيّاً بلا تسريب DSN) + سقف ترقيم 50 + إصلاح F1 (50/50). 204 معزولاً + 11 تكامل = 215، تغطية 99%، ruff نظيف، PostgreSQL 17.11 محلية. انقطاع الجلسة ×3؛ الدفع بعد كل نقطة أنقذ العمل مجدداً. ⚠️ PR #3 كان مغلقاً (merged عبر PR #4) فلم يتحدث؛ لم يُفتح PR جديد. | مراجعة المخطط لـ `7cf29df`؛ قرار PR جديد؛ رد المالك على §6 من مراجعة SPRINT-02. |
| 2026-10-29 | المخطط | `genspark_ai_developer` → PR #4 | `95d5223` → PR [#4](https://github.com/belalalibb/ai_agancy_markting/pull/4) | مراجعة مستقلة لـ SPRINT-02 على PostgreSQL 17.11+pgvector مثبّتة في بيئة المراجعة: 189 passed / 8 تكامل / 99.56% / ruff نظيف — **لكن** اختبار متذبذب (F1، 5.7%) + 8 عيوب كود مُثبتة بالسطر (أخطرها: `/debug_token` بلا فحص `is_valid`، `_fallback_identity` يسحق السجلات). تقرير المراقب الخارجي تُحقق منه: 6/6 صحيح في الكود. قرار Tenant/Brand رُفع للمالك. أُصدر `SPRINT-02.1-HOTFIX`. | المالك يدمج PR #4 ⇒ المنفذ ينفذ 02.1 ⇒ المالك يرد على §6. |
| 2026-10-29 | المنفذ | `work/implementation` → PR #3 | `c85c0bd` (Part A) / `96b1008` (Part B) | SPRINT-02 مُسلَّم: 181 معزولاً + 8 تكامل = 189 ناجحاً، تغطية 99%، ruff نظيف، `/docs` يعرض المسارات الخمسة + `/health` فقط، لا `InMemory`/`"***"`/أسرار في `src`. التكامل على PostgreSQL 17 محلية (لا Docker في البيئة). انقطاع الجلسة ×3 مجدداً؛ الدفع البعيد أنقذ كل شيء. | مراجعة المخطط لـ PR #3. |
| 2026-10-29 | المخطط | `genspark_ai_developer` → PR #1 | `31b2d36` → PR [#1](https://github.com/belalalibb/ai_agancy_markting/pull/1) | مراجعة مستقلة لـ SPRINT-01: 123 passed / 99.62% / ruff نظيف / `/docs` يعمل — **مقبول**. 4 ثغرات حرجة (v19.0، state غير مُتحقَّق، صلاحيات نشر، إعادة محاولة على Rate Limit) رُحِّلت إلى SPRINT-02 Part A. أُصدر توجيه SPRINT-02 (481 سطراً، 18 اختباراً معزولاً + 8 تكامل). أُنشئ أرشيف `sprints/`. | المالك يدمج PR #2 ثم PR #1 ثم يطلق المنفذ. |
| 2026-10-29 | المنفذ | `work/implementation` → PR #2 | `118c0bb` (كود) / `ed5fb47` (تقرير) | SPRINT-01 مُسلَّم: 123 اختباراً، 99.6%. انقطاع الجلسة ×3؛ الدفع البعيد بعد كل نقطة هو ما حفظ العمل (النسخ المحلية و AI Drive لم تنجُ). | مراجعة المخطط. |
| 2026-10-29 | المخطط | `genspark_ai_developer` | `d8e502a` | جرد المستودع + توجيه SPRINT-01 Rev 2 (لم يصل للمنفذ لأن PR #1 لم يُدمج قبل إطلاقه). | — |
| 2026-10-29 | المالك/الإعداد | `main` | `555768d` | تهيئة المستودع: Hub + الميثاق + ملفات التحكم + توجيه Rev 1. | — |
