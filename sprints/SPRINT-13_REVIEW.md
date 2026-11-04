# مراجعة SPRINT-13 — Day-2 Ops (PR #55) — مراجعة المخطط المستقلة

**الرأس المُراجَع:** `work/implementation` @ `1738e56` (A `863b4bf` · B `41aa94e` · C `b6bb7b9` · docs `1738e56`) فوق `main` @ `09e81ac`.
**بيئة المراجعة:** PostgreSQL **17.11** (PGDG) + pgvector، UTF8، أُعيد بناؤها **مرتين** في هذه الجولة (إعادتا ضبط الساندبوكس #14/#15). كل نتيجة أدناه إمّا مخرجات حيّة من هذه البيئة أو سطر كود مُقتبَس — **لا شيء مأخوذ من ادعاء المنفذ**.

**القرار التنفيذي: ❌ لا يُدمج PR #55 الآن — طلب تصحيح على الفرع (SPRINT-13.1-FIX) قبل الدمج.** التفاصيل في §5.

---

## 1. البوابات الثابتة (مُتحقَّقة محلياً)

| البوابة | النتيجة | الدليل |
|---|---|---|
| ruff | **0/0** على `1738e56` و`b6bb7b9` و`41aa94e` و`863b4bf` | `ruff check .` ×4 |
| هجرة 0017 ذهاباً وإياباً على PG 17.11 | ✅ `head ⇒ 0016 ⇒ head`: جداول 36 ⇒ 33 ⇒ 36، أعمدة 355 ⇒ 355، `alembic_version = 0017` | psql على `ai_rt` ثم `ai_growth_os_test` |
| تكامل على PG 17.11 | **153 passed / 0 failed** (346.83 s) | `pytest tests/integration` (`TEST_DATABASE_URL` محلي) |
| وحدات بلا DB | لم تُشغَّل محلياً (OOM ~1 GB معروف) — CI المنفذ: 1,614 / 95.12% | [CI run](https://github.com/belalalibb/ai_agancy_markting/actions/runs/41767754885) |
| greps الممنوعة (`httpx`/`sqlalchemy` في `src/ui`، أسرار في ZIP، `print`) | 0 | grep على الشجرة |
| الملفات المحمية (`contract.py`/`public_fetch.py`/`pyproject.toml`/`internal_metrics.py`) | 0 فرق | `git diff origin/main..1738e56 --stat` |
| الحجم | **py `src/` +2,179 / −66** · tests +2,489 · alembic +180 · html +66 · json +57 | `git diff --numstat` |
| `PROTECTED_ROUTE_COUNT` | 96 (`tests/test_auth_guard.py:98`) · `/app` 29 (`tests/ui/test_ui_session.py:58`) | ثابت في الاختبارات؛ غير الأدمن ⇒ 404 ×4 حياً (§3) |
| قيود FK المعنية بالاحتفاظ (PG 17) | `fk_approval_events_item_id` **CASCADE** · `fk_content_revisions_item_id` CASCADE · `fk_publish_jobs_item_id` CASCADE · `fk_publish_jobs_revision_id` **RESTRICT** · `fk_brand_recommendations_item` SET NULL | `pg_constraint` |

---

## 2. الفرضيات الجنائية S13-1..S13-5 — **الخمس ثبتت حيّاً على PG 17.11**

مسبار `tests/integration/_probe_s13.py` (مؤقت، حُذف، لم يُرتكب) باستخدام `integration_app` الحقيقي + `PostgresScheduler.run_once` الحقيقي + `FakePublisher` يُرجع `PublishOutcome.failure(TRANSIENT)`.

### 🔴 S13-1 — A-3 requeue يكسر `uq_publish_attempts_job_no`: المهمة المُعادة **لا تُنفَّذ أبداً** (مانع)

**الدورة الكاملة حيّاً:**

```
run_once ×3 ⇒ publish_attempts: [(1,retryable_failure),(2,…),(3,…)] · job: (failed, 3, transient) · item: publish_failed
POST /admin/publishing/jobs/{id}/requeue ⇒ 200 {"attempt_count": 0}
run_once #4 ⇒ IntegrityError: duplicate key value violates unique constraint "uq_publish_attempts_job_no"
   job بعدها: (leased, 1, "probe:1", leased_until≠NULL) · publish_attempts ما زالت 3 صفوف
run_once #5 (بعد انتهاء الإيجار) ⇒ IntegrityError نفسه · job: (leased, 2)
```

**السبب (file:line):** `ops/infrastructure/postgres_repository.py` `_REQUEUE_JOB` يضبط `attempt_count=0`؛ `publishing/infrastructure/postgres_repository.py:133` يرفع `attempt_count += 1` عند الإيجار؛ `publishing/scheduler.py:184` `PublishAttempt(attempt_no=job.attempt_count)` ⇒ `attempt_no=1` مكرر تحت `uq_publish_attempts_job_no` (`alembic/versions/0006_publishing.py:196`). الاستثناء يقع في معاملة 3a **بعد** التزام الإيجار ⇒ المهمة تبقى `leased` حتى انتهاء الإيجار، ثم `_recover_expired_leases` (`scheduler.py:141-150`) لا يجد attempt مفتوحاً ⇒ `requeue_expired` ⇒ إيجار جديد ⇒ `attempt_no=2` مكرر ⇒ **حلقة أبدية** كل `scheduler_lease_seconds` تُسجَّل `scheduler cycle failed` (`scheduler.py:358`) وتُعطّل بقية الدفعة (`run_once` يعالج المهام تسلسلياً — `scheduler.py:136-137`؛ الاستثناء يوقف الحلقة `for` قبل بقية المهام). **أي requeue واحد يسمّم دورة النشر لكل العلامات.**

**الحواف:**
- **(أ) owner `publish/retry` ثم admin requeue ⇒ `IntegrityError` 500 على `uq_publish_jobs_active_item`** (حيّاً: `retry ⇒ 200`، مهمتان `[failed, queued]`، `requeue ⇒ UniqueViolationError … uq_publish_jobs_active_item`، `admin_actions = []`). `OpsService.requeue` (`ops/service.py:160-200`) لا يفحص وجود مهمة نشطة للعنصر قبل CAS.
- **(ب) عنصر `archived` ⇒ requeue 200 والمهمة `queued` والعنصر يبقى `archived`** (حيّاً). `_ITEM_CAS` (`ops/infrastructure/postgres_repository.py` ~L80) لا يفحص `rowcount` ⇒ مهمة نشر لعنصر مؤرشف.
- **(ج) `last_error_class='ambiguous_outcome'` ⇒ requeue 200** (حيّاً) — **خرق D-070**: `content/service.py:316` يرفض بـ`manual_verification_required` بينما المسار الإداري يعيد النشر وقد يكون المنشور حيّاً فعلاً ⇒ تكرار على الصفحة.

### 🟠 S13-2 — `/app/admin/entitlements?brand_id=` **404 صفحة كاملة** للأدمن غير العضو

حيّاً (AGENCY كأدمن منصة بلا منحة على BRAND): `login 303` · **`GET /app/admin/entitlements?brand_id=… ⇒ 404`** (لا بطاقة suspension/entitlement/wa) بينما `GET /api/v1/admin/entitlements/{id} ⇒ 200` و`GET /api/v1/brands/{id} ⇒ 404`. السبب: `src/ui/router.py:1345-1353` `_admin_ent_ctx` يقرأ `/brands/{id}` و`/brands/{id}/whatsapp/account` بمسارات المستأجر (التعليق في `router.py:1350-1351` يعترف بذلك). **النتيجة: الأدمن لا يستطيع إيقاف علامة عميل ليس عضواً فيها من الواجهة** — وهو الاستخدام الأساسي لـA-2.

### 🔴 S13-3 — `token_encryption_key=None` + lifespan ⇒ **500** على export و**500** على delete

حيّاً: `http_client_present=True` · `POST /brands/{id}/export ⇒ 500` · `DELETE /brands/{id} ⇒ 500`. السبب: `lifecycle/router.py:54` `settings.token_encryption_key.get_secret_value()` بلا حارس `None` (`config.py:56` `SecretStr | None`)، ويُقيَّم داخل `get_unsubscriber` الذي هو تبعية لـ`get_lifecycle_service` (`router.py:70-80`) ⇒ **حتى التصدير (الذي لا يحتاج فك تشفير) يسقط**. في الإنتاج المفتاح موجود (`_start_scheduler` يرفض الإقلاع بدونه فقط حين `SCHEDULER_ENABLED=true` — `main.py:118-121`)؛ واجهة API بلا مجدول وبلا مفتاح = تكوين مسموح يُسقط Must-Have C-1/C-2 بـ500.

### 🔴 S13-4 — التصدير: (أ) **بلا `content_revisions.body`** · (ب) نص عميل يحوي `api_key` ⇒ **500**

- **(أ)** حيّاً: ZIP = `brand, profile, evidence, recommendations, content_items, approval_events, conversations, messages, alerts, manifest` — **`revision_body_in_zip = False`**. `lifecycle/domain.py:13-23` `EXPORT_ENTITIES` بلا `content_revisions`؛ `_EXPORT_SQL` بلا الكيان. **نصوص المنشورات — جوهر بيانات العميل (D-081 قابلية النقل) — غائبة.**
- **(ب)** حيّاً: رسالة واردة `'hi, my api_key is lost, help'` ⇒ `POST /export ⇒ 500`؛ `brand_exports` يحتفظ بالصف السابق فقط. السبب: `lifecycle/service.py:70-73` فحص `FORBIDDEN_FRAGMENTS` على **الحمولة كاملة** بعد `json.dumps` ⇒ `RuntimeError`؛ الراوتر يلتقط `LifecycleError` فقط (`router.py:141/162/193`). أي عميل يكتب «api_key» أو `gAAAA` في محادثة = **تعطيل دائم لتصدير العلامة**.

### 🟠 S13-5 — علم `feature_pulse` في DB يوقف HTTP فقط؛ خطوة المجدول تستمر

حيّاً: `PUT /admin/flags/feature_pulse {false} ⇒ 200` · `platform_flags = (feature_pulse,false)` · `GET /brands/{id}/pulse/alerts ⇒ 404` · **`pulse_step(now) ⇒ created=1` و`pulse_alerts = [wa_window_closing]`**. السبب: `main.py:166-180` `pulse_scheduler_step` يقرأ `settings.feature_pulse` فقط؛ `pulse/scheduler_step.py:25-50` لا يقرأ العلم. مفتاح القتل A-2 **نصف فعّال**؛ الـ`lead_loop` على المنوال ذاته (يُتحقق في 13.1).

---

## 3. معايير القبول الـ18 — ما ثبت حيّاً

| # | المعيار | الحكم | الدليل |
|---|---|---|---|
| 1 | هجرة 0017 واحدة ذهاباً وإياباً | ✅ | §1 |
| 2 | `admin_actions` صف لكل فعل في نفس المعاملة | ✅ | `test_a1` + حيّاً: 409 على requeue لا يكتب صفاً؛ (أ) أعلاه: الفشل لا يترك صفاً يتيماً |
| 3 | `platform_readonly` ⇒ 503 + `Retry-After: 300` للكتابات؛ GET وauth وadmin مُعفاة | ✅ | حيّاً: `write=503/300` · `GET=200` · `/admin/flags=200` |
| 4 | suspend ⇒ 403 `brand_suspended` للكتابات، القراءات تعمل | ✅ | حيّاً: `export=403 brand_suspended` · `GET content=200` |
| 5 | dead-letter + requeue CAS + حدث D-055 | ⚠️ CAS يعمل؛ **الدورة بعده مكسورة** | S13-1 |
| 6 | `GET /admin/alerts` | ✅ | تكامل 153 |
| 7 | غير الأدمن ⇒ 404 على كل `/admin/*` | ✅ | حيّاً ×4 = 404 |
| 8 | B-1 عتبة 800 رسالة/شهر + `billable` من webhook | ✅ | `test_sprint13_b_pg` (153) |
| 9 | B-2 عرض المالك للجودة/الحدود | ✅ | تكامل |
| 10 | B-3 `brand_profiles.sector` + CHECK | ✅ | `ck_brand_profiles_sector` حيّاً: `restaurants/clinics/NULL` |
| 11 | C-1 تصدير ZIP + sha256 + 24 h + صفر أسرار | ❌ | S13-3 (500) · S13-4 (نصوص غائبة؛ 500 على نص عميل) |
| 12 | C-2 حذف مع تأكيد + `ai_cost_log.brand_id NULL` + unsubscribe | ❌ جزئياً | 500 بلا مفتاح (S13-3)؛ المسار السعيد ✅ (`test_c2`) |
| 13 | C-3 retention dry-run + صف تدقيق | ✅ | `test_c3` + `service.py:182` |
| 14 | `/app` تصدير/حذف من الإعدادات | ✅ (29 مساراً) | `test_ui_session.py:58` |
| 15 | `/app/admin/entitlements` لعلامة أي عميل | ❌ | S13-2 |
| 16 | restore-drill في CI | ✅ (CI 6/6) | لم يُعَد محلياً — لا `pg_dump` رحلة مطلوبة بعد التحقق من 0017 |
| 17 | الصمّام ≤ 1,700 py | ❌ تجاوز (2,179) | قرار §4 |
| 18 | أكواد الخطأ الحرفية التسعة | ✅ | `job_not_failed` · `brand_suspended` · `platform_readonly` … حيّاً |

**الخلاصة:** 13 ✅ · 1 ⚠️ · 4 ❌ — والـ❌ الأربعة تقع على **Must-Have** (A-3، C-1، C-2، A-2/UI).

---

## 4. قرارات §6 (مطلوبة من المخطط)

1. **`content_items` في `retention_sweep` — محسوم: لا `DELETE FROM content_items` مباشر، أبداً.** `fk_approval_events_item_id` **CASCADE** ⇒ الحذف يمحو سجل الموافقات البشرية (D-055/D-081 سبع سنوات)؛ `fk_publish_jobs_revision_id` **RESTRICT** ⇒ الحذف يفشل أصلاً حين توجد مهمة. **القاعدة الملزمة:** الاحتفاظ للمحتوى = **إفراغ النص فقط**: `UPDATE content_revisions SET body = '' , media_refs = '[]'` للنسخ الأقدم من القطع **وللعناصر `archived`/`published`/`rejected` فقط**؛ تبقى الصفوف والمعرّفات والأحداث. يُنفَّذ في SPRINT-14 لا قبله، dry-run أولاً.
2. **OWNER لعلامة موقوفة ⇒ `POST /brands/{id}/export` مسموح** (D-081 قابلية النقل حق لا يُعلَّق بالإيقاف). حيّاً الآن 403 (§3-4). **الإصلاح في 13.1:** إعفاء `/export` و`DELETE /brands/{id}` من `require_brand_not_suspended` (`main.py:245` `dependencies=suspended` يُطبَّق على الراوتر كله ⇒ يُنقل إلى مستوى المسار).
3. **الحجم (+2,179 py > 1,700):** **مقبول لهذه المرة** — الفائض في A (حرّاس + تدقيق + flags cache = +1,199) بنية مشتركة لا تتكرر. **لكن** 13.1-FIX ≤ **500** py و SPRINT-14 يُقسَّم مسبقاً (§6 من التوجيه الجديد).
4. **`openssl` افتراضياً** (مُثبَت في CI، بلا ثنائي إضافي)؛ `age` اختياري عبر `BACKUP_CIPHER=age` حين يقرر المالك. لا تبعية Python جديدة (D-054).
5. **`RETENTION_DRY_RUN=true` يبقى** حتى أسبوع كامل من صفوف `admin_actions(action='retention_sweep')` تُراجَع يدوياً، **ولا يُقلَب قبل تنفيذ القرار 1**.

---

## 5. القرار على PR #55

**❌ طلب تصحيح على الفرع قبل الدمج** (الخيار الثاني في تفويض المالك). السبب — ثلاثة كسور تمسّ `main` فور الدمج لو فُعِّلت الميزة:

- **S13-1** يحوّل أداة «إنقاذ» إلى **تسميم دائم لحلقة النشر** (استثناء كل دورة + تعطيل بقية الدفعة) بمجرد ضغطة requeue واحدة — أخطر من غياب الميزة.
- **S13-3/S13-4(ب)** = 500 على Must-Have C-1/C-2 في تكوينات مشروعة.
- **S13-2** يجعل A-2 غير قابل للاستخدام من الواجهة للحالة الوحيدة المهمة (علامة عميل).

ما يُقبل كما هو: الهجرة 0017، A-1، A-2 (API)، B كاملاً، C-3، UI 29. **لا إعادة كتابة؛ إصلاحات موضعية ≤ 500 سطر على `work/implementation` نفسه ⇒ CI ⇒ أراجع الفرق فقط ⇒ دمج.** قائمة الإصلاح الملزمة في `CURRENT_DIRECTIVE.md` (SPRINT-13.1-FIX).

---

## 6. انحرافات المنفذ (§6 من التوجيه) — الأحكام

| # | الانحراف | الحكم |
|---|---|---|
| 1 | PROTECTED 96 بدل 95 (`GET /admin/alerts`) | ✅ مقبول |
| 2 | 404 عبر `require_ops_admin` بدل 403 | ✅ مقبول (يوافق نمط 11.1) |
| 3 | B-1 عدّ `billable` محافظ (NULL = billable) | ✅ مقبول |
| 4 | C-2 `ai_cost_log` SET NULL | ✅ مقبول (D-075 لا نص) |
| 5 | unsubscribe `skipped` بلا عميل | ✅ مقبول — **لكن** المسار مع عميل وبلا مفتاح ⇒ 500 (S13-3) ❌ |
| 6 | الصمّام متجاوز | ✅ مقبول بشروط §4-3 |
| 7 | `content_items` خارج الاحتفاظ | ✅ صحيح — ومُثبَّت الآن كقاعدة (§4-1) |

---

## 7. ملاحظات غير مانعة تُرحَّل

- `scheduler_loop` يبتلع أي استثناء في `run_once` كدورة كاملة (`scheduler.py:352-358`)؛ مهمة واحدة سامة تحجب الدفعة. **13.1:** عزل الفشل لكل مهمة (`try` حول `_process`) + عدّاد `publish_cycle_poisoned`.
- `_RefreshGate`/`PLATFORM_FLAGS` داخل العملية ⇒ `--workers 1` يبقى شرطاً (N1).
- Playbooks `restaurants.json`/`clinics.json` إنجليزية بالكامل رغم واجهة عربية — SPRINT-14.
- `FORBIDDEN_FRAGMENTS` كحارس أخير مفيد **لو** طُبِّق على أسماء الأعمدة والقيم المعروفة بالأسرار فقط، لا على نص العملاء.

---

*المخطط — 2026-11-04 — PG 17.11 — مسبار 9/9 نُفِّذ وحُذف؛ لا تغيير في `src/ tests/ alembic/` على فرع التخطيط.*
