# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-13 (PR #55 @ `1738e56`) رُوجع مستقلاً على PostgreSQL 17.11 ([`sprints/SPRINT-13_REVIEW.md`](./sprints/SPRINT-13_REVIEW.md)): البوابات خضراء (ruff 0/0 · 0017 ذهاباً وإياباً · تكامل 153/0) **لكن الفرضيات الخمس S13-1..S13-5 ثبتت حيّاً** وأربعة معايير Must-Have مكسورة ⇒ **القرار: لا دمج؛ تصحيح على الفرع نفسه أولاً (SPRINT-13.1-FIX)**، ثم SPRINT-14 (الانضمام الذاتي بالعربية).

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | **`SPRINT-13.1-FIX`** (الآن) ⇒ `SPRINT-14-SELF-SERVE` (بعد دمج #55) |
| **الحالة** | `ISSUED` — المنفذ يبدأ فور دمج هذا التوجيه في `main` (D-063) |
| **يبدأ من** | `work/implementation` @ `1738e56` **نفسه** (لا فرع جديد، لا rebase): `git checkout work/implementation && git fetch origin && git merge origin/main` (يجلب توجيه المخطط فقط — لا تعارض في `src/`) |
| **الفرع المستهدف** | **PR #55 نفسه** — كوميت واحد `fix(sprint-13.1): requeue attempt continuity + D-070 + export allowlist/revisions + unsubscriber guard + DB-flag scheduler + admin brand view` + كوميت docs §6 |
| **الهجرة** | **لا هجرة.** 0017 مقبولة كما هي |
| **الحجم** | **≤ 500 سطر `src/*.py`** مُضاف (F-1..F-5 لا تُقطع؛ F-6/F-7 تُختزل عند الحاجة) |
| **CI** | 6/6 قبل طلب المراجعة؛ المخطط يراجع **الفرق** `1738e56..HEAD` ويعيد مسبار S13-1..5 حرفياً |
| **المالك** | 🔑 **تدوير التوكن (≥ 26 ظهوراً)** · `--workers 1` · TTFV/App Review بعد دمج #55 (§7) |

---

## 0. قواعد ثابتة (لا تتغير)

1. **لا تلمس:** `src/modules/auth/*`, `src/core/contract.py`, `src/core/public_fetch.py`, `pyproject.toml`, `src/modules/brand_brain/internal_metrics.py`. لا تبعية جديدة (D-054).
2. **لا `ErrorCategory` جديدة.** أكواد حرفية في `exceptions.py` للوحدة + `ar.json`.
3. **D-068:** `src/ui` عبر `ApiClient` فقط. **D-069:** لا نص عميل في `admin_actions`/`pulse_alerts`. **D-055:** كل فعل حدث. **D-070:** لا إعادة عمياء — **تشمل المسار الإداري**.
4. كل كوميت يُدفع فوراً؛ المنفذ لا يدمج (D-063). مسابير `_probe_*.py` لا تُرتكب.

---

## 1. SPRINT-13.1-FIX — النطاق (مرتَّب الأولوية؛ كل بند يحمل اختبار PG يعكس مخرجات المسبار في المراجعة §2)

| # | الكسر (المراجعة) | الإصلاح الملزم | اختبار القبول |
|---|---|---|---|
| **F-1** | S13-1: `IntegrityError uq_publish_attempts_job_no` بعد requeue ⇒ حلقة أبدية | `_REQUEUE_JOB` **لا يُصفّر `attempt_count`**؛ بدلاً منه `max_attempts = attempt_count + :grace` (`REQUEUE_GRACE_ATTEMPTS=3` افتراضياً) و`next_attempt_at=:now`؛ استجابة `requeue` تعرض `attempt_count` الحقيقي و`max_attempts` الجديد. **مرفوض:** حذف `publish_attempts` القديمة (سجل D-055) | `publish_attempts` = 1..6 بعد requeue + 3 دورات؛ المهمة تنتهي `failed` طبيعياً بلا استثناء |
| **F-2** | S13-1 (ج) D-070 | `OpsService.requeue` يرفض `last_error_class == 'ambiguous_outcome'` ⇒ **409 `manual_verification_required`** (نفس `content/service.py:316`) | 409 حيّاً + لا صف `admin_actions` |
| **F-3** | S13-1 (أ)+(ب) | قبل CAS: مهمة `queued/leased` للعنصر ⇒ **409 `publish_in_progress`**؛ `_ITEM_CAS` يفحص `rowcount==1` وإلا **409 `item_not_publish_failed`** مع rollback | retry-ثم-requeue ⇒ 409 لا 500؛ `archived` ⇒ 409 والمهمة تبقى `failed` |
| **F-4** | S13-3 + S13-4 + §4-2 | **(أ)** `get_unsubscriber`: `if client is None or settings.token_encryption_key is None: return None`. **(ب)** `build_zip`: **قائمة سماح أعمدة لكل كيان** (`EXPORT_COLUMNS` في `domain.py`)؛ `FORBIDDEN_FRAGMENTS` على **أسماء الأعمدة فقط**؛ أي `RuntimeError` ⇒ `LifecycleError('export_failed')`. **(ج)** كيانان جديدان: **`content_revisions`** (`id,item_id,revision_no,body,media_refs,created_at`) و`brand_competitors`. **(د)** إعفاء `POST /brands/{id}/export` و`DELETE /brands/{id}` من `require_brand_not_suspended` (على المسار بدل `main.py:245`) | export بلا مفتاح ⇒ 202؛ رسالة `api_key` ⇒ 202 وتظهر نصاً؛ `revision_body_in_zip=True`؛ OWNER موقوف ⇒ 202/204؛ grep `gAAAA` ⇒ 0 |
| **F-5** | S13-2 | **`GET /api/v1/admin/brands/{brand_id}`** (`require_ops_admin`؛ `id,name,slug,suspended_at,owner_tenant_id` فقط) + **`GET /api/v1/admin/brands/{brand_id}/whatsapp/summary`** (`display_phone,status,quality_rating,messaging_limit`) ⇒ `_admin_ent_ctx` يستهلكهما. `PROTECTED_ROUTE_COUNT` ⇒ **98** | أدمن غير عضو ⇒ 200 + البطاقات الثلاث؛ غير الأدمن ⇒ 404 |
| **F-6** | S13-5 | `pulse_step` (و`lead_loop` إن كانت له خطوة) يقرأ `flag_active(repo, settings, "feature_pulse")` **داخل الخطوة كل دورة** عبر `independent_session`؛ `settings.feature_pulse=false` يبقى يمنع التركيب | علم DB=false ⇒ `pulse_step ⇒ 0` ولا صف `pulse_alerts` |
| **F-7** | المراجعة §7 | `run_once`: `try/except Exception` حول `_process(job)` لكل مهمة + `counters.increment("publish_job_poisoned")` + `logger.exception(job_id)` | مهمة سامة لا تمنع التالية (وحدة بـfake يرمي) |

**أكواد حرفية جديدة:** `item_not_publish_failed`، `export_failed` (+ إعادة استخدام `manual_verification_required`، `publish_in_progress`). ترجمة `ar.json` لكلٍّ.

### 1-ب. أوامر التحقق (ينسخها المنفذ إلى §6)
```
ruff check . && ruff format --check .
TEST_DATABASE_URL=… pytest tests/integration -q                       # ≥ 153 + الجديدة
pytest tests -q -m "not integration" --cov=src --cov-fail-under=95
# 98 محمياً / 9 عامة / /app 29 (test_auth_guard + test_ui_session)
git diff 1738e56..HEAD --numstat -- 'src/*.py' | awk '{a+=$1} END{print a}'   # ≤ 500
```

---

## 2. SPRINT-14 — الانضمام الذاتي بالعربية (Zero-API onboarding) — **يُصدَر رسمياً بعد دمج #55؛ النطاق مُجمَّد هنا ليبدأ التحضير**

**الهدف القابل للقياس:** مستخدم جديد يصل من `/app/login` إلى **أول مسودة معتمدة ومجدولة** دون أي نداء API يدوي، بالعربية، خلال ≤ 30 دقيقة (TTFV). كل شاشة تستهلك مسارات API **موجودة** — لا منطق أعمال جديد في `src/ui`.

### 14-A (PR أول — ≤ 900 py+html) — التسجيل والعلامة والقناة
| # | الشاشة/البند | يستهلك | ملاحظات |
|---|---|---|---|
| A-1 | `GET/POST /app/register` (بريد + كلمة مرور + اسم المستأجر) ⇒ دخول تلقائي ⇒ `/app/` | `POST /auth/register` ثم `POST /auth/login` | نفس حارس rate-limit؛ CSRF nonce؛ `email_taken` بالعربية |
| A-2 | `brands.html` حالة فارغة ⇒ «أنشئ علامتك الأولى» (الاسم، slug تلقائي، **القطاع**) | `POST /brands` ثم `PUT /brands/{id}/brain/profile` (sector) | بعدها ⇒ `/app/b/{id}/settings#connect` |
| A-3 | **إكمال ربط Meta:** اختيار الصفحات بعد OAuth (`/app/b/{id}/settings/connections/{cid}/pages` GET/POST) | `GET …/connections/{cid}` + `PUT …/pages/selection` | اليوم API فقط ⇒ القناة لا تكتمل من الواجهة |
| A-4 | **نموذج WhatsApp Cloud API** في `settings.html` (`phone_number_id, waba_id, system_token, app_secret, verify_token`) + تدوير | `POST/PUT …/whatsapp/account`, `POST …/account/rotate` | حقول سرّية `type=password`؛ لا تُعاد في HTML |
| A-5 | شريط تقدّم الانضمام في `/app/` (حساب ✓ · علامة · قناة · أول مسودة) | `GET /brands`, `GET …/connections`, `GET …/content/items` | يختفي عند الاكتمال |

### 14-B (PR ثانٍ — ≤ 900 py+html) — Brand Brain والمحتوى
| # | البند | يستهلك |
|---|---|---|
| B-1 | محرّر **Brand Profile** (`/app/b/{id}/brain`): القطاع، النبرة، الجمهور، العبارات الممنوعة، `If-Match` | `GET/PUT …/brain/profile` |
| B-2 | المنافسون ≤ 3 (إضافة/حذف/تحديث) + «حدّث الأدلة» | `…/competitors`, `…/competitors/{id}/refresh`, `…/evidence/refresh` |
| B-3 | زر **«خطة محتوى»** في `diagnosis.html` | `POST …/brain/content-plan` |
| B-4 | **`+ مسودة جديدة`** في `content.html` (القناة، النص، `target_page_id` **قائمة من الصفحات المختارة**، تاريخ الجدولة) | `POST …/content/items`, `GET …/connections` |
| B-5 | رسائل الموافقة الجماعية بالعربية لكل عنصر + `ar.json` ⊇ كل أكواد 13/13.1 | `POST …/content/approvals/batch` |
| B-6 | **Playbooks عربية:** `restaurants.json`/`clinics.json` تُعرَّب كاملةً مع `lang: "ar"`؛ الإنجليزية تُحفظ `*_en.json` | — |

### 14-C (بعد V1 — لا يُنفَّذ الآن)
N1 تعدد العمال (`_RefreshGate`/`PLATFORM_FLAGS` ⇒ PG advisory/Redis) · M8 الرؤى · تقرير أسبوعي · احتفاظ المحتوى (§4).

---

## 3. معايير قبول SPRINT-14 (مختصرة — تُفصَّل عند الإصدار)
1. مسار كامل بالمتصفح (TestClient بالكوكيز): register ⇒ brand ⇒ pages ⇒ WA ⇒ profile ⇒ draft ⇒ approve ⇒ schedule — **0 نداء API مباشر**.
2. لا `httpx`/`sqlalchemy` في `src/ui` (AST) · CSRF على كل POST · لا سرّ في HTML (`grep gAAAA|system_token`).
3. `ar.json` يغطي 100% من أكواد الأخطاء (اختبار يجمعها من `exceptions.py` لكل وحدة).
4. TTFV مُقاس من المالك على 3 علامات ≤ 30 دقيقة.

---

## 4. قرارات مُثبَّتة من المراجعة (ملزمة)
- **لا `DELETE FROM content_items` في الاحتفاظ، أبداً** (`fk_approval_events_item_id` CASCADE يمحو سجل الموافقات؛ `fk_publish_jobs_revision_id` RESTRICT). المحتوى = إفراغ `content_revisions.body/media_refs` للعناصر `archived/published/rejected` الأقدم من القطع فقط — 14-C، dry-run أولاً.
- OWNER لعلامة موقوفة **يصدّر ويحذف** (F-4 د) — D-081 حق لا يُعلَّق.
- `openssl` افتراضي؛ `age` اختياري بـ`BACKUP_CIPHER`. `RETENTION_DRY_RUN=true` يبقى حتى أسبوع من صفوف `retention_sweep` تُراجَع يدوياً.
- الحجم: S13 (+2,179) مقبول استثناءً (بنية A مشتركة)؛ 13.1 ≤ 500؛ 14 نصفان ≤ 900 لكلٍّ.
- **القطاع الثالث:** `beauty_salons` (مواعيد + واتساب كالعيادات) يُضاف في 14-B بهجرة **0018** توسّع `ck_brand_profiles_sector` + `beauty_salons.json` عربي. المالك يعترض خلال نافذة 13.1 إن أراد بديلاً.

---

## 5. التزامات المنفذ
- اختبار PG لكل بند F-1..F-7 **يعكس مخرجات المسبار حرفياً** (المراجعة §2).
- `PROTECTED_ROUTE_COUNT == 98`؛ `/app` 29؛ لا تغيير في `PUBLIC`.
- §6 بالأسماء من الكود + أرقام CI + الحجم المقاس بالأمر أعلاه.

## 6. تقرير المنفذ (يملؤه المنفذ)

**الحالة:** `SPRINT-13.1-FIX` **مُسلَّم** على PR [#55](https://github.com/belalalibb/ai_agancy_markting/pull/55) (`work/implementation`) — **متوقف** بانتظار مراجعة المخطط (D-063، لا دمج).

### 6-أ. الكوميتات
| # | SHA | المحتوى |
|---|---|---|
| 0 | `f27a237` | دمج `origin/main @ 6aab985` في `1738e56` (تعارضان في الوثيقتين ⇒ `--theirs`؛ `git diff --stat 1738e56 f27a237 -- src tests alembic` فارغ). |
| 1 | **`5c3b0ba`** | `fix(sprint-13.1): …` — F-1..F-7 + الاختبارات + REMOVABILITY (شجرة مطابقة لـ `afdcebe` التي مرّ عليها CI 6/6 قبل السحق؛ السحق غيّر الرسالة فقط). |
| 2 | (هذا الكوميت) | `docs(sprint-13.1): §6 report` — §6 هنا + `WORK_STATE.md`. |

### 6-ب. ما نُفِّذ (F-1..F-7)
| بند | التنفيذ | الدليل (اختبار) |
|---|---|---|
| F-1 | `_REQUEUE_JOB` لا يصفّر `attempt_count`؛ `max_attempts = attempt_count + :grace` (`REQUEUE_GRACE_ATTEMPTS = 3` في `ops/domain.py`)؛ `publish_attempts` لا تُمَس؛ الاستجابة و`admin_actions.after` تحملان `attempt_count` الحقيقي و`max_attempts` الجديد. `ambiguous_outcome` ⇒ **409 `manual_verification_required`** قبل أي تغيير (D-070). | PG: `test_f1_requeue_keeps_attempt_continuity_1_to_6_without_integrity_error` (run_once ×3 ⇒ 1..3 + failed ⇒ requeue ⇒ (3, 6) ⇒ run_once ×3 ⇒ 4..6 ⇒ failed طبيعياً، 6 استدعاءات للناشر، صفر IntegrityError) · `test_f1_ambiguous_outcome_is_409_manual_verification_required` (صفر `admin_actions`). وحدات: `test_ops_routes.py` ×2، `test_postgres_repository.py` (SQL بلا `attempt_count = 0`). |
| F-2 | `item_has_active_job(item_id)` (`status IN ('queued','leased')`) ⇒ **409 `publish_in_progress`** قبل CAS. | PG: `test_f2_owner_retry_then_admin_requeue_is_409_publish_in_progress` (لا 500 من `uq_publish_jobs_active_item`). |
| F-3 | `_ITEM_CAS` يجب أن يُصيب صفاً واحداً وإلا `ItemNotPublishFailedError` ⇒ **409 `item_not_publish_failed`**؛ معاملة الطلب تُرجِع تحديث المهمة (تبقى `failed`). | PG: `test_f3_archived_item_is_409_item_not_publish_failed_and_job_stays_failed` (المهمة `('failed', 3, 3)`، العنصر `archived`، صفر `admin_actions` وصفر `approval_events`). |
| F-4 | (1) `get_unsubscriber`: `None` إذا `client is None or token_encryption_key is None`؛ `decrypt_token` محروس ⇒ `unsubscribe='failed'` والحذف يمضي. (2) `EXPORT_COLUMNS` قائمة سماح صريحة لكل كيان في `lifecycle/domain.py` بدل `row_to_json(t)` + `_DROP_FRAGMENTS`؛ كيانان جديدان `content_revisions` و`brand_competitors` (الأعمدة كما في التوجيه حرفياً). (3) `FORBIDDEN_FRAGMENTS` على **أسماء المفاتيح فقط**؛ أي خطأ تصدير غير متوقَّع ⇒ `LifecycleError('export_failed')` (500 بكود، لا `RuntimeError`). (4) `include_router(lifecycle_router)` بلا `dependencies=suspended` (`platform_readonly_guard` باقٍ على `api_v1`). | PG: `test_f4_export_and_delete_without_encryption_key_and_with_http_client` (202/204، `unsubscribe=skipped`) · `test_f4_export_has_revisions_competitors_and_customer_text_with_secret_words` (202؛ ZIP يحوي نص المراجعة والمنافس ورسالة العميل «api_key … gAAAA»؛ grep صفر لـ `access_token/refresh_token/encrypted_/_hash/consent`؛ `gaaaa` مرة واحدة = كلمات العميل) · `test_f4_suspended_brand_owner_can_still_export_and_delete` (202/204). وحدات: `test_lifecycle.py` ×5 جديدة، `test_postgres_repository.py` (allowlist). |
| F-5 | `GET /admin/brands/{brand_id}` ⇒ `id, name, slug, suspended, suspended_at, owner_tenant_id` · `GET /admin/brands/{brand_id}/whatsapp/summary` ⇒ `id, display_phone, status, quality_rating, messaging_limit` (404 بلا حساب)؛ غير الأدمن ⇒ 404 `not_found`. `_admin_ent_ctx` يقرأ المسارين بدل مسارات المستأجر. **`PROTECTED_ROUTE_COUNT == 98`**. | PG: `test_f5_non_member_platform_admin_sees_three_cards_and_saves_wa_caps` (أدمن من مستأجر `stranger` بلا منحة ⇒ 200 + `data-suspended="true"` + `entitlement` + `wa-caps` بـ`account_id` صحيح ⇒ حفظ السقف يُثبت `(50, true)` في `wa_number_caps`) · `test_f5_non_admin_gets_404_on_both_admin_brand_routes`. وحدات: `test_ops_routes.py`، `test_ui_admin.py` (3 بطاقات). |
| F-6 | `pulse_step(flag_reader=…)` منفذ D-016؛ `main.py::pulse_scheduler_step` يحقنه عبر `flag_active(PgOpsRepository(session), settings, "feature_pulse")` داخل `independent_session` كل دورة؛ `False` ⇒ `0` بلا صف. لا استيراد `ops` داخل `pulse`. `settings.feature_pulse=false` ما زال يمنع التركيب. | PG: `test_f6_feature_pulse_off_in_platform_flags_skips_pulse_cycle` (`PUT /admin/flags/feature_pulse` false ⇒ `step(now) == 0` وصفر `pulse_alerts` رغم `quality_rating='RED'`؛ true ⇒ ≥ 1). وحدة: `test_pulse_step_skips_cycle_when_db_flag_is_off`. |
| F-7 | `run_once`: `try/except Exception` حول `_process` لكل مهمة ⇒ `counters.increment("publish_job_poisoned")` + `logger.exception("publish: poisoned job=%s")` والمتابعة. | وحدة: `test_poisoned_job_is_counted_and_rest_of_batch_still_processed` (الأولى ترمي ⇒ العدّاد 1، الثانية في نفس الدفعة `SUCCEEDED`). |
| ar.json | `error.manual_verification_required` · `error.item_not_publish_failed` · `error.export_failed` (و`publish_in_progress` موجود سلفاً). | اختبارات i18n ضمن unit الأخضر. |

### 6-ج. أوامر التحقق (§1-ب) — النتائج
```
ruff check . && ruff format --check .            ⇒ All checks passed! / 428 files already formatted
pytest tests/integration -q                      ⇒ CI (PG16 pgvector): 163 passed (153 + 10 جديدة في test_sprint13_1_pg.py)
pytest tests -q -m "not integration" --cov=src   ⇒ CI: 1630 passed, 1 skipped — TOTAL 95.16% (≥ 95.00)
PROTECTED_ROUTE_COUNT                            ⇒ 98 (test_auth_guard.py:98 + content/test_router.py:1478) · العامة 9 · /app 29 (26 محروساً) — بلا تغيير في UI routes
git diff 1738e56..HEAD --numstat -- 'src/*.py' | awk '{a+=$1} END{print a}'   ⇒ 462   (الشجرة كاملة: src/**/*.py ⇒ 462 ≤ 500)
```
**CI على الشجرة النهائية (`afdcebe` ≡ `5c3b0ba`):** run [41814237334](https://github.com/belalalibb/ai_agancy_markting/actions/runs/41814237334) — lint ✅ · unit ✅ (1630 / 95.16%) · integration ✅ (163) · secrets ✅ · licenses ✅ · restore-drill ✅ (`restore_pg seconds=0`, 109,232 bytes). **6/6.**

### 6-د. الحدود (§0) — مُحترَمة
- صفر تغيير في `src/modules/auth/*`، `src/core/contract.py`، `src/core/public_fetch.py`، `pyproject.toml`، `src/modules/brand_brain/internal_metrics.py`، `alembic/versions/*` (0017 نهائية؛ لا هجرة).
- لا `httpx`/`sqlalchemy`/`.infrastructure` في `src/ui/*.py` أو القوالب (الاختبار AST القائم أخضر). لا `_probe_*.py` مُلزَم. لا `ErrorCategory` جديد.
- `REMOVABILITY.md` لـ`ops` (8 ⇒ 10 مسارات + الأكواد الثلاثة + منفذ F-6)، `lifecycle` (التركيب خارج `suspended`، `EXPORT_COLUMNS`، `export_failed`)، `pulse` (`flag_reader`).

### 6-هـ. انحرافات/ملاحظات للمخطط
1. **`export_failed` = 500** (لا 502): الخطأ داخلي (SQL/ZIP) وليس من طرف ثالث؛ الكود حرفي كما طلب التوجيه. سهل التغيير إن رأى المخطط 502.
2. **`FORBIDDEN_FRAGMENTS`** وُسِّعت بـ`secret`/`hash` (أسماء مفاتيح فقط) كحزام أمان فوق قائمة السماح؛ لا تأثير على نص العملاء.
3. **`AdminWaSummary.status`** يُعاد كما في العمود؛ `quality_rating`/`messaging_limit` قد تكون `NULL` (أعمدة 0017) ⇒ `None` في JSON.
4. اختبارات PG الجديدة **متزامنة** تعمل بمحرك قصير العمر لكل خطوة (`_async`) بسبب `asyncio_mode=auto` + ارتباط اتصالات asyncpg بالحلقة — نفس نمط `_pulse_run`/`_sweep` في ملفات SPRINT-12/13. أضيفت `_fresh_world()` تُفرغ `admin_actions`/`platform_flags` (التي يسبقها `_seed_world` القديم).
5. اختبار A-3 القديم في `test_sprint13_a_pg.py` كان يُثبّت التصفير (`attempt_count == 0`) — عُدِّل إلى `(3, 6)` لأنه كان يختبر الخلل نفسه (S13-1).
6. CI: دفعتان وسيطتان فشلتا في `integration` (خلل في الاختبارات الجديدة لا في الكود: `asyncio.run` داخل حلقة، `ck_content_items_scheduled_for_iff_scheduled` في بذرة F-2، عمود `created_at` غير موجود في `social_connections`، تسرّب `admin_actions` بين الاختبارات) — أُصلحت كلها وسُحقت في `5c3b0ba`.
7. الساندبوكس أُعيد ضبطه ≥ 8 مرات أثناء التنفيذ؛ الدفع بعد كل شريحة حفظ العمل. السويت الكاملة لا تُشغَّل محلياً (985 MB) ⇒ الاعتماد على CI للأرقام.
8. 🔑 التوكن ظهر في المحادثة مجدداً (الآن ≥ 30 ظهوراً) — **التدوير فوري**.

## 7. ما ينتظر المالك
- 🔑 **تدوير توكن GitHub فوراً (≥ 26 ظهوراً)** + حماية `main`.
- TTFV على 3 علامات + فيديوهات App Review **بعد دمج #55** (A-2/C-1 يجب أن تعمل في الفيديو).
- قرار القطاع الثالث (§4) خلال نافذة 13.1.
