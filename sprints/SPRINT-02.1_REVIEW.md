# 📋 SPRINT-02.1-HOTFIX — تقرير مراجعة المخطط

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-02.1-HOTFIX` |
| **المُراجِع** | AI Master Planner |
| **تاريخ المراجعة** | 2026-10-29 |
| **الفرع المُراجَع** | `work/implementation` @ `4480309` (كود `7cf29df` فوق `main` @ `59363e2`) |
| **Pull Request** | [#5](https://github.com/belalalibb/ai_agancy_markting/pull/5) — **دُمج** في `main` @ `df01260` بتفويض المالك («إن اجتاز المراجعة بلا ثغرات، اعتمد فتح/دمج PR #5») |
| **الحكم النهائي** | 🟢 **مقبول — بلا ثغرات حاجبة.** ملاحظتان غير حاجبتين رُحِّلتا إلى SPRINT-03 (§4). |

> المراجعة أُجريت على نسخة طازجة من الفرع في worktree معزول، ببيئة أُعيد ضبطها بالكامل (لا أثر لأي تشغيل سابق؛ PostgreSQL أُعيد تثبيتها من الصفر). **لم يُؤخذ أي رقم من تقرير المنفذ كحقيقة** — كل سطر في §1 نفّذه المخطط بنفسه.

---

## 1. ما تحقق منه المخطط فعلياً

| الفحص | نتيجة المخطط | ادعاء المنفذ | تطابق |
|---|---|---|---|
| `pytest -m "not integration"` | **204 passed, 1 skipped** | 204 | ✅ |
| `pytest -m integration` ضد **PostgreSQL 17.11 + pgvector** حقيقية | **11 passed** | 11 | ✅ |
| الحزمة كاملة + تغطية | **215 passed، 99%** (842 عبارة، 1 غير مغطاة: `exceptions.py:118`) | 215 / 99% | ✅ |
| F1 — `test_oauth_state_tampered_rejected` ×50 | **0/50 فشل** | 50/50 | ✅ |
| هجرة `0002` على Postgres حقيقية: `head` ⇒ `downgrade 0001` ⇒ `upgrade head` | `is_nullable`: **YES ⇒ NO ⇒ YES**؛ `alembic current` = `0002 (head)` | ✓ | ✅ |
| `/health/ready` **حيّاً** عبر التبعية الحقيقية (لا override): Postgres تعمل / منفذ ميت 5999 / قاعدة غير موجودة | **200 / 503 / 503**؛ الجسم `{"status":"not_ready","database":"unreachable"}` بلا DSN أو رسالة سائق؛ lifespan يُغلق نظيفاً في الحالات الثلاث | 200 / 503 | ✅ |
| `ruff check` / `ruff format --check` | نظيفان (41 ملفاً) | نظيفان | ✅ |
| فحص الأسرار (أنماط `ghp_`, `EAA…`, `AKIA`, `META_APP_SECRET=`) | نظيف؛ `.env` غير متتبع | نظيف | ✅ |
| `grep -rn _fallback_identity src` | **فارغ** (الظهور الوحيد في `tests` هو H8 الذي يثبت غيابه) | ✓ | ✅ |
| الملفات المحظورة (`ARCHITECTURE_LOCK`, `SESSION_RECOVERY`, `imaster_planner`, `Hub/`, `sprints/`, `README`) | `git diff --stat origin/main..HEAD` ⇒ **لا شيء** | لم تُمس | ✅ |
| تراخيص شجرة التشغيل (venv نظيف، 36 حزمة) | **لا GPL/AGPL/LGPL/UNKNOWN**؛ الأبعد: `certifi` MPL-2.0 (مقبول) | — | ✅ (فحص إضافي تمهيداً لبوابة الترخيص في SPRINT-03) |

---

## 2. المراجعة البندية F1–F8

| # | المطلوب في التوجيه | ما وُجد في الكود | الحكم |
|---|---|---|---|
| **F1** | اختبار التلاعب بالتوقيع حتمي | `tests/core/test_oauth_state.py:64-72`: فك base64url للتوقيع ⇒ `sig_bytes[0] ^= 0x01` ⇒ إعادة ترميز. تلاعب على مستوى البايت لا الحرف؛ `verify()` لم تُمس | ✅ |
| **F2** | `/debug_token` يُفحص `is_valid` + `app_id` | `service.py`: `not debug.is_valid` ⇒ `MetaTokenInvalidError(190)`؛ `app_id != settings.meta_app_id` ⇒ `MetaTokenForeignAppError`؛ **إضافة لم تُطلب لكنها صحيحة:** `debug.user_id != me.id` ⇒ `MetaTokenForeignAppError` (يمنع خلط هوية التوكن بهوية `/me`) | ✅ |
| **F3** | الهوية من `GET /me?fields=id,name` + `appsecret_proof`؛ حذف `_fallback_identity` | `meta_client.py:182-190`: `get_me` بـ `fields=id,name` + `appsecret_proof`؛ `service.py`: `provider_user_id=me.id, provider_user_name=me.name`؛ الرمز محذوف | ✅ |
| **F4** | `encrypted_page_token` NULLable + هجرة 0002 بنزول آمن | `orm.py:81` `Text, nullable=True`؛ `models.py:52` `str \| None`؛ `0002_page_token_nullable.py`: `downgrade()` يُعيد `''` للصفوف NULL **قبل** `nullable=False` (لا يفشل على بيانات موجودة) — تحقق عملياً على PG | ✅ |
| **F5** | لا نداء Graph بعد توكن غير صالح | الرمي يسبق `get_user_pages`؛ H4 يثبت `pages_route.call_count == 0` | ✅ |
| **F6** | `engine.dispose()` عند الإغلاق | `main.py` lifespan: `if database.get_engine.cache_info().currsize > 0: await get_engine().dispose()` — يُفرّغ فقط إن أُنشئ المحرك؛ اختباران (`skips_dispose…`, `disposes_engine…`) | ✅ |
| **F7** | `/health/ready` بجولة DB حقيقية، 503 عند الفشل، بلا تسريب | `main.py:77-92`: `SELECT 1` عبر `Depends(get_db_session)`؛ يلتقط `(SQLAlchemyError, OSError)`؛ الجسم ثابت | ✅ (انظر §3) |
| **F8** | سقف ترقيم `/me/accounts` | `meta_client.py:48` `MAX_PAGINATION_REQUESTS = 50`؛ L212 يرمي عند التجاوز | ✅ |

**اختبارات حُذفت/أُعيدت تسميتها:** الأربعة المذكورة في تقرير المنفذ مبررة (كانت تثبّت السلوك الخاطئ الذي ألغاه F3/F4). لا حذف لاختبار كان يحمي سلوكاً صحيحاً.

**انحراف المنفذ #2 (`has_page_token` vs حارس Zero-Secret):** الاستثناء مقيَّد بالاسم وبالنوع `bool` عبر `_ALLOWED_FLAG_FIELDS` و lookbehind `(?<!has_page_)token`. أي حقل آخر يحمل `token` يُفشِل الحارس. **مقبول.**

---

## 3. ملاحظة المالك التقنية: `Depends(get_db_session)` + `session.commit()` في `/health/ready`

**التحليل:**
- `get_db_session` تلتزم: `yield session` ⇒ `commit()` عند الخروج الطبيعي، `rollback()` عند أي استثناء.
- في `/health/ready` **الاستثناء يُلتقط داخل المعالج** ويُعاد `JSONResponse(503)`، فالمعالج يخرج «طبيعياً» ⇒ التبعية تنفّذ `commit()` على جلسة قد يكون اتصالها ميتاً.
- **سلوك SQLAlchemy الفعلي (تحقق عملياً بالحالات الثلاث):** إن فشل الاتصال في `SELECT 1`، لا تكون هناك معاملة مفتوحة أصلاً ⇒ `commit()` على جلسة بلا معاملة هو **no-op** ولا يرمي. لذا النتيجة 503 صحيحة، ولا يتسرّب استثناء إلى 500.
- **لكن** هذا الصواب **عرضي** (يعتمد على تفصيلة تنفيذ: عدم بدء المعاملة قبل أول `execute` ناجح). لو بُدّلت `get_db_session` مستقبلاً لتبدأ معاملة صريحة (`session.begin()`)، سيصبح `commit()` قابلاً للرمي خارج `try` ويحوّل 503 إلى 500 غير مُتحكَّم به.
- فحص الجاهزية لا يحتاج جلسة ORM ولا معاملة ولا `commit` أصلاً؛ طبيعته هي `engine.connect()` + `SELECT 1`.

**الحكم:** **صحيح الآن، هشّ بنيوياً** — ليس ثغرة حاجبة. **يُرحَّل إلى SPRINT-03 (بند A6):** تحويل `/health/ready` إلى `async with get_engine().connect() as conn: await conn.execute(text("SELECT 1"))` بلا تبعية جلسة، مع إبقاء اختبارات 200/503 وإضافة اختبار يُثبت أن المعالج لا يعتمد على `get_db_session`.

---

## 4. ملاحظات غير حاجبة (مرحَّلة إلى SPRINT-03)

| # | الملاحظة | المعالجة |
|---|---|---|
| N1 | `/health/ready` عبر `Depends(get_db_session)` (§3) | SPRINT-03 بند **A6**: `engine.connect()` مباشرة |
| N2 | `provider_user_name` يُخزَّن الآن من `/me.name`. **تحقق المخطط من شرطَي المالك:** (أ) لا يُسجَّل — الظهور الوحيد لـ `logger.warning` في `service.py:161` يطبع نوع الاستثناء والكود فقط، لا حقول هوية؛ (ب) يُحذف عند `disconnect` — `PostgresRepository.delete` ينفّذ `DELETE` فعلياً (لا soft-delete) و`social_connection_pages` بـ `ondelete=CASCADE`. **الشرطان مستوفيان حالياً.** | SPRINT-03 بند **B7**: اختباران حارسان دائمان (لا يُسجَّل / يُمحى عند disconnect) حتى لا ينكسر الشرط مستقبلاً |
| N3 | تحذير `alembic`: `No path_separator found… Consider adding path_separator=os` | SPRINT-03 بند **A7** (سطر واحد في `alembic.ini`) |
| N4 | `StarletteDeprecationWarning: install httpx2` — خارجي | يُراقَب فقط، لا إجراء |

---

## 5. تقرير المنفذ المؤرشف (§7 من `CURRENT_DIRECTIVE.md` @ `4480309`)

- **حالة التنفيذ:** `[x] مكتمل`
- **Commit SHA:** `7cf29df` — كوميت واحد على `work/implementation` بالرسالة الإلزامية (فوق `59363e2` = `main`). 16 ملفاً، +813/−53.
- **تشغيل F1 50×:** `50/50 passed, failures=0`. الإصلاح: قلب بت واحد في أول بايت من التوقيع بعد فك base64url بدل تبديل آخر حرف (الذي يحمل بتّين فقط)؛ `verify()` لم تُمسّ.
- **`pytest -m "not integration"` / `-m integration` / الكل:** `204 passed` / `11 passed` / `215 passed` (كان 181 + 8 = 189).
- **التغطية:** `99%` (842 stmt، 1 غير مغطى: `exceptions.py:118` فرع `_as_int` الدفاعي).
- **ruff:** `ruff check src tests alembic` ⇒ All checks passed؛ `ruff format --check` ⇒ 41 files already formatted.
- **اختبارات حُذفت (بالاسم) وسببها:**
  1. `test_complete_connection_debug_token_failure_degrades_gracefully` — كان يثبّت الهوية المشتقة `"pages:1001,1002"` (F3 يلغيها). استُبدل بـ `test_complete_connection_debug_token_failure_keeps_identity_from_me`.
  2. `test_complete_connection_without_pages_and_without_identity` — كان يثبّت `"unknown"`. استُبدل بـ H3 `test_complete_connection_uses_me_for_identity_and_name`.
  - أُعيدت تسمية (لا حذف): `…_uses_debug_token_for_identity_and_expiry` ⇒ `…_for_scopes_and_expiry`؛ `test_page_without_access_token_is_stored_with_empty_ciphertext` ⇒ `…_is_stored_as_none`.
- **إصدار PostgreSQL المستخدم:** `PostgreSQL 17.11 (Debian)` + `pgvector` محلياً عبر apt (لا Docker في البيئة؛ `docker-compose.yml` لم يُمسّ). `alembic downgrade base` ⇒ `upgrade head` يُنشئ 0001 ثم 0002؛ `is_nullable = YES` بعد head.
- **فحص `/health/ready` حيّاً:** مع Postgres ⇒ `200`؛ مع DSN غير قابل للوصول ⇒ `503` ولا أثر لـ DSN/كلمة المرور/اسم السائق في الجسم. `/health` بلا تغيير.
- **فحوص القبول (§4):** `grep _fallback_identity` نظيف في `src`؛ `is_valid` سطر 94؛ `dispose` سطر 48؛ فحص الأسرار نظيف؛ لا ملف محظور مُسّ.
- **انحرافات:** (1) PR #3 أُغلق تلقائياً بدمج PR #4 ⇒ فتح PR جديد يحتاج قرار المخطط. (2) استثناء مسمّى `has_page_token` في حارسَي Zero-Secret مقيَّد بنوع `bool`. (3) حرفي `"unknown"` في `test_router.py` أُعيد تسميته `"unmapped"`. (4) رمي `MetaTokenInvalidError` يسبق `get_user_pages`. (5) انقطاع الجلسة ×3؛ الدفع الفوري أنقذ الكود.
- **أسئلة:** (1) PR جديد أم دمج مباشر؟ ⇒ **رد المخطط: فُتح PR #5 ودُمج.** (2) `provider_user_name` بيانات شخصية؟ ⇒ **رد المالك: مقبول كبيانات عرض بشرط ألا يُسجَّل وأن يُحذف عند `disconnect`** (تحقق المخطط §4-N2؛ حارس دائم في SPRINT-03).

---

## 6. ملاحظة عملية

ثلاثة سبرنتات متتالية بـ **انقطاع جلسة ×3** لكل منها، ولم يُفقد كوميت واحد لأن المنفذ يدفع فور كل كوميت. هذا الانضباط هو ما يجعل نموذج «الكود هو مصدر الحقيقة» يعمل، ويُثبَّت في SPRINT-03 كقاعدة إلزامية (كوميت واحد لكل Part + دفع فوري).

---

## 7. القرار

- ✅ **SPRINT-02.1 مقبول.** PR #5 دُمج في `main` @ `df01260`.
- ✅ خط الأساس المُتحقَّق الجديد: `main` @ `df01260` — 215 اختباراً، 99%، هوية Meta موثوقة من `/me`، تخزين دائم متعدد المستأجرين.
- ➡️ SPRINT-03 صادر في `CURRENT_DIRECTIVE.md` بالتزامن مع هذا التقرير (PR #6)، وفق قرارات المالك الخمسة.
