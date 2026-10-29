# WORK STATE & EXECUTION POINTER

## سجل حالة العمل ومؤشر التنفيذ النشط

> **الغرض:** نقطة الحقيقة اللحظية لأي وكيل ذكاء اصطناعي أو مهندس يعمل على المستودع. يُسجل بدقة المهمة الحالية، والفرع النشط، وآخر فحص، والخطوة التالية.

---

## 1. ملخص الحالة العامة للمشروع (Project Snapshot)

- **اسم المشروع:** AI Marketing Platform (AI Growth OS / BrandMind AI / QEVION)
- **الحالة الإجمالية:** `SPRINT_01_ACCEPTED__SPRINT_02_DIRECTIVE_ISSUED`
- **آخر تحديث للمؤشر:** 2026-10-29 — المخطط راجع SPRINT-01 (مقبول) وأصدر توجيه SPRINT-02
- **المرحلة الحالية:** Phase 1 — Meta connector: من «تدفق OAuth مُختبَر» إلى «تخزين دائم متعدد المستأجرين» (SPRINT-02)
- **الفرع المعتمد المستقر:** `main`
- **فرع التطوير المستمر:** `work/implementation`
- **المرجع المعرفي المعتمد:** مجلد `AI_Marketing_Platform_Hub/` (57 وثيقة تخصصية)
- **الميثاق المعماري الحاكم:** وثيقة `imaster_planner.md`

---

## 2. خط الأساس البرمجي المُتحقَّق منه (Verified Codebase Baseline)

> ⚠️ **تصحيح:** الأصول القديمة (`ult_agent_agent/…`, `first_lunch/…`, `whatsapp_ai_v2/…`) **ليست في هذا المستودع** وتبقى مرجعاً تصميمياً فقط. خط الأساس الحقيقي الآن هو كود SPRINT-01 أدناه.

| المكون | المسار | الحالة (تحقق المخطط 2026-10-29) |
|---|---|---|
| نواة FastAPI + إعدادات + تشفير Fernet | `src/core/` | ✅ 123 اختباراً، تغطية 99.62%، ruff نظيف |
| وحدة ربط Meta (OAuth، Graph client، أخطاء، مسارات) | `src/modules/social_connect/` | ✅ تعمل بـ Mock؛ **بلا تخزين** (يُعالَج في SPRINT-02) |
| محرك SQLAlchemy متزامن كسول | `src/core/database.py` | ⚠️ يُستبدل بمحرك async في SPRINT-02 |
| سكربت نسخ احتياطي | `scripts/backup_progress.sh` | ✅ مقبول |

**مرجع المراجعة الكاملة:** [`sprints/SPRINT-01_REVIEW.md`](./sprints/SPRINT-01_REVIEW.md)

---

## 3. المهمة النشطة الحالية (Active Task)

