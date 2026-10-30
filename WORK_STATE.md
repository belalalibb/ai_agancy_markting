# WORK STATE & EXECUTION POINTER

## سجل حالة العمل ومؤشر التنفيذ النشط

> **الغرض:** نقطة الحقيقة اللحظية لأي وكيل ذكاء اصطناعي أو مهندس يعمل على المستودع. يُسجل بدقة المهمة الحالية، والفرع النشط، وآخر فحص، والخطوة التالية.

---

## 1. ملخص الحالة العامة للمشروع (Project Snapshot)

- **اسم المشروع:** AI Marketing Platform (AI Growth OS / BrandMind AI / QEVION)
- **الحالة الإجمالية:** `SPRINT_04_ACCEPTED_CONDITIONAL__HOTFIX_04_1_ISSUED`
- **آخر تحديث للمؤشر:** 2026-10-30 — المخطط راجع **SPRINT-04** مستقلاً (PG 17.11؛ 434 اختباراً، 99%؛ CI خضراء) **وتحقق بالسطر من 8 ملاحظات رقابية خارجية — كلها ثابتة** (اثنتان P0 موروثتان من SPRINT-03: تسريب صفحات عملاء الوكالة/اختطاف الاختيار، وأسرار Graph في query params تُسجَّل في `httpx` — أُثبتت تجريبياً). القرار: **قبول مشروط** ⇒ PR #11 دُمج (`6f263ac`) لأنه يُغلق فجوة الهوية ولا يُدخل الـP0، **ولا بناء فوقه قبل `SPRINT-04.1-HOTFIX`** (H1–H8).
- **المرحلة الحالية:** Phase 1 — تصحيح أمني إلزامي (SPRINT-04.1) قبل أي ميزة: عزل بيانات عملاء الوكالة، أسرار Graph خارج الروابط، كوكي Refresh صالح للمتصفحات
- **الفرع المعتمد المستقر:** `main`
- **فرع التطوير المستمر:** `work/implementation`
- **المرجع المعرفي المعتمد:** مجلد `AI_Marketing_Platform_Hub/` (57 وثيقة تخصصية)
- **الميثاق المعماري الحاكم:** وثيقة `imaster_planner.md`

---

## 2. خط الأساس البرمجي المُتحقَّق منه (Verified Codebase Baseline)

> ⚠️ **تصحيح:** الأصول القديمة (`ult_agent_agent/…`, `first_lunch/…`, `whatsapp_ai_v2/…`) **ليست في هذا المستودع** وتبقى مرجعاً تصميمياً فقط. خط الأساس الحقيقي الآن هو كود SPRINT-01 أدناه.

| المكون | المسار | الحالة (تحقق المخطط 2026-10-30، `main` @ `c88a721`) |
|---|---|---|
| نواة FastAPI + إعدادات + MultiFernet (D-058) + `state` موقَّع `{t,b,n,iat}` + `/health/ready` عبر `engine.connect()` | `src/core/`, `src/main.py` | ✅ 315 اختباراً، تغطية 99.68% |
| وحدة Tenancy: `tenants` / `brands` / `brand_access_grants`، `BrandAccessResolver` (404/403)، سحب المنحة يمسح اتصالات الوكالة | `src/modules/tenancy/` | ✅ جديد في SPRINT-03؛ **الفاعل `?tenant_id=` مُدَّعى ذاتياً ⇒ يُستبدل بـ JWT في SPRINT-04** |
| وحدة ربط Meta بنطاق العلامة (`/brands/{brand_id}/social/meta/…`)، اختيار الصفحات صراحةً، حارس `provider_user_name` | `src/modules/social_connect/` | ✅ 17 اختبار تكامل على PG حقيقية |
| هجرات Alembic async | `alembic/versions/0001`–`0003` | ✅ 0003 مُختبَرة بـ backfill على بيانات قديمة حدية (تصادم slug، عربي، قصير) صعوداً ونزولاً |
| CI: `lint` / `unit` / `integration` (pgvector:pg16) / `secrets` (gitleaks) / `licenses` (D-054) | `.github/workflows/ci.yml`, `scripts/check_licenses.py` | ✅ خمسة فحوص منفصلة خضراء على `main` |
| سكربت نسخ احتياطي | `scripts/backup_progress.sh` | ✅ |

