# WORK STATE & EXECUTION POINTER

## سجل حالة العمل ومؤشر التنفيذ النشط

> **الغرض:** نقطة الحقيقة اللحظية لأي وكيل ذكاء اصطناعي أو مهندس يعمل على المستودع. يُسجل بدقة المهمة الحالية، والفرع النشط، وآخر فحص، والخطوة التالية.

---

## 1. ملخص الحالة العامة للمشروع (Project Snapshot)

- **اسم المشروع:** AI Marketing Platform (AI Growth OS / BrandMind AI / QEVION)
- **الحالة الإجمالية:** `SPRINT_02_1_DELIVERED_AWAITING_REVIEW`
- **آخر تحديث للمؤشر:** 2026-10-29 — المنفذ سلّم SPRINT-02.1-HOTFIX (F1–F8) في كوميت واحد `7cf29df` على `work/implementation`: 215 اختباراً (204 + 11 تكامل)، تغطية 99%، ruff نظيف؛ بانتظار مراجعة المخطط. قرار Tenant/Brand ما زال معلّقاً على المالك
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

- **معرف المهمة:** `SPRINT-02.1-HOTFIX`
- **وثيقة التوجيه المعتمدة (المصدر الوحيد):** [`CURRENT_DIRECTIVE.md`](./CURRENT_DIRECTIVE.md)
- **حالة المهمة:** `DELIVERED_AWAITING_REVIEW` — مُنفَّذة على `work/implementation` @ `7cf29df` (فوق `59363e2`). ⚠️ PR #3 أُغلق تلقائياً (merged) عند دمج PR #4، فلم يعد يتحدث بالدفع؛ أُضيف تعليق ملخص عليه، ولم يُفتح PR جديد التزاماً بالتوجيه — **قرار فتح PR لـ 02.1 للمالك/المخطط**. تقرير المنفذ في `CURRENT_DIRECTIVE.md` §7.
- **الهدف:** إصلاح 8 عيوب مُثبتة في SPRINT-02 (F1–F8): اختبار متذبذب، فحص `is_valid`/`app_id` في `/debug_token`، الهوية من `GET /me` بدل `_fallback_identity`، `encrypted_page_token` NULLable (+هجرة 0002)، `engine.dispose()`, `/health/ready`, سقف الترقيم.
- **خارج النطاق:** Tenant/Brand، CI، تغيير التشفير، أي ميزة جديدة.
- **السبرنت السابق:** `SPRINT-02-PERSISTENCE-AND-HARDENING` — **مقبول مشروطاً** — مراجعته في [`sprints/SPRINT-02_REVIEW.md`](./sprints/SPRINT-02_REVIEW.md).
- **قرارات معلّقة على المالك (تحجب SPRINT-03):** انظر `sprints/SPRINT-02_REVIEW.md` §6 — أهمها **نموذج Tenant/Brand** (التوصية: الخيار ب).

---

## 4. معايير القبول للمهمة الحالية (Acceptance Criteria)

الكاملة في **§4 من `CURRENT_DIRECTIVE.md`**. ملخصها:
- [ ] `test_oauth_state_tampered_rejected` ينجح 50/50 متتالية.
- [ ] `grep _fallback_identity src tests` ⇒ لا نتائج؛ `is_valid` يُفحص في الخدمة.
- [ ] `/health/ready` ⇒ 200 مع Postgres، 503 بدونها.
- [ ] هجرة `0002` تصعد وتنزل مع صفوف NULL.
- [ ] كل الاختبارات خضراء (معزولة + تكامل)، تغطية ≥ 95%، ruff نظيف.
- [ ] كوميت واحد على `work/implementation`.

---

## 5. الخطوة الدقيقة التالية (Exact Next Step)

| الترتيب | الدور | الخطوة |
|---|---|---|
| 1 | **المالك** | دمج **PR #4** (`genspark_ai_developer` → `main`): مراجعة SPRINT-02 + توجيه SPRINT-02.1 + هذا الملف. |
| 2 | **المالك** | يأمر المنفذ: *«نفذ التوجيه الموجود في الريبو»*. |
| 3 | **المنفذ** | ✅ **تم** — F1–F8 في كوميت واحد `7cf29df` على `work/implementation`، §7 مملوء، تعليق على PR #3 (المغلق). |
| 3b | **المالك/المخطط** | تقرير: هل يُفتح PR جديد `work/implementation` → `main` لـ 02.1 أم يُراجَع/يُدمج الفرع مباشرة؟ |
| 4 | **المالك** | يرد على القرارات الأربعة في `sprints/SPRINT-02_REVIEW.md` §6 (يمكن بالتوازي مع خطوة 3). |
| 5 | **المخطط** | عند *«راجع شغل المنفذ»*: مراجعة 02.1 (`7cf29df`) ⇒ اعتماد الدمج ⇒ كتابة SPRINT-03 (Tenant/Brand + CI + تنظيف) **إن كانت قرارات §6 قد صدرت**. |

