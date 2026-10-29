# 📋 SPRINT-01 — تقرير مراجعة المخطط وإغلاق السبرنت

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-01-FASTAPI-META-FOUNDATION` |
| **المُراجِع** | AI Master Planner |
| **تاريخ المراجعة** | 2026-10-29 |
| **الفرع المُراجَع** | `work/implementation` @ `ed5fb47` (كوميت الكود: `118c0bb`) |
| **Pull Request** | [#2](https://github.com/belalalibb/ai_agancy_markting/pull/2) |
| **الحكم النهائي** | ✅ **مقبول للدمج** — مع 4 إصلاحات حرجة مُرحَّلة إلزامياً إلى SPRINT-02 (Part A) |

> **لماذا هذا الملف؟** `CURRENT_DIRECTIVE.md` يُستبدل كل دورة فتضيع سجلات المراجعة وتقارير المنفذ. مجلد `sprints/` أرشيف تاريخي قابل للتدقيق (ليس ملف تحكم). ملفات التحكم تبقى ثلاثة فقط وفق الميثاق.

---

## 1. ملاحظة إجرائية: المنفذ عمل على Rev 1 لا Rev 2

- `git merge-base` بين فرع المنفذ وفرع المخطط = `555768d` ⇒ المنفذ قرأ **التوجيه الأصلي (Rev 1)** على `main`، ولم يرَ **Rev 2** الذي كان ينتظر الدمج في PR #1.
- **النتيجة:** يُحاكَم التنفيذ على معايير Rev 1 — وقد **حققها كاملة**. الفجوات مقابل Rev 2 ليست تقصيراً من المنفذ وتُرحَّل إلى SPRINT-02.
- **درس للعملية:** يجب دمج PR التوجيه في `main` **قبل** إطلاق المنفذ. أُضيفت قاعدة بذلك في `WORK_STATE.md`.

---

## 2. ما تحقق منه المخطط فعلياً (وليس من تقرير المنفذ)

| الفحص | الأمر | النتيجة |
|---|---|---|
| الاختبارات والتغطية | `pytest -q --cov=src --cov-report=term-missing` | **123 passed, 0 failed — 99.62%** (Python 3.13.14) |
| Lint / Format | `ruff check src tests` / `ruff format --check` | All checks passed / 26 files formatted |
| فحص الأسرار | `grep -rnE "EAA…\|sk-…\|ghp_…\|gsk_…" src tests scripts .env.template` | لا نتائج |
| الخادم | `uvicorn src.main:app` ثم `curl` على `/health`, `/docs`, `/openapi.json`, `/authorize-url` | 200 على الجميع؛ 3 مسارات + `/health` |
| الملفات المحظورة | `git diff --stat origin/main -- AI_Marketing_Platform_Hub ARCHITECTURE_LOCK.md imaster_planner.md SESSION_RECOVERY.md README.md` | فارغ |
| `appsecret_proof` | قراءة `meta_client.py` + الاختبارات | HMAC-SHA256 مُنفَّذ ومُرسَل مع `/me/accounts` |
| الترقيم | `get_user_pages` يتبع `paging.next` | مُنفَّذ ومُختبَر |
| تصنيف الأخطاء | `exceptions.py::map_graph_error` | 190/463/460/458 → Expired؛ 4/17/32/613/80001 → RateLimit؛ 10/2xx → Permission؛ 1/2/5xx → Transient |
| إخفاء التوكنات | `field_serializer` ⇒ `"***"` في JSON | مُختبَر في `test_router.py` |
| التشفير | Fernet عبر `MultiFernet` + `rotate()` | يتجاوز المطلوب |

---

## 3. مطابقة معايير القبول (Rev 1)

| المعيار | الحالة |
|---|---|
| تشغيل FastAPI وظهور `/docs` | ✅ |
| تشفير/فك تشفير ومطابقة الأصل | ✅ (+ رفض العبث + مفتاح خاطئ + تدوير) |
| دورة OAuth وتبادل التوكن بـ Mock | ✅ (`respx`) |
| استرجاع الصفحات وحسابات Instagram | ✅ (مع ترقيم) |
| معالجة `OAuthException` للتوكن المنتهي | ✅ → `MetaTokenExpiredError` → HTTP 401 |
| `pytest` Zero Failures | ✅ 123/123 |
| PEP 8 + Type Hints | ✅ (ruff + قواعد ANN) |
| لا أسرار | ✅ |

**الخلاصة: كل معايير Rev 1 محققة ومُتحقَّق منها بشكل مستقل.**

---

## 4. الملاحظات والفجوات (مُرتَّبة حسب الخطورة)

### 🔴 حرج — يُصلَح في بداية SPRINT-02 (Part A)

| # | الملاحظة | الدليل | القرار |
|---|---|---|---|
| C1 | **إصدار Graph API `v19.0` منتهي.** صدر يناير 2024؛ بسياسة السنتين انتهى مطلع 2026. الطلبات إليه تُخدَم بأقدم إصدار حيّ بصمت ⇒ تغيّر استجابات دون إنذار. | `config.py:48`, `.env.template:21` | تثبيت `v26.0` (صدر 2026-07-29) افتراضياً + اختبار يرفض أي إصدار أقل من `v21.0`. |
| C2 | **`state` لا يُتحقق منه خادمياً.** يُولَّد في `/authorize-url` ويُرجَع، لكن `/connect` يقبله اختيارياً ويتجاهله ⇒ حماية CSRF غير مكتملة. | `router.py`, `service.py`, `schemas.py::MetaAuthRequest.state` | state موقّع بـ HMAC (stateless) يحمل `tenant_id` + `nonce` + `issued_at`؛ يُرفض إن كان مجهولاً/منتهياً/مُعدَّلاً. |
| C3 | **الصلاحيات تطلب النشر** (`pages_manage_posts`, `instagram_content_publish`, `business_management`) رغم أن النشر خارج النطاق. مخالف لمبدأ أقل صلاحية ويُعقّد App Review. | `config.py:52-55`, `.env.template:22` | تقليص الافتراضي إلى `pages_show_list,pages_read_engagement,instagram_basic`. |
| C4 | **إعادة المحاولة على Rate Limit** (4/17/32/613 و HTTP 429) حتى 3 مرات. وثائق Meta: الاستمرار يُطيل الحظر. | `meta_client.py::_request_absolute` → `retryable = isinstance(error, MetaRateLimitError \| MetaTransientError)` | إعادة المحاولة **فقط** على `MetaTransientError` و timeouts. Rate Limit يُرفع فوراً مع `Retry-After`. |

### 🟠 معماري — جوهر SPRINT-02 (Part B)

| # | الملاحظة | القرار |
|---|---|---|
| A1 | **لا طبقة تخزين.** `MetaConnectionResult` يُرجَع للعميل ويُنسى؛ `encrypted_user_token` يُرسَل إلى المتصفح ("store as-is") ⇒ يحوّل مسؤولية التخزين للواجهة — غير مقبول لمنصة متعددة المستأجرين. | Repository Protocol + تنفيذ PostgreSQL (D-032) + عزل `tenant_id`. **لا يُرجَع أي حقل `encrypted_*` في أي استجابة.** |
| A2 | **توكنات الصفحات لا تُشفَّر ولا تُحفظ** (تُقنَّع فقط في JSON). | جدول `social_connection_pages` مع `encrypted_page_token`. |
| A3 | **`expires_in = 0` يُفسَّر "لا ينتهي أبداً".** Meta قد تُسقط الحقل؛ الافتراض خطر. | `None` = غير معروف، ثم استخدام `debug_token` (مُنفَّذ لكن غير مستخدم) لجلب `expires_at`, `scopes`, `user_id` الفعلية. |
| A4 | **`.env.template` يشير إلى `/callback` غير موجود.** | إضافة `GET /callback` يتحقق من state ويُكمل الربط ويحفظ. |

### 🟡 نطاق وتبعيات (مقبولة مع تسجيل)

| # | الملاحظة | القرار |
|---|---|---|
| S1 | أُضيفت `sqlalchemy` و `src/core/database.py` (محرك **متزامن** `psycopg`) دون ورودها في التوجيه. كسولة ولا تفتح اتصالاً. | مقبولة لأن D-032 يفرض PostgreSQL؛ تُستبدل بمحرك **async** (`asyncpg`) في SPRINT-02 لتوافق FastAPI. |
| S2 | أُضيف `scripts/backup_progress.sh` (git bundle + AI Drive). غير مطلوب لكنه مفيد للاستئناف. | مقبول. لا يحوي أسراراً. |
| S3 | `MetaPageInfo` يجلب `instagram_business_account{id,username}` فقط. | إضافة `name,profile_picture_url` في SPRINT-02. |
| S4 | مسار `POST /token` يُرجع توكنات مُقنَّعة `***` ⇒ لا فائدة للعميل ويزيد سطح الهجوم. | يُحذف في SPRINT-02. |

### ✅ تفوّق على المطلوب
- `MultiFernet` + `rotate()` لتدوير المفاتيح.
- `debug_token` جاهز.
- احترام ترويسة `Retry-After`.
- `sleep` قابل للحقن ⇒ اختبارات backoff سريعة.
- 123 اختباراً (Rev 1 لم يحدد عدداً؛ Rev 2 طلب 26).

---

## 5. الرد على سؤال المنفذ (401 مقابل 403)

**السؤال:** أخطاء الصلاحيات بنوع `OAuthException` (مثل `(#200) Requires pages_show_list`) تُصنَّف 401 أم 403؟

**قرار المخطط: الكود يسبق النوع.**
- `code in {10} ∪ [200..299]` ⇒ `MetaPermissionError` ⇒ **403** مهما كان `type`، مع `required_action: "reauthorize_with_additional_scopes"`.
- `code == 190` ⇒ `MetaTokenExpiredError` / `MetaOAuthError` ⇒ **401** مع `required_action: "reconnect"`.

**المبرر:** الواجهة تحتاج تمييز «التوكن ميت — أعد الربط» عن «التوكن حيّ لكن ينقصه إذن — اطلب صلاحية إضافية»؛ الأول يمسح الحساب من اللوحة، والثاني يعرض زر «منح صلاحية». النوع `OAuthException` تستخدمه Meta لكلا الحالتين فلا يصلح مُميِّزاً.

---

## 6. القرارات الإجرائية

1. **PR #2 (`work/implementation` → `main`): يُدمج.** يحقق Rev 1 كاملاً؛ الفجوات مُعالَجة في SPRINT-02.
2. **PR #1 (المخطط):** أُعيد بناؤه فوق كود المنفذ ليحوي فقط: هذا التقرير + توجيه SPRINT-02 + تحديث `WORK_STATE.md`. **يُدمج بعد PR #2.**
3. **قاعدة جديدة:** لا يبدأ المنفذ قبل دمج PR التوجيه في `main`.

---

## 7. تقرير المنفذ الأصلي (مؤرشف من `CURRENT_DIRECTIVE.md` @ `ed5fb47`)

- **حالة التنفيذ:** `[x] مكتمل ومختبر`
- **Commit SHA:** `118c0bbae1882012e1825fc1595d24db19489657`
- **pytest:** 123 passed, 0 failed (≈31s) — تغطية 99.6% — ruff: All checks passed.
- **الملفات:** `pyproject.toml`, `requirements*.txt`, `.env.template`; `src/core/{config,security,database}.py`; `src/modules/social_connect/{schemas,exceptions,meta_client,service,router}.py`; `src/main.py`; `tests/**` (9 ملفات)؛ `scripts/backup_progress.sh`.
- **ملاحظات المنفذ:** (1) كل معايير القبول محققة. (2) سؤال 401/403 — **أُجيب في §5**. (3) مقترح المهمة التالية: Repository في PostgreSQL + `/callback` خادمي يتحقق من `state` — **مُعتمَد ومُدرج في SPRINT-02**. (4) لا أسرار؛ ملاحظات bandit إيجابيات كاذبة مُعلَّقة. (5) تحذير Starlette/httpx توافقي لا يؤثر.