**مراجع المراجعات:** [`SPRINT-01`](./sprints/SPRINT-01_REVIEW.md) · [`SPRINT-02`](./sprints/SPRINT-02_REVIEW.md) · [`SPRINT-02.1`](./sprints/SPRINT-02.1_REVIEW.md) · [`SPRINT-03`](./sprints/SPRINT-03_REVIEW.md)

---

## 3. المهمة النشطة الحالية (Active Task)

- **معرف المهمة:** `SPRINT-04.1-HOTFIX`
- **وثيقة التوجيه المعتمدة (المصدر الوحيد):** [`CURRENT_DIRECTIVE.md`](./CURRENT_DIRECTIVE.md)
- **حالة المهمة:** `READY_FOR_EXECUTION` — تبدأ من `main` (التوجيه مدموج)؛ المنفذ يفتح **PR #12**.
- **الهدف:** إغلاق 8 ثغرات مُثبتة بالسطر — **Part A (موروث من 03):** H1 عزل صفحات الوكالة + ملكية الاتصال (`connected_by_tenant_id`) 🔴P0؛ H2 أسرار Graph من `params` إلى `Authorization: Bearer`/POST form + كبح `httpx` + مرشّح تنقيح + لا `{exc}` في 502 🔴P0؛ H3 `set_status(REAUTH_REQUIRED)` خارج معاملة الطلب + صفحة بلا توكن ⇒ 422؛ H4 فحص https/مضيف لـ`paging.next` + `ON CONFLICT DO NOTHING RETURNING` بدل 500. **Part B (عيوب 04):** H5 `__Secure-refresh` بدل `__Host-` (يكسر الدخول في المتصفحات) 🔴؛ H6 `CurrentUserFresh` يفحص `expires_at` + `users.is_active` و`GET /auth/me` عليه؛ H7 إبطال قبل إصدار + `rowcount == 1`؛ H8 سقف ذاكرة للمحدِّد.
- **خارج النطاق:** أي ميزة. Redis. تغيير مخطط البيانات.
- **السبرنت السابق:** `SPRINT-04-AUTH-AND-ACTOR-IDENTITY` — **مقبول مشروطاً ودُمج** (PR #11 ⇒ `6f263ac`) — [`sprints/SPRINT-04_REVIEW.md`](./sprints/SPRINT-04_REVIEW.md).
- **ردود المخطط على أسئلة المنفذ (04):** نموذج `start` الفارغ يبقى (`extra="forbid"`)؛ `GET /auth/me` ⇒ `CurrentUserFresh`؛ Redis خلف `RateLimiter` قرار مؤجل (Hub)؛ تدوير التوكن مرفوع للمالك (الطلب الثالث).
- **درس للمخطط (مُسجَّل):** S03-1 وS03-2 ثغرتا **توجيه** لا تنفيذ — التوجيه القادم لأي تكامل خارجي يجب أن ينص على: (أ) أين تُرسل الأسرار (رؤوس/جسم، لا روابط)، (ب) من يرى ماذا بدقة الحقل.

---

## 4. معايير القبول للمهمة الحالية (Acceptance Criteria)

الكاملة في **§3 من `CURRENT_DIRECTIVE.md`**. ملخصها:
- [ ] CI خضراء على PR #12؛ 0 فشل؛ تغطية ≥ 95%؛ ruff نظيف.
- [ ] لا سر Graph في `params=` (أسطر `headers=`/`data=` مذكورة في §6)؛ تجربة `httpx` INFO تُظهر `client_secret in log: False`.
- [ ] `grep "__Host-" src` فارغ؛ حارس H5-U1 موجود.
- [ ] H1-I1, H3-I1, H4-I1, H6-I1, H7-I1 موجودة وناجحة على PG.
- [ ] `grep 'f".*{exc}' meta_client.py` فارغ.
- [ ] كوميتان فوق `main`؛ لا ملف محظور مُسّ.

---

## 5. الخطوة الدقيقة التالية (Exact Next Step)

| الترتيب | الدور | الخطوة |
|---|---|---|
| 1 | **المالك** | 🔑 **تدوير توكن GitHub (الطلب الثالث — إلزامي قبل SPRINT-05)**. 🛡️ حماية `main` بالفحوص الخمسة. |
| 2 | **المالك** | يأمر المنفذ: *«نفذ التوجيه الموجود في الريبو»*. |
| 3 | **المنفذ** | `merge --ff-only origin/main` ⇒ Part A (H1–H4) كوميت + دفع + PR #12 ⇒ Part B (H5–H8) كوميت + دفع ⇒ §6 ⇒ هذا الملف ⇒ *«PR #12 جاهز لمراجعة المخطط»*. |
| 4 | **المخطط** | مراجعة 04.1 على PG حقيقية + **إعادة تجربة تسريب `httpx` بنفسه** + سباق H7-I1 ⇒ يدمج PR #12 بنفسه ⇒ يحدّث `SECURITY_BASELINE` §2 و`KNOWN_ISSUES` ⇒ يكتب SPRINT-05 ويدمج توجيهه. |

> **ثلاث قواعد:** (1) لا يبدأ المنفذ قبل دمج توجيه المخطط في `main`. (2) المخطط يفرّع `genspark_ai_developer` من `main` **فقط**. (3) **المالك لا يدمج يدوياً** — المخطط يدمج توجيهاته فوراً وPRs المنفذ بعد الاعتماد؛ المنفذ لا يدمج أبداً.

---

## 6. سجل نقاط الاستئناف (Checkpoint Log)

> يُضاف سطر جديد في الأعلى عند كل نقطة مؤكدة. **لا تُحذف السطور القديمة.**

| التاريخ | الوكيل | الفرع | الكوميت | ما تم تأكيده | الخطوة التالية |
|---|---|---|---|---|---|
| 2026-10-30 | المخطط | `genspark_ai_developer` → PR #13 | *(يُحدَّث بعد الدفع)* | مراجعة SPRINT-04 @ `0db7828`: 434 اختباراً/99%/CI خضراء/greps نظيفة ⇒ **8 ملاحظات رقابية خارجية تُحقق منها المخطط بالسطر — كلها ثابتة**؛ S03-2 أُثبتت تجريبياً (سجل `httpx` INFO طبع `client_secret=` و`access_token=`). **قبول مشروط** ⇒ PR #11 دُمج `6f263ac`. أُصدر **SPRINT-04.1-HOTFIX** (H1–H8، كوميتان، لا ميزات) مع إقرار أن S03-1/S03-2 ثغرتا توجيه المخطط. | المالك: تدوير التوكن + إطلاق المنفذ ⇒ PR #12. |
| 2026-10-30 | المنفذ | `work/implementation` → PR [#11](https://github.com/belalalibb/ai_agancy_markting/pull/11) | `b2bd3ad` (Part A) / `b0a4ccf` (Part B) / كوميت التوثيق فوقهما | SPRINT-04 مُسلَّم. **Part A (auth):** هجرة `0004` (`users` بـ `uq_users_email_lower` + `tenant_id` UNIQUE؛ `refresh_sessions` بـ `token_hash` sha256 و`replaced_by` و`ip INET`)، Argon2id خلف `PasswordHasher` Protocol مع `DUMMY_HASH` وإعادة التجزئة، JWT HS256 (`sub/tid/sid/iat/exp=iat+900/iss/aud=api/jti`، `algorithms=["HS256"]`، سر ≥32 بايت إلزامي خارج development)، كوكي `__Host-refresh` HttpOnly SameSite=Strict Path=/api/v1/auth مع دوران وكشف إعادة الاستخدام (إبطال كل الجلسات)، CSRF، محدِّد معدل in-memory خلف `RateLimiter` Protocol، مسارات `register/login/refresh/logout/logout-all/me`، `bearerAuth` في OpenAPI، `CurrentUser`/`CurrentUserFresh`. **Part B (الهوية):** `ActorTenantId` من JWT، حذف `tenant_id` من كل الأجسام/الاستعلامات، حذف `POST /tenants`، `CurrentUserFresh` على المسارات الهدّامة، claim `u` في state، حارس B-U1 على **15** مساراً، حارس Zero-Secret موسَّع. **الأرقام:** 413 + 21 = **434 ناجحاً**، 0 فشل، **99%**، ruff نظيف، تراخيص 39/0، 0004 صعود/نزول/صعود على PG 17. CI خضراء على `b0a4ccf` (push + PR). ساندبوكس أُعيد ≥15 مرة؛ gitleaks أجبر على إعادة بناء Part A. 12 انحرافاً + 4 أسئلة في §7. | مراجعة المخطط لـ PR #11 ⇒ دمجه ⇒ المالك يدوّر التوكن ⇒ SPRINT-05. |
| 2026-10-30 | المخطط | `genspark_ai_developer` → PR #10 | `76ab3d8` | دورة انتظار (المنفذ لم يسلّم SPRINT-04 بعد؛ `work/implementation` @ `2b1e1fe`). أُنجز البند المؤجل على المخطط: **مزامنة Hub docs** — `DECISION_LOG` (D-056…D-063)، `SECURITY_BASELINE` (استثناء D-058، مواصفة JWT/Refresh، جدول حالة التنفيذ)، `DATA_AND_INTEGRATIONS` (المخطط الفعلي حتى 0003)، `DEFERRED_DECISIONS` (tenant_members، التفويض الفرعي، AES-256-GCM)، `CHANGELOG` (المحطة 7). `verify_hub.py` ناجح. | المالك: تدوير التوكن + إطلاق المنفذ على SPRINT-04. |
| 2026-10-30 | المخطط | `genspark_ai_developer` → PR #9-docs | `ed565c3` | PR #8 **دُمج بواسطة المخطط** (`2851c9f`) بناءً على قرار المالك: **لا دمج يدوي من المالك بعد الآن**. القاعدة ثُبِّتت في `CURRENT_DIRECTIVE.md` (بطاقة المهمة، §6/4، §8) و§5 هنا. SPRINT-04 أصبح قابلاً للتنفيذ فوراً. | المالك: تدوير التوكن + إطلاق المنفذ. المخطط يدمج هذا الـPR بنفسه. |
| 2026-10-30 | المخطط | `genspark_ai_developer` (من `main` @ `c88a721`) → PR #8 | `60383f6` → PR [#8](https://github.com/belalalibb/ai_agancy_markting/pull/8) | مراجعة مستقلة لـ SPRINT-03 @ `2b1e1fe` في بيئة طازجة (PG 17.11 أُعيد تثبيتها): 298 وحدة + 17 تكامل = **315 ناجحاً، 99.68%**؛ CI الفحوص الخمسة `success` (Checks API)؛ هجرة 0003 مُجرَّبة بحالات حدية (تصادم slug `Acme Co`/`acme-co`، `ab`، `عميل`، نفس حساب Meta في مستأجرين) صعوداً/نزولاً/صعوداً؛ OpenAPI 13 مساراً كلها بنطاق العلامة، `has_page_token` الحقل الوحيد بـ token؛ بوابة التراخيص exit 0 (34 حزمة) ⇒ **مقبول** ⇒ PR #7 دُمج `c88a721`. أُصدر توجيه **SPRINT-04** (auth: Argon2id + HS256 JWT 15m + Refresh دوّار HttpOnly + كشف إعادة الاستخدام + CSRF + rate limit؛ استبدال `ActorTenantId`). | المالك يدمج PR #8 ⇒ يدوّر التوكن ⇒ يطلق المنفذ ⇒ PR #9. |
| 2026-10-30 | المنفذ | `work/implementation` → PR [#7](https://github.com/belalalibb/ai_agancy_markting/pull/7) | `a985d55` (Part A) / `b712df0` (Part B) / كوميت التوثيق فوقهما | SPRINT-03 مُسلَّم. **Part A:** `.github/workflows/ci.yml` بخمس وظائف منفصلة (lint / unit cov≥95 / integration على `pgvector/pgvector:pg16` / secrets gitleaks مع `GITHUB_TOKEN` + `pull-requests: read` / licenses D-054 عبر `scripts/check_licenses.py` في venv نظيف)، `/health/ready` عبر `engine.connect()`، `alembic.ini path_separator=os`. **Part B:** وحدة `src/modules/tenancy/` (tenants / brands / brand_access_grants، `BrandAccessResolver`، أدوار owner/manager/viewer، 8 مسارات)، هجرة `0003` بـbackfill خام + تنظيف التكرارات عند النزول (ملاحظة المالك 3)، `OAuthStateSigner` بزوج UUID (tenant, brand)، مسارات Meta تحت `/brands/{brand_id}/social/meta/…`، اختيار الصفحات الصريح (B6 + ملاحظة المالك 2: إعادة الربط تحفظ المختار وتحدّث توكنه) + CHECK في DB، حارس B7 (لا تسجيل للاسم/المعرف؛ حذف عند disconnect وسحب المنحة B8). **الأرقام:** 298 وحدة + 17 تكامل = 315 ناجحاً، 0 فشل، تغطية 99.68%، ruff نظيف، تراخيص 37/0 مرفوضة، PG 17.11 محلياً. CI خضراء على `b712df0` (push + PR). إعادة تهيئة الساندبوكس ×7؛ الدفع بعد كل نقطة أنقذ العمل مجدداً؛ كوميتات WIP سُحقت في `b712df0`. تفاصيل الانحرافات (8) والأسئلة (3) في §7. | مراجعة المخطط لـ PR #7 ⇒ المالك يفعّل حماية `main` ويدمج ⇒ تدوير التوكن ⇒ SPRINT-04. |
| 2026-10-29 | المخطط | `genspark_ai_developer` (من `main` @ `df01260`) → PR #6 | `c2175a3` (+ كوميت تسجيل SHA) → PR [#6](https://github.com/belalalibb/ai_agancy_markting/pull/6) | مراجعة مستقلة لـ SPRINT-02.1 @ `4480309` في بيئة طازجة (PG 17.11 أُعيد تثبيتها): 204 وحدة + 11 تكامل = **215 ناجحاً، 99%**، F1 0/50 فشل، هجرة 0002 صعود/نزول على PG، `/health/ready` حيّاً 200/503/503 بلا تسريب، تراخيص شجرة التشغيل نظيفة (36 حزمة) ⇒ **مقبول** ⇒ فُتح PR #5 ودُمج في `main` @ `df01260` بتفويض المالك. طُبّقت قرارات المالك: `ARCHITECTURE_LOCK` (D-056/D-057 ترقيم، D-058 استثناء Fernet، الهوامش ⇒ فرضيات)، حُذف `Hub/imaster_planner.md`. أُصدر توجيه **SPRINT-03** (CI Gates + Tenancy + اختيار الصفحات + هجرة 0003). | المالك يدمج PR #6 ⇒ يطلق المنفذ ⇒ PR #7. |
| 2026-10-29 | المنفذ | `work/implementation` | `7cf29df` | SPRINT-02.1-HOTFIX مُسلَّم في كوميت واحد: الهوية من `GET /me` (إلزامي) + فحص `is_valid`/`app_id`/`user_id` من `/debug_token` (`MetaTokenInvalidError`/`MetaTokenForeignAppError` ⇒ 401 reconnect) + حذف `_fallback_identity` واختباريه + `encrypted_page_token` NULLable (هجرة 0002 صعوداً/نزولاً مع صفوف NULL) + `has_page_token` + `engine.dispose()` مشروط + `/health/ready` (200/503 حيّاً بلا تسريب DSN) + سقف ترقيم 50 + إصلاح F1 (50/50). 204 معزولاً + 11 تكامل = 215، تغطية 99%، ruff نظيف، PostgreSQL 17.11 محلية. انقطاع الجلسة ×3؛ الدفع بعد كل نقطة أنقذ العمل مجدداً. ⚠️ PR #3 كان مغلقاً (merged عبر PR #4) فلم يتحدث؛ لم يُفتح PR جديد. | مراجعة المخطط لـ `7cf29df`؛ قرار PR جديد؛ رد المالك على §6 من مراجعة SPRINT-02. |
| 2026-10-29 | المخطط | `genspark_ai_developer` → PR #4 | `95d5223` → PR [#4](https://github.com/belalalibb/ai_agancy_markting/pull/4) | مراجعة مستقلة لـ SPRINT-02 على PostgreSQL 17.11+pgvector مثبّتة في بيئة المراجعة: 189 passed / 8 تكامل / 99.56% / ruff نظيف — **لكن** اختبار متذبذب (F1، 5.7%) + 8 عيوب كود مُثبتة بالسطر (أخطرها: `/debug_token` بلا فحص `is_valid`، `_fallback_identity` يسحق السجلات). تقرير المراقب الخارجي تُحقق منه: 6/6 صحيح في الكود. قرار Tenant/Brand رُفع للمالك. أُصدر `SPRINT-02.1-HOTFIX`. | المالك يدمج PR #4 ⇒ المنفذ ينفذ 02.1 ⇒ المالك يرد على §6. |
| 2026-10-29 | المنفذ | `work/implementation` → PR #3 | `c85c0bd` (Part A) / `96b1008` (Part B) | SPRINT-02 مُسلَّم: 181 معزولاً + 8 تكامل = 189 ناجحاً، تغطية 99%، ruff نظيف، `/docs` يعرض المسارات الخمسة + `/health` فقط، لا `InMemory`/`"***"`/أسرار في `src`. التكامل على PostgreSQL 17 محلية (لا Docker في البيئة). انقطاع الجلسة ×3 مجدداً؛ الدفع البعيد أنقذ كل شيء. | مراجعة المخطط لـ PR #3. |
| 2026-10-29 | المخطط | `genspark_ai_developer` → PR #1 | `31b2d36` → PR [#1](https://github.com/belalalibb/ai_agancy_markting/pull/1) | مراجعة مستقلة لـ SPRINT-01: 123 passed / 99.62% / ruff نظيف / `/docs` يعمل — **مقبول**. 4 ثغرات حرجة (v19.0، state غير مُتحقَّق، صلاحيات نشر، إعادة محاولة على Rate Limit) رُحِّلت إلى SPRINT-02 Part A. أُصدر توجيه SPRINT-02 (481 سطراً، 18 اختباراً معزولاً + 8 تكامل). أُنشئ أرشيف `sprints/`. | المالك يدمج PR #2 ثم PR #1 ثم يطلق المنفذ. |
| 2026-10-29 | المنفذ | `work/implementation` → PR #2 | `118c0bb` (كود) / `ed5fb47` (تقرير) | SPRINT-01 مُسلَّم: 123 اختباراً، 99.6%. انقطاع الجلسة ×3؛ الدفع البعيد بعد كل نقطة هو ما حفظ العمل (النسخ المحلية و AI Drive لم تنجُ). | مراجعة المخطط. |
| 2026-10-29 | المخطط | `genspark_ai_developer` | `d8e502a` | جرد المستودع + توجيه SPRINT-01 Rev 2 (لم يصل للمنفذ لأن PR #1 لم يُدمج قبل إطلاقه). | — |
| 2026-10-29 | المالك/الإعداد | `main` | `555768d` | تهيئة المستودع: Hub + الميثاق + ملفات التحكم + توجيه Rev 1. | — |