- **معرف المهمة:** `SPRINT-02-PERSISTENCE-AND-HARDENING` — Revision 1
- **وثيقة التوجيه المعتمدة (المصدر الوحيد):** [`CURRENT_DIRECTIVE.md`](./CURRENT_DIRECTIVE.md)
- **حالة المهمة:** `AWAITING_PLANNER_REVIEW` — نُفِّذ Part A (`c85c0bd`) و Part B (`96b1008`)؛ PR [#3](https://github.com/belalalibb/ai_agancy_markting/pull/3) مفتوح وغير مدموج؛ التقرير في قسم 11 من `CURRENT_DIRECTIVE.md`.
- **الفرع المستهدف للكود:** `work/implementation`
- **الهدف المختصر:**
  - **Part A (Hardening):** Graph API `v26.0`، صلاحيات قراءة فقط، إلغاء إعادة المحاولة على Rate Limit، `state` موقّع بـ HMAC، تصحيح ترتيب 401/403.
  - **Part B (Persistence):** Docker Compose (pgvector/pg16)، SQLAlchemy async + Alembic، جدولا `social_connections` و `social_connection_pages`، Repository Protocol + تنفيذ PostgreSQL، `GET /callback` يحفظ، مسارات قراءة/حذف بدون أي توكن.
- **خارج النطاق:** النشر، المصادقة، Redis، Temporal، Insights، أي منصة غير Meta، الواجهة.
- **السبرنت السابق:** `SPRINT-01-FASTAPI-META-FOUNDATION` — **مقبول** (PR #2) — مراجعته في `sprints/SPRINT-01_REVIEW.md`.

---

## 4. معايير القبول للمهمة الحالية (Acceptance Criteria)

المعايير الكاملة والملزمة في **قسم 7 من `CURRENT_DIRECTIVE.md`**. ملخصها:
- [ ] Part A في كوميت منفصل؛ `v19.0` مرفوض؛ Rate Limit بلا إعادة؛ `code 200 + OAuthException` ⇒ 403.
- [ ] `docker compose up -d` + `alembic upgrade head` ينجحان.
- [ ] `pytest -m "not integration"` بدون Docker ⇒ Zero Failures.
- [ ] `pytest -m integration` ضد PostgreSQL حقيقية ⇒ ≥ 8 ناجحة.
- [ ] التغطية الكلية ≥ 90%؛ ruff نظيف.
- [ ] لا `InMemory` في `src/`، لا `"***"`، لا حقل توكن في أي استجابة.
- [ ] اختبارات SPRINT-01 الـ 123 خضراء أو مُعدَّلة بوعي موثَّق.

---

## 5. الخطوة الدقيقة التالية (Exact Next Step)

| الترتيب | الدور | الخطوة |
|---|---|---|
| 1 | **المخطط** | عند *«راجع شغل المنفذ»*: مراجعة PR [#3](https://github.com/belalalibb/ai_agancy_markting/pull/3) فعلياً — `docker compose up -d` ⇒ `alembic upgrade head` ⇒ `pytest -m "not integration"` ⇒ `TEST_DATABASE_URL=… pytest -m integration` (على pg16) ⇒ ruff ⇒ `/docs`. |
| 2 | **المخطط** | الرد على الأسئلة المفتوحة الثلاثة في قسم 11، ثم إما قبول (دمج PR #3) أو ملاحظات في `sprints/SPRINT-02_REVIEW.md`. |
| 3 | **المالك** | دمج PR #3 بعد القبول، ثم أمر المنفذ بالتوجيه التالي. |
| 4 | **المنفذ** | ينتظر «تابع» أو تعليقات المراجعة؛ لا يبدأ عملاً جديداً قبل أن يكون توجيه المخطط مدموجاً في `main`. |

> **قاعدة إجرائية (من درس SPRINT-01/02):** بيئة المنفذ تُعاد تهيئتها بين الجلسات؛ **الدفع البعيد بعد كل نقطة** هو الضمان الوحيد. Docker غير متاح للمنفذ ⇒ اختبارات التكامل تُشغَّل محلياً على PostgreSQL مثبتة عبر apt.

---

## 6. سجل نقاط الاستئناف (Checkpoint Log)

> يُضاف سطر جديد في الأعلى عند كل نقطة مؤكدة. **لا تُحذف السطور القديمة.**

| التاريخ | الوكيل | الفرع | الكوميت | ما تم تأكيده | الخطوة التالية |
|---|---|---|---|---|---|
| 2026-10-29 | المنفذ | `work/implementation` → PR #3 | `c85c0bd` (Part A) / `96b1008` (Part B) | SPRINT-02 مُسلَّم: 181 معزولاً + 8 تكامل = 189 ناجحاً، تغطية 99%، ruff نظيف، `/docs` يعرض المسارات الخمسة + `/health` فقط، لا `InMemory`/`"***"`/أسرار في `src`. التكامل على PostgreSQL 17 محلية (لا Docker في البيئة). انقطاع الجلسة ×3 مجدداً؛ الدفع البعيد أنقذ كل شيء. | مراجعة المخطط لـ PR #3. |
| 2026-10-29 | المخطط | `genspark_ai_developer` → PR #1 | `31b2d36` → PR [#1](https://github.com/belalalibb/ai_agancy_markting/pull/1) | مراجعة مستقلة لـ SPRINT-01: 123 passed / 99.62% / ruff نظيف / `/docs` يعمل — **مقبول**. 4 ثغرات حرجة (v19.0، state غير مُتحقَّق، صلاحيات نشر، إعادة محاولة على Rate Limit) رُحِّلت إلى SPRINT-02 Part A. أُصدر توجيه SPRINT-02 (481 سطراً، 18 اختباراً معزولاً + 8 تكامل). أُنشئ أرشيف `sprints/`. | المالك يدمج PR #2 ثم PR #1 ثم يطلق المنفذ. |
| 2026-10-29 | المنفذ | `work/implementation` → PR #2 | `118c0bb` (كود) / `ed5fb47` (تقرير) | SPRINT-01 مُسلَّم: 123 اختباراً، 99.6%. انقطاع الجلسة ×3؛ الدفع البعيد بعد كل نقطة هو ما حفظ العمل (النسخ المحلية و AI Drive لم تنجُ). | مراجعة المخطط. |
| 2026-10-29 | المخطط | `genspark_ai_developer` | `d8e502a` | جرد المستودع + توجيه SPRINT-01 Rev 2 (لم يصل للمنفذ لأن PR #1 لم يُدمج قبل إطلاقه). | — |
| 2026-10-29 | المالك/الإعداد | `main` | `555768d` | تهيئة المستودع: Hub + الميثاق + ملفات التحكم + توجيه Rev 1. | — |