> **قاعدة:** لا يبدأ المنفذ قبل دمج توجيه المخطط في `main`.

---

## 6. سجل نقاط الاستئناف (Checkpoint Log)

> يُضاف سطر جديد في الأعلى عند كل نقطة مؤكدة. **لا تُحذف السطور القديمة.**

| التاريخ | الوكيل | الفرع | الكوميت | ما تم تأكيده | الخطوة التالية |
|---|---|---|---|---|---|
| 2026-10-29 | المنفذ | `work/implementation` | `7cf29df` | SPRINT-02.1-HOTFIX مُسلَّم في كوميت واحد: الهوية من `GET /me` (إلزامي) + فحص `is_valid`/`app_id`/`user_id` من `/debug_token` (`MetaTokenInvalidError`/`MetaTokenForeignAppError` ⇒ 401 reconnect) + حذف `_fallback_identity` واختباريه + `encrypted_page_token` NULLable (هجرة 0002 صعوداً/نزولاً مع صفوف NULL) + `has_page_token` + `engine.dispose()` مشروط + `/health/ready` (200/503 حيّاً بلا تسريب DSN) + سقف ترقيم 50 + إصلاح F1 (50/50). 204 معزولاً + 11 تكامل = 215، تغطية 99%، ruff نظيف، PostgreSQL 17.11 محلية. انقطاع الجلسة ×3؛ الدفع بعد كل نقطة أنقذ العمل مجدداً. ⚠️ PR #3 كان مغلقاً (merged عبر PR #4) فلم يتحدث؛ لم يُفتح PR جديد. | مراجعة المخطط لـ `7cf29df`؛ قرار PR جديد؛ رد المالك على §6 من مراجعة SPRINT-02. |
| 2026-10-29 | المخطط | `genspark_ai_developer` → PR #4 | `95d5223` → PR [#4](https://github.com/belalalibb/ai_agancy_markting/pull/4) | مراجعة مستقلة لـ SPRINT-02 على PostgreSQL 17.11+pgvector مثبّتة في بيئة المراجعة: 189 passed / 8 تكامل / 99.56% / ruff نظيف — **لكن** اختبار متذبذب (F1، 5.7%) + 8 عيوب كود مُثبتة بالسطر (أخطرها: `/debug_token` بلا فحص `is_valid`، `_fallback_identity` يسحق السجلات). تقرير المراقب الخارجي تُحقق منه: 6/6 صحيح في الكود. قرار Tenant/Brand رُفع للمالك. أُصدر `SPRINT-02.1-HOTFIX`. | المالك يدمج PR #4 ⇒ المنفذ ينفذ 02.1 ⇒ المالك يرد على §6. |
| 2026-10-29 | المنفذ | `work/implementation` → PR #3 | `c85c0bd` (Part A) / `96b1008` (Part B) | SPRINT-02 مُسلَّم: 181 معزولاً + 8 تكامل = 189 ناجحاً، تغطية 99%، ruff نظيف، `/docs` يعرض المسارات الخمسة + `/health` فقط، لا `InMemory`/`"***"`/أسرار في `src`. التكامل على PostgreSQL 17 محلية (لا Docker في البيئة). انقطاع الجلسة ×3 مجدداً؛ الدفع البعيد أنقذ كل شيء. | مراجعة المخطط لـ PR #3. |
| 2026-10-29 | المخطط | `genspark_ai_developer` → PR #1 | `31b2d36` → PR [#1](https://github.com/belalalibb/ai_agancy_markting/pull/1) | مراجعة مستقلة لـ SPRINT-01: 123 passed / 99.62% / ruff نظيف / `/docs` يعمل — **مقبول**. 4 ثغرات حرجة (v19.0، state غير مُتحقَّق، صلاحيات نشر، إعادة محاولة على Rate Limit) رُحِّلت إلى SPRINT-02 Part A. أُصدر توجيه SPRINT-02 (481 سطراً، 18 اختباراً معزولاً + 8 تكامل). أُنشئ أرشيف `sprints/`. | المالك يدمج PR #2 ثم PR #1 ثم يطلق المنفذ. |
| 2026-10-29 | المنفذ | `work/implementation` → PR #2 | `118c0bb` (كود) / `ed5fb47` (تقرير) | SPRINT-01 مُسلَّم: 123 اختباراً، 99.6%. انقطاع الجلسة ×3؛ الدفع البعيد بعد كل نقطة هو ما حفظ العمل (النسخ المحلية و AI Drive لم تنجُ). | مراجعة المخطط. |
| 2026-10-29 | المخطط | `genspark_ai_developer` | `d8e502a` | جرد المستودع + توجيه SPRINT-01 Rev 2 (لم يصل للمنفذ لأن PR #1 لم يُدمج قبل إطلاقه). | — |
| 2026-10-29 | المالك/الإعداد | `main` | `555768d` | تهيئة المستودع: Hub + الميثاق + ملفات التحكم + توجيه Rev 1. | — |
