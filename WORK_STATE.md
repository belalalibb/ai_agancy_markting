# WORK STATE & EXECUTION POINTER

## سجل حالة العمل ومؤشر التنفيذ النشط

> **الغرض:** نقطة الحقيقة اللحظية لأي وكيل ذكاء اصطناعي أو مهندس يعمل على المستودع. يُسجل بدقة المهمة الحالية، والفرع النشط، وآخر فحص، والخطوة التالية.

---

## 1. ملخص الحالة العامة للمشروع (Project Snapshot)

- **اسم المشروع:** AI Marketing Platform (AI Growth OS / BrandMind AI / QEVION)
- **الحالة الإجمالية:** `SPRINT_04_1_ACCEPTED__SPRINT_05_ISSUED`
- **آخر تحديث للمؤشر:** 2026-10-30 — المخطط راجع **SPRINT-04.1-HOTFIX** مستقلاً (مرتين؛ الثانية على `main` @ `32c20cb` نفسه بعد أن دمج المالك PR #13 يدوياً): **474 اختباراً / 99.57%** على PG 17، ruff نظيف، تجربة تسريب `httpx` ⇒ لا سر من خمسة في السجل ولا في أي رابط، سباق H7-I1 3/3 `[200,401]`؛ H1–H8 مُتحقَّق منها بالسطر ⇒ **مقبول** ([`sprints/SPRINT-04.1_REVIEW.md`](./sprints/SPRINT-04.1_REVIEW.md)). قرارات: H7 صارم (0 جلسات) يُعتمد؛ H1-3 الاختيار يُحفظ عند نقل الملكية. **SPRINT-05** صادر في `CURRENT_DIRECTIVE.md`.
- **المرحلة الحالية:** Phase 1 — SPRINT-05 (`SPRINT-05-META-OPERATIONAL-READS`) **صادر وجاهز للتنفيذ**
- **الفرع المعتمد المستقر:** `main`
- **فرع التطوير المستمر:** `work/implementation`
- **المرجع المعرفي المعتمد:** مجلد `AI_Marketing_Platform_Hub/` (57 وثيقة تخصصية)
- **الميثاق المعماري الحاكم:** وثيقة `imaster_planner.md`

---

## 2. خط الأساس البرمجي المُتحقَّق منه (Verified Codebase Baseline)

> ⚠️ **تصحيح:** الأصول القديمة (`ult_agent_agent/…`, `first_lunch/…`, `whatsapp_ai_v2/…`) **ليست في هذا المستودع** وتبقى مرجعاً تصميمياً فقط. خط الأساس الحقيقي الآن هو كود SPRINT-01 أدناه.

| المكون | المسار | الحالة (تحقق المخطط 2026-10-30، `main` @ `32c20cb`) |
|---|---|---|
| نواة FastAPI + إعدادات + MultiFernet (D-058) + `state` موقَّع `{t,b,n,iat}` + `/health/ready` عبر `engine.connect()` | `src/core/`, `src/main.py` | ✅ **474 اختباراً، تغطية 99.57%** (الحزمة كاملة)؛ `src/core/logging.py` مرشّح تنقيح الأسرار على الجذر + `httpx`/`httpcore` على WARNING (H2) |
| وحدة Tenancy: `tenants` / `brands` / `brand_access_grants`، `BrandAccessResolver` (404/403)، سحب المنحة يمسح اتصالات الوكالة | `src/modules/tenancy/` | ✅ الفاعل من JWT (`ActorTenantId`) منذ SPRINT-04؛ `ON CONFLICT` ⇒ 409 حتمي (H4) |
| وحدة ربط Meta بنطاق العلامة (`/brands/{brand_id}/social/meta/…`)، اختيار الصفحات صراحةً، حارس `provider_user_name` | `src/modules/social_connect/` | ✅ عزل الوكالة: غير المختارة لمالك الاتصال فقط + `is_connection_owner` + 403 لغير المالك (H1)؛ أسرار Graph في Bearer/جسم POST فقط (H2)؛ `PostgresStatusMarker` خارج المعاملة (H3)؛ فحص https+مضيف لـ`paging.next` (H4). ⚠️ `POST /debug_token` بلا `method=GET` ⇒ يُصلَح في SPRINT-05 A-0 |
| هجرات Alembic async | `alembic/versions/0001`–`0004` | ✅ 0004 (`users`, `refresh_sessions`) منجزة |
| وحدة Auth: Argon2id + JWT HS256 (15 د) + Refresh دوّار في `__Secure-refresh` (`Path=/api/v1/auth`) + كشف إعادة الاستخدام + `CurrentUserFresh` (revoked/expires/is_active) + محدِّد معدّل محدود الذاكرة | `src/modules/auth/` | ✅ إبطال-أولاً بـ`rowcount==1`؛ سباق refresh مُثبَت على PG `[200,401]` و0 جلسات حية (قرار صارم مُعتمَد) |
| CI: `lint` / `unit` / `integration` (pgvector:pg16) / `secrets` (gitleaks) / `licenses` (D-054) | `.github/workflows/ci.yml`, `scripts/check_licenses.py` | ✅ خمسة فحوص منفصلة خضراء على `main` |
| سكربت نسخ احتياطي | `scripts/backup_progress.sh` | ✅ |

**مراجع المراجعات:** [`SPRINT-01`](./sprints/SPRINT-01_REVIEW.md) · [`SPRINT-02`](./sprints/SPRINT-02_REVIEW.md) · [`SPRINT-02.1`](./sprints/SPRINT-02.1_REVIEW.md) · [`SPRINT-03`](./sprints/SPRINT-03_REVIEW.md) · [`SPRINT-04`](./sprints/SPRINT-04_REVIEW.md) · [`SPRINT-04.1`](./sprints/SPRINT-04.1_REVIEW.md)

---

## 3. المهمة النشطة الحالية (Active Task)

- **معرف المهمة:** `SPRINT-05-META-OPERATIONAL-READS`
- **وثيقة التوجيه المعتمدة (المصدر الوحيد):** [`CURRENT_DIRECTIVE.md`](./CURRENT_DIRECTIVE.md)
- **حالة المهمة:** `ISSUED` — المنفذ يبدأ فور دمج توجيه المخطط في `main` (المخطط يدمجه بنفسه — D-063).
- **الهدف:** **Part A (إلزامي):** A-0 `POST /debug_token` بجسم `{input_token, method=GET}` (تجاوز طريقة Graph لنقاط القراءة) + إنهاء الابتلاع الصامت في `_inspect_token` (ERROR + عدّاد لكود 100) + تغطية الفرعين المتبقيين؛ A-1 اختبارات تكامل تثبت: هوية Meta مختلفة = اتصال جديد بلا وراثة اختيار، ونقل الملكية بنفس الهوية يحفظ الاختيار. **Part B (ميزة، بلا هجرة):** B1 `token_health` محسوب (`valid/expiring_soon/expired/unknown/reauth_required`)؛ B2 `POST …/connections/{id}/resync` (مالك الاتصال فقط؛ تحديث انتهاء/نطاقات/توكنات الصفحات المختارة؛ `removed_selected_page_ids`)؛ B3 `GET …/pages/{page_id}/posts` وB4 `GET …/pages/{page_id}/instagram/media` — قراءة مباشرة (D-065) للصفحات **المختارة فقط** (غير المختارة ⇒ 404 للجميع)، Bearer + `appsecret_proof` فقط، `next_cursor` من `cursors.after` ولا `paging.next` للعميل أبداً (D-064).
- **خارج النطاق:** النشر/الجدولة، `/insights`، تخزين المنشورات، تغيير نطاقات OAuth، أي هجرة، Redis/Temporal/LiteLLM.
- **السبرنت السابق:** `SPRINT-04.1-HOTFIX` — **مقبول** (PR #13 ⇒ `main` @ `32c20cb`؛ دُمج بيد المالك قبل المراجعة، والمراجعة اللاحقة أثبتت السلامة) — [`sprints/SPRINT-04.1_REVIEW.md`](./sprints/SPRINT-04.1_REVIEW.md).
- **ردود المخطط على أسئلة المنفذ (04.1):** (1) H7 **صارم**: توكن تحديث قُدِّم مرتين ⇒ إبطال كل الجلسات (0 حية) — نافذة التسامح للتبويبين بند مؤجَّل #8 حتى سبرنت الواجهة؛ (2) H1-3 الاختيار **يُحفظ** عند نقل الملكية بنفس هوية Meta؛ هوية مختلفة = اتصال جديد بلا وراثة (يُثبَّت في A-1) — بند مؤجَّل #9؛ (3) تدوير التوكن مرفوع للمالك (الطلب الخامس).
- **درس للمخطط (مُطبَّق في هذا التوجيه):** §2-0 من التوجيه يحمل **جدول قناة الأسرار** لكل نقطة Graph (رابط/رأس/جسم) و**جدول الرؤية لكل حقل** لكل دور — القاعدة صارت D-064.

---

## 4. معايير القبول للمهمة الحالية (Acceptance Criteria)

الكاملة في **§3 من `CURRENT_DIRECTIVE.md`**. ملخصها:
- [ ] CI خضراء (الفحوص الخمسة)؛ 0 فشل؛ تغطية ≥ 95% (لا تراجع عن 99%)؛ ruff نظيف.
- [ ] `"method": "GET"` داخل `data=` لـ`/debug_token`؛ لا `params=` يحمل توكناً/سراً؛ لا `paging.next` في `schemas.py`/`router.py`.
- [ ] الاختبارات بالأسماء `A0-U1..U4`, `A1-I1/I2`, `B1-U1`, `B2-U1..U4`, `B2-I1/I2`, `B3-U1..U7`, `B3-I1`, `B4-U1..U4`, `B4-I1` ناجحة على PG حقيقية.
- [ ] لا هجرة جديدة (0001–0004 فقط)؛ لا تبعية جديدة؛ كوميتان فوق `main`؛ لا ملف محظور مُسّ.
- [ ] §6 من التوجيه مملوء بالسطر؛ التسليم بصيغة «PR #N جاهز لمراجعة المخطط»؛ المنفذ لا يدمج.

---

## 5. الخطوة الدقيقة التالية (Exact Next Step)

| الترتيب | الدور | الخطوة |
|---|---|---|
| 1 | **المخطط** | يدمج PR هذا التوجيه في `main` فور فتحه (D-063) ويسجّل SHA الدمج في §6. |
| 2 | **المنفذ** | `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` ⇒ ينفّذ SPRINT-05 Part A ثم Part B (كوميتان) ⇒ يفتح PR ⇒ يملأ §6 ويحدّث §1/§6 هنا ⇒ يكتب «PR #N جاهز لمراجعة المخطط». **لا يدمج.** |
| 3 | **المالك** | 🔑 **تدوير توكن GitHub (الطلب الخامس)**. 🛡️ حماية `main` بالفحوص الخمسة + منع الدفع المباشر. **لا دمج يدوي** — المخطط يدمج. |
| 4 | **المخطط** | عند التسليم: مراجعة مستقلة على PG حقيقية (بما فيها التقاط الطلبات الفعلية لـ`/posts` و`/media` والتأكد من غياب الأسرار و`paging.next`) ⇒ `sprints/SPRINT-05_REVIEW.md` ⇒ دمج ⇒ SPRINT-06. |

> **ثلاث قواعد:** (1) لا يبدأ المنفذ قبل دمج توجيه المخطط في `main`. (2) المخطط يفرّع `genspark_ai_developer` من `main` **فقط**. (3) **المالك لا يدمج يدوياً** — المخطط يدمج توجيهاته فوراً وPRs المنفذ بعد الاعتماد؛ المنفذ لا يدمج أبداً.

---

## 6. سجل نقاط الاستئناف (Checkpoint Log)

> يُضاف سطر جديد في الأعلى عند كل نقطة مؤكدة. **لا تُحذف السطور القديمة.**

| التاريخ | الوكيل | الفرع | الكوميت | ما تم تأكيده | الخطوة التالية |
|---|---|---|---|---|---|
| 2026-10-30 | المخطط | `genspark_ai_developer` (من `main` @ `32c20cb`) → PR *(يُحدَّث بعد الدفع)* | *(يُحدَّث بعد الدفع)* | مراجعة مستقلة **لاحقة للدمج** لـ SPRINT-04.1 (المالك دمج PR #13 يدوياً @ 12:19Z قبل خطوة المخطط): بيئة طازجة ×2 (PG 17 + pgvector أُعيد تثبيتهما بعد انقطاعين)، **474 ناجحاً / 99.57%**، ruff نظيف، تجربة تسريب `httpx` على DEBUG ⇒ `False` للأسرار الخمسة ولا سر في أي رابط (POST form لـ`/oauth/access_token`، Bearer لـ`/me/accounts` و`/debug_token`)، سباق H7-I1 3/3 `[200,401]`، greps H5/H2 نظيفة (مطابقات `__Host-` تعليقات فقط)، H1–H8 بالسطر ⇒ **مقبول** (`sprints/SPRINT-04.1_REVIEW.md`). قرارات: H7 صارم؛ H1-3 حفظ الاختيار؛ ملاحظة المالك `debug_token` بلا `method=GET` ثبتت بنيوياً ⇒ A-0. Hub: `SECURITY_BASELINE` §2 (6 صفوف جديدة/محدَّثة)، `KNOWN_ISSUES` دروس 5–8، `DEFERRED` 7–9، `DATA_AND_INTEGRATIONS` 0004 منجز، `CHANGELOG`، `DECISION_LOG` **D-064** (قناة الأسرار + جدول الرؤية) و**D-065** (قراءة مباشرة بلا تخزين). **SPRINT-05** صادر في `CURRENT_DIRECTIVE.md` (A-0/A-1 إلزامي؛ B1–B4: `token_health`, `resync`, `/posts`, `/instagram/media`؛ بلا هجرة). | المخطط يدمج هذا الـPR فوراً؛ المنفذ يبدأ SPRINT-05؛ المالك يدوّر التوكن (الطلب الخامس). |
| 2026-10-30 | المنفذ | `work/implementation` → PR [#13](https://github.com/belalalibb/ai_agancy_markting/pull/13) | `51ac0fe` (Part A) / `1b7a046` (Part B) / كوميت التوثيق فوقهما | SPRINT-04.1-HOTFIX مُسلَّم. **Part A:** H1 `ConnectedAccountPublic.from_domain(…, viewer_tenant_id)` يُخفي الصفحات غير المختارة عن غير صاحب الاتصال + `is_connection_owner`؛ `select_pages` لصاحب الاتصال فقط (403 `not_connection_owner` قبل أي نداء Graph)؛ `disconnect` لمالك العلامة أو صاحب الاتصال؛ إعادة الربط بفاعل مختلف تنقل الملكية. H2 `POST` بجسم form لـ`/oauth/access_token` و`/debug_token`، `Authorization: Bearer` لـ`/me` و`/me/accounts`، تنقية `paging.next`، لا `{exc}` في 502؛ `src/core/logging.py` (`httpx`/`httpcore` ⇒ WARNING + `SecretRedactingFilter` على الجذر). H3 `StatusMarker` Protocol + `PostgresStatusMarker` بمعاملة مستقلة؛ صفحة بلا توكن ⇒ 422 `page_not_selectable`. H4 فحص https/مضيف لـ`paging.next`؛ `INSERT … ON CONFLICT DO NOTHING RETURNING id` في tenancy/auth ⇒ 409 بدل 500. **Part B:** H5 `__Secure-refresh` + حارس H5-U1؛ H6 `is_active(sid, now)` بـ`JOIN users` + `/auth/me` على `CurrentUserFresh`؛ H7 إبطال أولاً بـ`rowcount` + `link_replacement`؛ H8 محدِّد بسقف 10k مفتاح + مسح كسول. **الأرقام:** 447 + 27 = **474 ناجحاً**، 0 فشل، **99.57%**، ruff نظيف، greps القبول نظيفة، H7-I1 `[200, 401]` حتمياً. ساندبوكس أُعيد ≥4 مرات؛ 6 checkpoints WIP سُحقت في الكوميتين. 14 انحرافاً موثقاً + 3 أسئلة في §6. | مراجعة المخطط لـ PR #13 ⇒ قرار H7 ⇒ دمج ⇒ المالك يدوّر التوكن ⇒ SPRINT-05. |
| 2026-10-30 | المخطط | `genspark_ai_developer` → PR [#12](https://github.com/belalalibb/ai_agancy_markting/pull/12) *(صُحّح: كان مكتوباً #13 خطأً)* | `709ffad` | مراجعة SPRINT-04 @ `0db7828`: 434 اختباراً/99%/CI خضراء/greps نظيفة ⇒ **8 ملاحظات رقابية خارجية تُحقق منها المخطط بالسطر — كلها ثابتة**؛ S03-2 أُثبتت تجريبياً (سجل `httpx` INFO طبع `client_secret=` و`access_token=`). **قبول مشروط** ⇒ PR #11 دُمج `6f263ac`. أُصدر **SPRINT-04.1-HOTFIX** (H1–H8، كوميتان، لا ميزات) مع إقرار أن S03-1/S03-2 ثغرتا توجيه المخطط. | المالك: تدوير التوكن + إطلاق المنفذ ⇒ PR #12. |
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
