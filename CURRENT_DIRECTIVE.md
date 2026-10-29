# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-02 رُوجع — النتيجة: **مقبول مشروطاً**؛ مراجعته الكاملة في [`sprints/SPRINT-02_REVIEW.md`](./sprints/SPRINT-02_REVIEW.md). هذا التوجيه هو **دفعة التصحيح** التي تفتح باب دمج PR #3.

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-02.1-HOTFIX` |
| **النوع** | تصحيح عيوب مُثبتة — **لا ميزات جديدة** |
| **الحالة** | `READY_FOR_EXECUTION` |
| **يبدأ من** | `work/implementation` @ `4f8345e` (رأس PR #3) — **لا تنتظر دمجاً**؛ هذا التوجيه موجود على `main` بعد دمج PR #4 |
| **الفرع المستهدف** | `work/implementation` (نفس PR #3 يُحدَّث؛ لا PR جديد) |
| **الحجم المتوقع** | كوميت واحد، ~150–250 سطر تغيير، ≤ 12 اختباراً جديداً |
| **تاريخ الإصدار** | 2026-10-29 |

---

## 0. لماذا هذا التوجيه؟

المخطط شغّل PR #3 بنفسه على PostgreSQL 17 + pgvector: **189 اختباراً ناجحاً، 8 تكامل، تغطية 99.56%، ruff نظيف** — عمل قوي. لكن المراجعة المتقاطعة (مخطط + مراقب خارجي + كود) كشفت **9 عيوب** منها 3 تمنع الدمج. كلها في الجدول أدناه بالملف والسطر. **لا شيء هنا قابل للنقاش أو يحتاج قرار مالك.**

| # | الملف:السطر | العيب | الخطورة |
|---|---|---|---|
| F1 | `tests/core/test_oauth_state.py:65-67` | اختبار متذبذب (~5.7%): تبديل آخر حرف base64url قد يفك لنفس البايتات | 🔴 يكسر Zero Failures عشوائياً |
| F2 | `service.py:80-84` | `/debug_token`: لا فحص لـ `is_valid` ولا `app_id` | 🔴 توكن باطل/تطبيق آخر يُحفظ كحساب نشط |
| F3 | `service.py:179-183` | `_fallback_identity` ⇒ `"unknown"`/`"pages:…"` ⇒ تصادم هويات وسحق سجلات | 🔴 فقدان بيانات |
| F4 | `orm.py:80`, `0001:77`, `service.py:149-151` | `encrypted_page_token NOT NULL` ⇒ `""` غير مشفرة ⇒ `decrypt("")` ينفجر | 🟠 |
| F5 | `service.py:96` | `provider_user_name=None` دائماً | 🟠 |
| F6 | `main.py:39` | لا `engine.dispose()` عند الإيقاف | 🟠 |
| F7 | `main.py:63` | `/health` ثابت؛ لا `/health/ready` | 🟠 |
| F8 | `meta_client.py:186, 221` | `while True` بلا سقف للترقيم | 🟠 |
| F9 | `WORK_STATE.md:12` | §1 لم يُحدَّث | 🟡 (أُصلح بهذا التوجيه) |

---

## 1. النطاق

### ✅ داخل النطاق
F1–F8 فقط، بالضبط كما هو موصوف في §2.

### 🚫 خارج النطاق — **ممنوع منعاً باتاً**
- أي تغيير في نموذج `tenant_id`/Brand (قرار مالك معلّق — SPRINT-03).
- CI / GitHub Actions (SPRINT-03).
- تغيير خوارزمية التشفير.
- حذف أو نقل أي ملف وثائقي.
- أي ميزة أو مسار أو جدول جديد غير المذكور.
- تعديل أي ملف محظور (`AI_Marketing_Platform_Hub/**`, `ARCHITECTURE_LOCK.md`, `imaster_planner.md`, `SESSION_RECOVERY.md`, `README.md`, `sprints/**`).

---

## 2. المواصفات الفنية

### F1 — إصلاح الاختبار المتذبذب (`tests/core/test_oauth_state.py`)
السبب الجذري: HMAC-SHA256 = 32 بايت = 43 حرف base64url؛ الحرف الأخير يحمل بتّين فقط، فـ `sig[:-1] + "A"` قد يساوي نفس البايتات.
**الإصلاح:** عدّل الاختبار ليُفسد **البايتات** لا الأحرف:
```python
sig_bytes = bytearray(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)))
sig_bytes[0] ^= 0x01                                   # قلب بت فعلي
flipped = base64.urlsafe_b64encode(bytes(sig_bytes)).rstrip(b"=").decode()
with pytest.raises(InvalidOAuthStateError, match="signature"):
    signer.verify(f"{body}.{flipped}")
```
**لا تغيّر `verify()`** — سلوكه صحيح. أضف تعليقاً يشرح السبب. تحقق: شغّل الاختبار 50 مرة متتالية في حلقة (`for i in $(seq 50); do pytest … -q || exit 1; done`) ⇒ 50/50.

### F2 + F3 + F5 — الهوية الموثوقة (`meta_client.py`, `service.py`, `schemas.py`)

**(أ) أضف إلى `MetaGraphClient`:**
```python
async def get_me(self, user_access_token: str) -> MetaUserInfo:
    """GET /me?fields=id,name — the canonical identity source."""
    payload = await self._request("GET", "/me", params={
        "fields": "id,name",
        "access_token": user_access_token,
        "appsecret_proof": self.appsecret_proof(user_access_token),
    })
    return MetaUserInfo(id=_require_str(payload, "id"), name=payload.get("name"))
```
`MetaUserInfo(BaseModel)`: `id: str`, `name: str | None = None` في `schemas.py`.

**(ب) أضف إلى `MetaDebugTokenInfo`** حقلاً اختيارياً `error_code: int | None = None` يُقرأ من `data.error.code` إن وُجد.

**(ج) أعد كتابة خطوة الهوية في `complete_connection`:**
```python
me = await self._client.get_me(long_lived)          # إلزامي؛ أي MetaGraphError هنا فادح (يُرفع كما هو)
debug = await self._inspect_token(long_lived)       # اختياري كما هو الآن

if debug is not None:
    if not debug.is_valid:
        raise MetaTokenInvalidError(...)             # جديد: subclass من MetaOAuthError، code=debug.error_code or 190
    if debug.app_id and debug.app_id != self._settings.meta_app_id:
        raise MetaTokenForeignAppError(...)          # جديد: subclass من MetaOAuthError
    if debug.user_id and debug.user_id != me.id:
        raise MetaTokenForeignAppError("debug_token user_id does not match /me")   # دفاع إضافي

provider_user_id   = me.id
provider_user_name = me.name
granted_scopes     = list(debug.scopes) if debug else []
token_expires_at   = _expiry_from_debug(debug) if debug else None
```
- **احذف `_fallback_identity` نهائياً** مع اختباراته. الهوية الآن من `/me` دائماً؛ لا hash ولا `"unknown"`.
- الاستثناءان الجديدان في `exceptions.py` يُحوَّلان في `router.py::_to_http` إلى **401** مع `required_action: "reconnect"`.
- حدّث docstring الخدمة.

### F4 — توكن الصفحة الغائب = `NULL`
- `orm.py`: `encrypted_page_token: Mapped[str | None] = mapped_column(Text, nullable=True)`.
- **هجرة جديدة `0002_page_token_nullable`**: `op.alter_column("social_connection_pages", "encrypted_page_token", nullable=True)` مع `downgrade` يعيد `nullable=False` **بعد** `UPDATE … SET encrypted_page_token = '' WHERE encrypted_page_token IS NULL` (وإلا يفشل الـ downgrade على بيانات حقيقية). **لا تعدّل `0001`.**
- `domain/models.py`: `PageAsset.encrypted_page_token: str | None`.
- `service.py::_to_page_asset`: `None` إن غاب التوكن؛ **لا تشفّر سلسلة فارغة أبداً**.
- `postgres_repository.py`: تأكد أن `None` يمر للإدراج والقراءة.
- `PageAssetPublic` (الاستجابة العامة): أضف `has_page_token: bool` (مشتق) ليعرف العميل أن الصفحة بلا صلاحية — **لا تضف التوكن نفسه**.

### F6 — إغلاق المحرك (`main.py`)
في `lifespan` بعد `yield`: إن كان المحرك قد أُنشئ (`get_engine.cache_info().currsize > 0` أو علم في `app.state`) ⇒ `await get_engine().dispose()`. لا تُنشئ المحرك لمجرد إغلاقه.

### F7 — فحص الجاهزية (`main.py`)
```python
@app.get("/health/ready", tags=["System"])
async def ready(session: Annotated[AsyncSession, Depends(get_db_session)]) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok"}
```
- عند فشل الاتصال ⇒ **503** `{"status": "not_ready", "database": "unreachable"}` (التقط `SQLAlchemyError`/`OSError` بـ exception handler محلي، لا تُسرّب DSN).
- `/health` يبقى كما هو (liveness).

### F8 — سقف الترقيم (`meta_client.py`)
- ثابت `MAX_PAGINATION_REQUESTS: int = 50` على مستوى الوحدة.
- في `get_user_pages`: عدّاد؛ عند تجاوزه ⇒ `raise MetaGraphError("Pagination exceeded MAX_PAGINATION_REQUESTS", http_status=200)`.
- حلقة `_request_absolute` (إعادة المحاولة) محدودة أصلاً بـ `_max_retries` — **تحقق** من ذلك واكتب اختباراً يثبت أن 5xx متكرر يتوقف بعد `max_retries + 1` طلبات، ولا تغيّرها إن كانت سليمة.

---

## 3. الاختبارات الإلزامية

| # | الاختبار | يثبت |
|---|---|---|
| H1 | `test_oauth_state_tampered_rejected` (مُعدَّل) + تشغيله 50× | F1 |
| H2 | `test_get_me_returns_identity_and_sends_appsecret_proof` | F2/F5 |
| H3 | `test_complete_connection_uses_me_for_identity_and_name` | F3/F5 — `provider_user_id == me.id`, `provider_user_name == me.name` |
| H4 | `test_complete_connection_rejects_debug_is_valid_false` | F2 ⇒ `MetaTokenInvalidError` ⇒ 401 |
| H5 | `test_complete_connection_rejects_foreign_app_id` | F2 ⇒ `MetaTokenForeignAppError` ⇒ 401 |
| H6 | `test_complete_connection_rejects_user_id_mismatch` | F2 |
| H7 | `test_complete_connection_get_me_failure_is_fatal` | F3 — فشل `/me` ⇒ لا حفظ، الاستثناء يُرفع |
| H8 | `test_no_fallback_identity_symbol_exists` | `grep`-style: `not hasattr(service_module, "_fallback_identity")` |
| H9 | `test_page_without_token_stored_as_null` (تكامل) + `test_page_asset_public_has_page_token_flag` | F4 |
| H10 | `test_alembic_0002_upgrade_downgrade_roundtrip_with_null_rows` (تكامل) | F4 — downgrade لا يفشل مع صفوف NULL |
| H11 | `test_lifespan_disposes_engine_when_created` / `…_skips_when_never_created` | F6 |
| H12 | `test_health_ready_200_with_db` (تكامل) + `test_health_ready_503_without_db` (معزول بـ mock session يرمي) | F7 |
| H13 | `test_pagination_cap_raises_after_max` | F8 |

- كل اختبارات SPRINT-02 الـ 189 تبقى خضراء، **عدا** اختبارات `_fallback_identity` التي تُحذف مع الدالة (اذكرها بالاسم في §6).
- التغطية ≥ 95% كلياً.

---

## 4. معايير القبول

- [ ] `for i in $(seq 50); do pytest tests/core/test_oauth_state.py -q -p no:cacheprovider > /dev/null || { echo FAIL $i; break; }; done` ⇒ لا FAIL.
- [ ] `pytest -m "not integration"` ⇒ Zero Failures.
- [ ] `TEST_DATABASE_URL=… pytest -m integration` ⇒ Zero Failures (≥ 11 الآن).
- [ ] `grep -rn "_fallback_identity\|\"unknown\"\|\"pages:\"" src tests` ⇒ لا نتائج.
- [ ] `grep -n "is_valid" src/modules/social_connect/service.py` ⇒ نتيجة واحدة على الأقل.
- [ ] `grep -n "dispose" src/main.py` ⇒ موجود.
- [ ] `curl /health/ready` مع Postgres ⇒ 200؛ بدونها ⇒ 503.
- [ ] `alembic upgrade head` من قاعدة فارغة ينشئ 0001 ثم 0002؛ `downgrade base` ينجح.
- [ ] `ruff check` / `ruff format --check` نظيفان.
- [ ] فحص الأسرار نظيف؛ لا ملف محظور مُعدَّل.
- [ ] **كوميت واحد** بعنوان `fix(social-connect): SPRINT-02.1 hotfix — trusted identity via /me, debug_token validation, nullable page token, readiness, pagination cap, flaky test`.

---

## 5. أوامر التشغيل

```bash
# Postgres (بدون Docker — كما في بيئتك):
#   pg_ctl … start ثم CREATE DATABASE + CREATE EXTENSION vector
export TEST_DATABASE_URL=postgresql+asyncpg://<user>@localhost:<port>/<db>
pip install -e ".[dev]"
ruff check src tests alembic && ruff format --check src tests alembic
pytest -m "not integration" -q
pytest -m integration -q
pytest -q --cov=src --cov-report=term-missing
alembic upgrade head && alembic downgrade base && alembic upgrade head
```

---

## 6. التزامات المنفذ عند الإنهاء

1. ادفع الكوميت إلى `work/implementation` ⇒ **PR #3 يتحدث تلقائياً** (لا تفتح PR جديداً). أضف تعليقاً في PR #3 بملخص الإصلاحات.
2. املأ **§7** أدناه.
3. حدّث `WORK_STATE.md`: §1 الحالة ⇒ `SPRINT_02_1_DELIVERED_AWAITING_REVIEW`، §3، §5، وسطر في §6.
4. **لا تدمج.**

---

## 7. تقرير المنفذ (يملؤه المنفذ)

- **حالة التنفيذ:** `[ ] مكتمل`
- **Commit SHA:** `...`
- **تشغيل F1 50×:** `...`
- **`pytest -m "not integration"` / `-m integration` / الكل:** `...`
- **التغطية:** `...%`
- **ruff:** `...`
- **اختبارات حُذفت (بالاسم) وسببها:** `...`
- **إصدار PostgreSQL المستخدم:** `...`
- **انحرافات:** `لا يوجد` / `...`
- **أسئلة:** `...`

---

## 8. ما ينتظر قرار المالك (ليس للمنفذ)

مُفصَّل في `sprints/SPRINT-02_REVIEW.md` §6. ملخص:
1. **نموذج Tenant/Brand** — التوصية: جدولا `tenants` و `brands` وربط الحسابات الاجتماعية بـ `brand_id`.
2. تأكيد مرجع أرقام القرارات D-048/049/051.
3. حذف النسخة المكررة `AI_Marketing_Platform_Hub/imaster_planner.md`.
4. تسجيل Fernet كاستثناء مؤقت موثَّق عن AES-256-GCM.

**بعد رد المالك** يكتب المخطط SPRINT-03 (Tenant/Brand + CI + تنظيف).
