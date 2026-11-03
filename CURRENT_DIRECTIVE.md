# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-12.1-B رُوجع مستقلاً على PostgreSQL 17.11 UTF8 و**قُبل ودُمج** (PR #52 ⇒ `main @ 29205d6`؛ التقرير [`sprints/SPRINT-12.1-B_REVIEW.md`](./sprints/SPRINT-12.1-B_REVIEW.md): U1–U4 **معكوسة حياً** بمسبار مستقل، XSS/السرّ/الأدمن 404 مُثبتة). **الحظر التشغيلي على `/app` مرفوع** بشرط `--workers 1` (N1). **بوابة TTFV/App Review مفتوحة للمالك** (§7). هذا التوجيه = **SPRINT-13 — تشغيل اليوم الثاني (Day-2 Ops)** وفق `sprints/POST_M7_STRATEGIC_PLAN.md` §4-ب/§4-ج وD-081/D-082.

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-13-DAY2-OPS` |
| **النوع** | **A** صلابة التشغيل (أدوات الإدارة + مفاتيح الإيقاف من DB + dead-letter مرئي + تنبيه التوكنات) ⇒ **B** اقتصاد واتساب (D-082 الجزء 2: تنبيه 800/شهر + `quality_rating` للمالك) + قرارات 12.1-B (أ/ب) ⇒ **C** دورة حياة البيانات (D-081: تصدير/حذف/احتفاظ) + تمرين الاستعادة |
| **الحالة** | `ISSUED` — جاهز فور دمج هذا التوجيه في `main` (المخطط يدمجه — D-063) |
| **يبدأ من** | `main` @ **SHA دمج هذا التوجيه** (يسجَّل في `WORK_STATE.md` §6). أول أمر: `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` |
| **الفرع المستهدف** | `work/implementation` ⇒ **PR واحد** بعنوان `SPRINT-13: Day-2 Ops — admin_actions + kill switches + dead-letter + WA economics (D-082/2) + data lifecycle (D-081)` |
| **الكوميتات (بالترتيب، كلٌّ يُدفع فور اكتماله)** | **A** `feat(sprint-13): admin_actions audit + DB kill switches + dead-letter requeue + token-expiry alert` · **B** `feat(sprint-13): WA economics — monthly service-message alert, quality_rating for OWNER, brand sector` · **C** `feat(sprint-13): data lifecycle — export/delete brand, retention sweep (D-081), restore drill doc` · **docs** `docs(sprint-13): §6 report` |
| **الهجرة** | **واحدة فقط: `0017_day2_ops`** تحمل كل أعمدة/جداول A+B+C (ذهاباً وإياباً على PG 17). لا هجرة ثانية. |
| **الحجم المتوقع (G6)** | A ≈ 650 · B ≈ 400 · C ≈ 550 ⇒ **≈ 1,600 سطر `src/` (py فقط، JSON/HTML لا يُحسبان) ≤ 1,700**. صمّام §1-ج. **التقرير بالامتداد:** `git diff --numstat origin/main -- src \| awk` مجموعات `py/html/json` (درس 12.1-B #14). |
| **المالك** | 🔑 تدوير التوكن (≥ 25 ظهوراً) · TTFV + فيديوهات App Review (§7) · `PLATFORM_ADMIN_USER_IDS` · `--workers 1`. |

---

## 0. قواعد ثابتة (لا تتغير)

1. **لا تلمس:** `src/modules/auth/*` (باستثناء البند A-4 المحدَّد أدناه بسطرٍ واحد)، `src/core/contract.py`, `src/core/public_fetch.py`, `pyproject.toml` (لا اعتمادية جديدة — D-054)، وأي هجرة قبل 0017.
2. **لا `ErrorCategory` جديدة** (12 ثابتة). أخطاء الوحدات الجديدة = أكواد حرفية في `exceptions.py` الخاصة بالوحدة + ترجمة `ar.json`.
3. **D-068 No-Bypass:** الواجهة `src/ui` تنادي الـAPI عبر `ApiClient` فقط؛ لا `sqlalchemy`/`.infrastructure`/`httpx` فيها.
4. **D-069:** لا نص عميل في `pulse_alerts.payload` ولا في `admin_actions.before/after` (معرّفات وأعداد فقط).
5. **D-055:** كل فعل إداري حدثٌ واحد؛ **D-070:** لا إعادة محاولة عمياء.
6. **كل كوميت يُدفع فور اكتماله** (الساندبوكس يُعاد ضبطه كل 1–2 دور) ويمرّ CI 5/5 قبل التالي.
7. **المنفذ لا يدمج** (D-063).

### 1-ج. صمّام الحجم (G6) — مرتَّب الأولوية، **لا يُضحّى بالبنود 1–4 أبداً**
| الأولوية | البند | عند الاقتراب من 1,700 |
|---|---|---|
| 1 | A-1 `admin_actions` + A-2 مفاتيح الإيقاف من DB | **لا يُقطع** (شرط App Review/ثقة) |
| 2 | C-1 تصدير + C-2 حذف العلامة | **لا يُقطع** (Must Have §1-ز) |
| 3 | A-3 dead-letter مرئي + إعادة طابور | **لا يُقطع** |
| 4 | B-1 تنبيه 800/شهر + B-2 `quality_rating` للمالك | **لا يُقطع** (D-082) |
| 5 | A-4 تنبيه التوكنات (تحسين الفحص 5) | يُختزل إلى تنبيه الأدمن فقط |
| 6 | B-3 `brand_profiles.sector` + ربط الـPlaybook | يُؤجَّل إلى S14 (العمود يبقى في 0017) |
| 7 | C-3 مهمة الاحتفاظ (retention sweep) | تُختزل إلى «dry-run يُعيد العدّ» بلا حذف |
| 8 | C-4 `ops/RESTORE_DRILLS.md` + سكربت `scripts/backup_pg.sh` | **لا يُقطع** — وثيقة + سكربت فقط (≈ 80 سطراً) |

تجاوز 1,700 بعد تطبيق 5⇒7 = توقّف وسجّل في §6 لقرار المخطط.

---

## 2. النطاق التفصيلي

### Part A — صلابة التشغيل

**A-1 `admin_actions` (سجل تدقيق).** جدول `admin_actions(id, actor_user_id, action, target_kind, target_id, before JSONB, after JSONB, at)` — فهرس `(target_kind, target_id, at desc)`. يُكتب **صفاً واحداً** لكل: `/admin/ai/*` (create/update/delete/routing)، `/admin/entitlements/*` (activate/extend/wa-caps)، A-2 (kill/suspend)، A-3 (requeue)، C-2 (delete brand). الكتابة في **نفس المعاملة** مع الفعل (فشل الفعل ⇒ لا صف). `GET /api/v1/admin/actions?target_kind=&target_id=&limit=` (PlatformAdmin، ≤ 200، الأحدث أولاً). الواجهة: تبويب «السجل» في `/app/admin/ai?tab=audit` يعرض آخر 50 (قراءة فقط).

**A-2 مفاتيح الإيقاف من DB (لا env).** أعمدة: `brands.suspended_at TIMESTAMPTZ NULL` · جدول `platform_flags(key TEXT PK, value BOOLEAN, updated_at)` بمفتاحَي `platform_readonly` و`feature_pulse`/`feature_lead_loop` (تُقرأ أولاً من DB وإن غابت فمن `Settings` — **لا كسر للسلوك الحالي**). السلوك: `suspended_at IS NOT NULL` ⇒ كل كتابة على العلامة **403 `brand_suspended`** (قراءة تبقى)؛ `platform_readonly=true` ⇒ كل `POST/PUT/PATCH/DELETE` تحت `/api/v1` **503 `platform_readonly`** + `Retry-After: 300` **باستثناء** `/auth/*` و`/admin/*` (حتى يُرفع المفتاح). مسارات: `POST /api/v1/admin/brands/{id}/suspend` + `/unsuspend`، `PUT /api/v1/admin/flags/{key}` (PlatformAdmin). كاش 30 ثانية داخل العملية. الواجهة: زرّان في `/app/admin/entitlements` (بحث العلامة ⇒ suspend/unsuspend بـ`hx-confirm`) + مفاتيح في تبويب `audit`.

**A-3 dead-letter مرئي + إعادة طابور.** `GET /api/v1/admin/publishing/dead-letter?limit=` يعيد مهام `publish_jobs.status='failed'` مع `error_class`/`attempt_count`/`item_id`/`brand_id` (PlatformAdmin). `POST /api/v1/admin/publishing/jobs/{job_id}/requeue` ⇒ `queued` + `attempt_count=0` + `next_attempt_at=now` + عنصر `publish_failed → scheduled` (CAS) + حدث D-055 `requeued_by_admin` + صف `admin_actions`. **409 `job_not_failed`** إن لم تكن `failed`. الواجهة: تبويب `/app/admin/ai?tab=dead_letter` (قائمة + زر إعادة). **لا تغيير في منطق الحجز/الاستعادة الحالي** (`scheduler.py` B6-I3).

**A-4 تنبيه التوكنات.** الفحص 5 `meta_token_expiring` موجود؛ يُضاف **(أ)** سبب `refresh_failed` في `payload` عند فشل تجديد التوكن الدوري (من `social_connect`) **(ب)** تنبيه الأدمن: صف `pulse_alerts` بـ`brand_id` = العلامة ونسخة ملخصة في `GET /api/v1/admin/alerts?kind=meta_token_expiring` (PlatformAdmin). **سطر واحد مسموح في `src/modules/auth`**: لا شيء — إن احتجت تغييراً هناك توقّف وسجّل.

### Part B — اقتصاد واتساب + قرارات 12.1-B

**B-1 تنبيه 800 رسالة خدمة/شهر/رقم (D-082/Q16).** فحص Pulse **(9) `wa_monthly_service_80pct`**: عدّ الرسائل الصادرة `pricing_category='service'`/`billable` للرقم في الشهر التقويمي الحالي ≥ 800 ⇒ تنبيه `warning` بـ`payload={ref:'<account_id>:<YYYY-MM>', count, cap:1000}`؛ idempotent شهرياً. ترجمة `alert.wa_monthly_service_80pct` + `severity` موجودة. **إن لم يوجد عمود `pricing_category`/`billable` على رسائل واتساب** ⇒ أضفه في 0017 (`messages.billable BOOLEAN NULL`, `pricing_category TEXT NULL`) ويُملأ من الـwebhook `pricing` عند توفره؛ الفحص يعدّ `billable IS TRUE OR billable IS NULL` (محافظ) ويُسجَّل ذلك في §6.

**B-2 `quality_rating` والأسقف للمالك (قرار ب = نعم للقراءة).** `WhatsAppAccountPublic` += `quality_rating`, `messaging_limit`, `daily_reply_cap`, `kill_switch`, `replies_today` (قراءة فقط؛ التعديل يبقى أدمن wa-caps). بطاقة واتساب في `/app/b/{id}/settings` تعرضها مترجَمة + سبب التوقف عند `kill_switch` أو السقف (`settings.wa_stopped_reason`).

**B-3 `brand_profiles.sector` (قرار أ = نعم).** عمود `sector TEXT NULL CHECK (sector IN ('restaurants','clinics'))` في 0017؛ `PATCH /api/v1/brands/{id}/brain/profile` يقبله؛ `BrandWithRoleOut`/`BrandProfileOut` يعيده؛ `GET /api/v1/playbooks?sector=` يصبح اختيارياً (يأخذ قطاع العلامة عند `brand_id`)؛ بطاقة «نصائح اليوم» تقرأه بدل `?sector=` غير المُخزَّن (يبقى `?sector=` كتجاوز).

### Part C — دورة حياة البيانات (D-081) + تمرين الاستعادة

**C-1 التصدير.** `POST /api/v1/brands/{id}/export` (OWNER، `CurrentUserFresh`) ⇒ **202** + `export_id`؛ `GET /api/v1/brands/{id}/export/{export_id}` ⇒ `pending|ready|failed` + عند `ready` **ملف ZIP** (JSON لكل كيان: brand/profile/evidence/recommendations/content_items+approval_events/conversations+messages/alerts + `manifest.json` بالأعداد وsha256). التنفيذ **متزامن داخل الطلب لحجم ≤ 5,000 صف لكل كيان** وإلا `413 export_too_large` (الخلفية الحقيقية ⇒ V1.1 — سجّل). التخزين: جدول `brand_exports(id, brand_id, status, requested_by, created_at, ready_at, sha256, byte_size, blob BYTEA)` مع **انتهاء 24 ساعة** (مهمة C-3 تحذف المنتهي). **لا سرّ/توكن في الملف**؛ `learning_consent` والنصوص المُعقَّمة المشتركة **لا تُصدَّر**.

**C-2 الحذف.** `DELETE /api/v1/brands/{id}` موجود أم لا؟ — إن لم يوجد: `DELETE /api/v1/brands/{id}` (OWNER، `CurrentUserFresh`) بتأكيد مزدوج: الجسم `{"confirm": "<brand slug أو name حرفياً>"}` وإلا **422 `confirmation_mismatch`**. الخطوات بالترتيب داخل معاملة واحدة ما أمكن: إبطال اشتراك webhooks Meta (best-effort، فشله لا يوقف الحذف — يُسجَّل في `admin_actions.after.unsubscribe='failed'`)، إبطال/حذف توكنات `social_connect`، حذف تسلسلي (CASCADE موجود في الهجرات؛ `ai_cost_log.brand_id → NULL` D-081)، صف `admin_actions(action='brand_deleted')` **بـ`brand_hash = sha256(brand_id)`** في `after`، ثم **204**. الواجهة: قسم «منطقة الخطر» في `/app/b/{id}/settings` (حقل التأكيد + زر) ⇒ بعد النجاح `HX-Redirect` إلى `/app`.

**C-3 الاحتفاظ (retention sweep).** مهمة دورية `retention_sweep` (كل 24 ساعة، تُقرأ المهلة من `Settings.retention_*`): محادثات/رسائل/مسودات أقدم من **12 شهراً** (24 لـ`agency`) ⇒ حذف؛ `brand_exports` أقدم من 24 ساعة ⇒ حذف؛ **لا تلمس** `ai_cost_log`/`entitlement_events`/`approval_events`/`admin_actions` (7 سنوات). الافتراضي **`RETENTION_DRY_RUN=true`** (يعدّ ويسجّل فقط) — المالك يقلبها في الإنتاج. صف `admin_actions(actor=NULL, action='retention_sweep')` بالأعداد.

**C-4 نسخ احتياطي + تمرين استعادة.** `scripts/backup_pg.sh` (pg_dump `-Fc` ⇒ تشفير بـ`age` أو `openssl enc -aes-256-cbc -pbkdf2` من متغير بيئة — **لا اعتمادية بايثون**) + `scripts/restore_pg.sh` + `ops/RESTORE_DRILLS.md` بقالب: التاريخ، حجم الملف، زمن الاستعادة، عدد الصفوف قبل/بعد، التوقيع. **تمرين فعلي واحد** على PG 17 في CI (job جديد `restore-drill` يستعيد dump العينة ويتحقق من `alembic current == 0017`) — يُوثَّق زمنه في §6.

---

## 3. معايير القبول (18)

1. `ruff check . && ruff format --check .` = 0/0 على كل كوميت.
2. هجرة **0017** ذهاباً وإياباً على PG 17 (`upgrade head` ⇒ `downgrade 0016` ⇒ `upgrade head`) بلا بقايا.
3. وحدات بلا DB ≥ **95.00%** (دقة 2، نصفان `--cov-append` إن لزم) · 0 فشل.
4. تكامل PG 17: الحالي 142 + الجديد ≥ **12** (admin_actions نفس المعاملة، suspend ⇒ 403، readonly ⇒ 503 مع استثناء auth/admin، requeue CAS، 800/شهر idempotent، export round-trip sha256، delete ⇒ 204 + `ai_cost_log.brand_id IS NULL` + صف admin_actions بـ`brand_hash`، retention dry-run لا يحذف، confirmation_mismatch 422).
5. `PROTECTED_ROUTE_COUNT` = **85 + 10** (`GET admin/actions`, `POST/POST suspend/unsuspend`, `PUT admin/flags/{key}`, `GET dead-letter`, `POST requeue`, `GET admin/alerts`, `POST export`, `GET export/{id}`, `DELETE brands/{id}`) = **95** — أو الفعلي مع تبرير في §6. العامة **7** (+2 health) بلا تغيير.
6. كل مسار `/admin/*` جديد: غير أدمن ⇒ **404** (API) و**404** صفحة (UI) — لا 403.
7. `admin_actions`: فعل فاشل ⇒ **0 صف** (اختبار معاملة)؛ فعل ناجح ⇒ **صف واحد** بالضبط (D-055).
8. `platform_readonly=true`: `POST /api/v1/brands/{id}/content/...` ⇒ 503 + `Retry-After`; `POST /api/v1/auth/login` ⇒ 200/401 عادي؛ `PUT /api/v1/admin/flags/platform_readonly` ⇒ 200 (يمكن الرفع).
9. `brands.suspended_at`: كتابة ⇒ 403 `brand_suspended`؛ قراءة `GET /brands/{id}` ⇒ 200 مع `suspended: true`.
10. B-1: 799 رسالة ⇒ لا تنبيه؛ 800 ⇒ تنبيه واحد؛ 801 في نفس الشهر ⇒ لا تنبيه ثانٍ؛ شهر جديد ⇒ تنبيه جديد.
11. B-2: `WhatsAppAccountPublic` لا يحمل أي سرّ/hash (اختبار الحقول)؛ VIEWER لا يراه (403 كما الآن).
12. C-1: الملف ZIP لا يحتوي `access_token|refresh_token|api_key|gAAAA` (grep على المحتوى)؛ `manifest.sha256` يطابق.
13. C-2: بعد الحذف `GET /brands/{id}` ⇒ 404؛ `/app/b/{id}/today` ⇒ 404؛ `ai_cost_log` صفوف العلامة باقية بـ`brand_id NULL`.
14. C-3: `RETENTION_DRY_RUN=true` ⇒ 0 حذف + صف `admin_actions` بالأعداد؛ `false` ⇒ حذف الصفوف الأقدم فقط (اختبار تكامل بتواريخ مُزوَّرة).
15. C-4: job `restore-drill` أخضر في CI + زمن الاستعادة في §6 + `ops/RESTORE_DRILLS.md` به سطر التمرين الأول.
16. `src/ui`: greps 12.1-B (httpx/sqlalchemy/infrastructure/CDN/`api_key`/`|safe`/عربية حرفية) = **0**؛ كل نص جديد في `ar.json`؛ `test_i18n_covers_all_enums` يغطي `alert.wa_monthly_service_80pct` وأكواد A/B/C الجديدة.
17. لا `ErrorCategory` جديدة · لا اعتمادية جديدة · `src/modules/auth` = **0 فرق** · `contract.py`/`public_fetch.py` = 0.
18. الحجم: py ≤ 1,700 أو صمّام §1-ج مُسجَّل؛ التقرير **بالامتداد**.

---

## 4. أوامر التحقق (ينسخها المنفذ حرفياً إلى §6)

```bash
ruff check . && ruff format --check .
alembic upgrade head && alembic downgrade 0016 && alembic upgrade head
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_growth_os_test pytest tests/integration -q
pytest tests -q --ignore=tests/integration --cov=src --cov-report=term --cov-precision=2 --cov-fail-under=95
python -c "from tests.test_auth_guard import PROTECTED_ROUTE_COUNT, PUBLIC; print(PROTECTED_ROUTE_COUNT, len(PUBLIC))"
git diff --numstat origin/main -- src | awk '{split($3,a,"."); e=a[length(a)]; i[e]+=$1} END {for (k in i) print k, i[k]}'
git diff origin/main -- src/modules/auth src/core/contract.py src/core/public_fetch.py pyproject.toml | wc -l   # = 0
grep -rn "import httpx\|from httpx\|sqlalchemy\|\.infrastructure" src/ui | wc -l   # = 0
grep -rn "api_key\|get_secret_value\||safe" src/ui/templates | wc -l   # = 0
unzip -p export.zip | grep -c "access_token\|refresh_token\|api_key\|gAAAA"   # = 0
```

---

## 5. التزامات المنفذ

1. **A يُدفع وحده أولاً** ويمرّ CI 5/5 قبل B؛ B قبل C.
2. أي تغيير مطلوب في `src/modules/auth` ⇒ **توقّف وسجّل** (لا تنفّذ).
3. الهجرة 0017 **واحدة** تُكتب في A وتحمل أعمدة B/C منذ البداية (لا تعديل لاحق للهجرة؛ إن لزم عمود نسيته ⇒ سجّل في §6 واطلب قرار المخطط).
4. **لا مسبار/ملف `_probe_*`** يُرفع.
5. أسماء الأكواد الحرفية ملزمة: `brand_suspended`, `platform_readonly`, `job_not_failed`, `export_too_large`, `confirmation_mismatch`, `requeued_by_admin`, `brand_deleted`, `retention_sweep`, `wa_monthly_service_80pct`.
6. صمّام §1-ج ملزم بترتيبه؛ تجاوز بلا تسجيل = رفض.
7. §6 يُملأ بالأرقام الحرفية (لا «✅» بلا رقم)، والحجم **بالامتداد**.

---

## 6. تقرير المنفذ (يملؤه المنفذ)

| البند | القيمة |
|---|---|
| SHA الكوميتات | |
| نتائج `pytest` (العدد/التغطية) لكل كوميت — كامل **و** بلا DB | |
| هجرة 0017 ذهاباً وإياباً (الجداول/الأعمدة/القيود) | |
| `PROTECTED_ROUTE_COUNT` / العامة / `/app/*` | |
| أسطر `src/` **بالامتداد** لكل Part + حالة صمّام §1-ج | |
| نتائج greps §4 + grep على ZIP التصدير | |
| زمن تمرين الاستعادة (C-4) + رابط job CI | |
| انحرافات عن §2 (مع السبب) | |
| ما يحتاج قرار المالك | |

---

## 7. ما ينتظر المالك (بوابة TTFV / App Review — مفتوحة الآن)

1. 🔑 **تدوير التوكن** (ظهر ≥ 25 مرة في محادثة المخطط وحدها) + حماية `main`.
2. **تشغيل حي للواجهة:** `uvicorn --workers 1` (N1 في مراجعة 12.1-B) · `PLATFORM_ADMIN_USER_IDS=<uuid المالك>` · `UMAMI_WEBSITE_ID` اختياري.
3. **3 علامات تجريبية** (مطعم/كافيه ×2 + عيادة ×1) بحسابات Meta/WABA حقيقية ⇒ قياس **TTFV ≤ 30 دقيقة** (من الدخول إلى أول توصية + أول ردّ مُوافَق) وتسجيل الزمن في `WORK_STATE.md` §6.
4. **فيديوهات App Review** (الرحلة: دخول ⇒ اليوم ⇒ صندوق الوارد ⇒ ردّ ⇒ تشخيص ⇒ مشاركة رابط ⇒ الإعدادات/الموافقة). الواجهة جاهزة لذلك الآن.
5. قرار (ج) من 12.1-B: منح الأعضاء بالبريد ⇒ **مؤجَّل V1.1** (`tenant_members`) ما لم يعترض المالك.
6. اختيار أداة تشفير النسخ الاحتياطي (C-4): `age` (Apache-2.0/MIT) أو `openssl` — كلاهما بلا اعتمادية بايثون.
