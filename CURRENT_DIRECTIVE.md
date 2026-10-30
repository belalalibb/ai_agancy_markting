# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-04 رُوجع و**قُبل مشروطاً** ودُمج (PR #11 ⇒ `main` @ `6f263ac`)؛ تقريره في [`sprints/SPRINT-04_REVIEW.md`](./sprints/SPRINT-04_REVIEW.md). هذا التوجيه **دفعة تصحيح إلزامية**: 8 ثغرات ثبتت بالملف والسطر (اثنتان P0 موروثتان من SPRINT-03). **لا ميزة جديدة، ولا بناء فوق الكود الحالي قبل إغلاقها.**

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-04.1-HOTFIX` |
| **النوع** | تصحيح أمني — **لا ميزات** |
| **الحالة** | `READY_FOR_EXECUTION` |
| **يبدأ من** | `main` @ `6f263ac` (مدموج). أول أمر: `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` |
| **الفرع المستهدف** | `work/implementation` ⇒ **PR #12** إلى `main` |
| **الكوميتات** | **اثنان**: Part A = H1–H4 (P0 + الفخاخ الموروثة من 03)، Part B = H5–H8 (عيوب 04). كل كوميت يُدفع فوراً |
| **الحجم المتوقع** | ~500 سطر كود + ~400 سطر اختبارات |
| **تاريخ الإصدار** | 2026-10-30 |

---

## 0. لماذا؟

مراقب خارجي قدّم 8 ملاحظات؛ **المخطط تحقق من كلٍّ منها بالسطر وأثبت S03-2 تجريبياً** (سجل `httpx` على INFO طبع `client_secret=…` و`access_token=…` حرفياً). اثنتان منها P0 تخصّان عزل بيانات العملاء وتسريب الأسرار، وموجودتان على `main` منذ SPRINT-03 — **ثغرة في توجيه المخطط لا في تنفيذ المنفذ.** الباقية عيوب محصورة في كود SPRINT-04. التفاصيل الكاملة في `sprints/SPRINT-04_REVIEW.md` §2.

| # | الملف:السطر (@ `6f263ac`) | العيب | الخطورة |
|---|---|---|---|
| H1 | `social_connect/schemas.py:236`, `router.py:234-254, 265-275`, `service.py:178-215` | صفحات عملاء الوكالة الآخرين تظهر لكل مخوّل على العلامة؛ `select_pages` لا يتحقق أن الفاعل هو صاحب الاتصال ⇒ اختطاف توكن صفحة عميل آخر | 🔴 P0 |
| H2 | `meta_client.py:130,151,168,189,204` + `:260-262` | `client_secret`/`access_token` في query params ⇒ تُسجَّل في `httpx` INFO؛ `str(exc)` يحمل الرابط بالأسرار إلى 502 | 🔴 P0 |
| H3 | `service.py:194-198`, `:270` | `set_status(REAUTH_REQUIRED)` يضيع بـ`rollback`؛ صفحة بلا توكن تُقبل بـ200 بدل 422 | 🟠 |
| H4 | `meta_client.py:226,243-254`؛ `tenancy`/`auth` postgres repos | `paging.next` بلا فحص https/مضيف؛ `IntegrityError` ⇒ 500 | 🟠/🟡 |
| H5 | `config.py:150-152`, `auth/router.py:116-129` | `__Host-refresh` مع `Path=/api/v1/auth` — المتصفحات تُسقطه بصمت (RFC 6265bis §4.1.3.2) | 🔴 يكسر الدخول في الإنتاج |
| H6 | `auth/postgres_repository.py:79-85`, `dependencies.py:135-142` | `CurrentUserFresh` لا يفحص `expires_at` ولا `users.is_active` | 🟠 |
| H7 | `auth/service.py:142-144`, `postgres_repository.py:87-94` | الإصدار قبل الإبطال؛ `revoke` بلا `rowcount` ⇒ تزاحم يُصدر جلستين | 🟠 |
| H8 | `auth/infrastructure/rate_limit.py:19-21` | القواميس بلا سقف ولا تنظيف | 🟡 |

---

## 1. النطاق

**داخل:** H1–H8 فقط + اختباراتها + ردود الأسئلة المعتمدة (§3 من المراجعة): `GET /auth/me` ⇒ `CurrentUserFresh`؛ نموذج `start` الفارغ يبقى.
**خارج (ممنوع):** أي ميزة، أي تغيير مخطط بيانات بخلاف ما يلزم H1 (لا يلزم شيء)، تغيير التشفير، Redis، الملفات المحظورة (`ARCHITECTURE_LOCK.md`, `SESSION_RECOVERY.md`, `imaster_planner.md`, `README.md`, `AI_Marketing_Platform_Hub/**`, `sprints/**`).

---

## 2. المواصفات

### Part A — الموروث من SPRINT-03

#### H1. عزل صفحات الوكالة وملكية الاتصال
**المبدأ:** الصفحات **غير المختارة** هي بيانات شخصية لحساب Meta الخاص بمن أجرى OAuth (`connected_by_tenant_id`)، لا بيانات العلامة. لا يراها ولا يختار منها إلا **صاحب الاتصال**. بقية المخوّلين على العلامة يرون المختارة فقط.

1. **الرؤية** — في `ConnectedAccountPublic.from_domain(account, *, viewer_tenant_id: UUID)`:
   - `pages = [p for p in account.pages if p.is_selected or account.connected_by_tenant_id == viewer_tenant_id]`.
   - أضف حقل `is_connection_owner: bool` (= المساواة أعلاه) ليعرف العميل إن كان يستطيع تعديل الاختيار.
   - `router.py` يمرر `actor.tenant_id` في `list`/`get`/`callback`/`select_pages`.
2. **التعديل** — `select_pages` و`disconnect`:
   - في `MetaConnectService.select_pages(brand_id, account_id, page_ids, *, actor_tenant_id)`: بعد `get`، إن `account.connected_by_tenant_id != actor_tenant_id` ⇒ ارمِ `NotConnectionOwnerError` ⇒ **403 `not_connection_owner`** (الاتصال موجود ومرئي، لذا 403 لا 404).
   - `disconnect`: **يُسمح** لـ OWNER بفصل أي اتصال على علامته (حقه في طرد الوكالة) **و** لصاحب الاتصال؛ MANAGER غير المالك يفصل اتصالاته فقط. طبّقها في الخدمة بنفس النمط.
3. **Callback بإعادة ربط**: `find_by_identity` يعيد اتصالاً قائماً لنفس `(brand, provider, provider_user_id)`؛ إن كان `connected_by_tenant_id` مختلفاً عن الفاعل الحالي ⇒ **حدّثه** إلى الفاعل الحالي (من أعاد OAuth هو المالك الجديد للتوكن) وسجّل `logger.info` بالمعرّفات فقط.
4. **اختبارات (وحدة):** H1-U1 مالك العلامة يرى صفحات اتصال الوكالة **المختارة فقط** و`is_connection_owner=false`؛ H1-U2 الوكالة ترى الكل على اتصالها؛ H1-U3 مالك العلامة ⇒ `PUT …/pages/selection` على اتصال الوكالة ⇒ **403** ولا تغيير ولا نداء Graph (`pages_route.call_count == 0`)؛ H1-U4 الوكالة على اتصال المالك ⇒ 403؛ H1-U5 OWNER يفصل اتصال الوكالة ⇒ 204؛ MANAGER يفصل اتصال المالك ⇒ 403؛ H1-U6 إعادة ربط بفاعل مختلف تنقل `connected_by_tenant_id`. **(تكامل)** H1-I1 السيناريو الكامل على PG: وكالة بـ3 صفحات لعملاء مختلفين تربط بعلامة (أ) ⇒ مالك (أ) يرى 0 صفحات قبل الاختيار ⇒ الوكالة تختار 1 ⇒ المالك يرى 1 فقط ⇒ المالك يحاول اختيار صفحة أخرى ⇒ 403 ⇒ `SELECT count(*) … encrypted_page_token IS NOT NULL` = 1.

#### H2. أسرار Graph خارج الرابط
1. **كل** استدعاء في `meta_client.py` ينقل `access_token`/`input_token`/`client_secret`/`appsecret_proof` من `params` إلى:
   - `GET` ⇒ رأس `Authorization: Bearer <token>` + `appsecret_proof` في **الرأس غير ممكن** في Graph ⇒ حوّل تلك الطلبات إلى **`POST` مع `method=GET` override** غير مطلوب؛ **الحل المعتمد:** Graph API يقبل `access_token` و`appsecret_proof` في **جسم `application/x-www-form-urlencoded` لطلبات POST**، ويقبل `Authorization: Bearer` للـGET. اعتمد:
     - `GET /me`, `GET /me/accounts`, `GET /debug_token`: `headers={"Authorization": f"Bearer {token}"}` + `params={"appsecret_proof": proof, "fields": …}` (الـproof ليس سراً قابلاً لإعادة الاستخدام بلا التوكن؛ مقبول في الرابط).
     - `GET /oauth/access_token` (تبادل الكود/الـlong-lived): **حوّله إلى `POST`** بجسم form (`client_id`, `client_secret`, `grant_type`, `code`/`fb_exchange_token`, `redirect_uri`) — Graph يدعم POST لهذا المسار.
   - `debug_token` يحتاج `input_token` (سر): مرّره في الرأس؟ غير مدعوم. ⇒ **POST** بجسم form `input_token=…` مع `Authorization: Bearer <app_token>`.
2. **كبح `httpx`**: في `src/core/logging.py` (جديد) دالة `configure_logging()` تُستدعى من `create_app()` تضبط `logging.getLogger("httpx").setLevel(logging.WARNING)` و`httpcore` كذلك، **وتُركّب `logging.Filter` على الجذر** يُنقّح أي `record.getMessage()` بأنماط: `(access_token|client_secret|input_token|fb_exchange_token|code)=[^&\s]+` ⇒ `\1=***`، و`Bearer\s+[A-Za-z0-9._~+/=-]+` ⇒ `Bearer ***`، و`EAA[A-Za-z0-9]{20,}` ⇒ `EAA***`. (دفاع في العمق؛ الكبح الأول هو عدم وضعها في الرابط أصلاً.)
3. **رسائل الأخطاء:** `meta_client.py:260-262`: `MetaNetworkError("Meta Graph API timeout")` / `("Meta Graph API network error")` **بلا `{exc}`**؛ السبب يُسجَّل بـ`logger.warning("…: %s", type(exc).__name__)` فقط. تحقق أن `_to_http` لا يُمرر `str(exc)` لأي استثناء شبكي إلى الجسم.
4. **اختبارات:** H2-U1 `respx` يثبت أن **لا طلب** يحمل `access_token`/`client_secret`/`input_token` في `request.url.query` (تحقق على كل مسارات `MetaGraphClient`)؛ H2-U2 `test_identity_and_secrets_never_logged` يُوسَّع ليراقب **الجذر** (`logger=None`) على DEBUG مع `httpx` على INFO صراحةً، ويفشل إن ظهر أي سر؛ H2-U3 `ConnectError("…?access_token=LEAK")` ⇒ جسم 502 لا يحوي `LEAK`؛ H2-U4 مرشّح التنقيح يحوّل الأنماط الأربعة إلى `***`.

#### H3. فخ rollback + صفحة بلا توكن
1. `select_pages` عند `MetaTokenExpiredError`: بدل `set_status` داخل نفس الجلسة ثم `raise`، استخدم **جلسة مستقلة**: `async with get_session_factory()() as s, s.begin(): await PostgresRepo(s).set_status(...)` عبر دالة `mark_reauth_required_out_of_band(brand_id, account_id)` تُحقن في الخدمة (Protocol `StatusMarker`؛ في الاختبارات fake). ثم `raise`. طبّق نفس النمط على **كل** `set_status(REAUTH_REQUIRED)` قبل `raise` في `social_connect` (ابحث: `grep -n "REAUTH_REQUIRED" src`).
2. `select_pages`: أي `page_id` مطلوب وصفحته في `fresh` **بلا `access_token`** ⇒ `PageNotSelectableError(page_ids)` ⇒ **422 `page_not_selectable`** ولا تغيير.
3. **اختبارات:** H3-I1 (PG): توكن منتهٍ أثناء selection ⇒ 401 **و** `status = 'reauth_required'` محفوظ فعلاً بعد انتهاء الطلب؛ H3-U1 صفحة بلا توكن ⇒ 422 ولا تغيير.

#### H4. SSRF + IntegrityError
1. `meta_client.py` قبل `_request_absolute(next_url)`: `urlparse(next_url)`: `scheme == "https"` **و** `netloc == urlparse(settings.meta_graph_base_url).netloc` وإلا `MetaGraphError("untrusted pagination url")` (لا طلب). اختبار H4-U1 بـ`paging.next = "http://evil/…"` و`https://evil/…` ⇒ لا طلب (`respx` route على evil غير مستدعى).
2. مستودعات `tenancy` و`auth` (PostgreSQL): لفّ `flush()` في `create_*` بـ`try/except IntegrityError` ⇒ `await session.rollback()`؟ **لا** — `rollback` يقتل المعاملة. **الحل:** استخدم `INSERT … ON CONFLICT DO NOTHING RETURNING id`؛ إن لم يُرجع صفاً ⇒ ارمِ الاستثناء المجالي (`TenantSlugTakenError`/`BrandSlugTakenError`/`EmailTakenError`/`GrantAlreadyActiveError`). اختبار H4-I1 (PG): إدراجان متزامنان بنفس slug عبر `asyncio.gather` بجلستين ⇒ واحد 201 وواحد 409، لا 500.

### Part B — عيوب SPRINT-04

#### H5. اسم الكوكي
- **القرار:** `__Secure-refresh` مع إبقاء `Path=/api/v1/auth` (حصر الإرسال بمسارات المصادقة أهم من حماية `__Host-` ضد الـsubdomains، ونحن نتحكم بالمضيف). في `development` يبقى `refresh`.
- `config.py:150-152`: `"__Secure-refresh" if self.auth_cookie_secure else "refresh"`. حدّث docstring `router.py:12-13`.
- **اختبار H5-U1:** يفحص رأس `Set-Cookie` الخام: إن بدأ الاسم بـ`__Host-` ⇒ يجب `Path=/` وبلا `Domain`؛ إن بدأ بـ`__Secure-` ⇒ يجب `Secure`. (حارس دائم ضد تكرار الخطأ.) H5-U2 في وضع secure الاسم `__Secure-refresh` و`Path=/api/v1/auth`.

#### H6. `CurrentUserFresh` كامل
- `RefreshSessionRepository.is_active(session_id, now)` ⇒ استعلام واحد بـ`JOIN users`: `revoked_at IS NULL AND expires_at > :now AND users.is_active`.
- `GET /auth/me` ⇒ `CurrentUserFresh` (قرار المراجعة §3-2).
- **اختبارات:** H6-U1 جلسة منتهية ⇒ 401 `session_revoked`؛ H6-U2 مستخدم `is_active=false` ⇒ 401 على `DELETE …/connections/{id}` و`GET /auth/me`؛ H6-I1 (PG) تعطيل المستخدم بـ`UPDATE users SET is_active=false` ⇒ الطلب التالي بـ`CurrentUserFresh` 401.

#### H7. دوران ذرّي
- `postgres_repository.revoke(...)` تُرجع `int(rowcount)`.
- `AuthService.refresh`: **أبطل أولاً**: `revoked = await self._sessions.revoke(session.id, now)`؛ إن `revoked != 1` ⇒ عامل كإعادة استخدام (`revoke_all_for_user` + `RefreshReuseDetectedError`). ثم `_issue` ثم `update replaced_by` للجلسة القديمة (أو مرّر `replaced_by` لاحقاً بـ`UPDATE` ثانٍ).
- **اختبارات:** H7-U1 fake repo يُرجع 0 من `revoke` ⇒ `RefreshReuseDetectedError` وكل الجلسات مُبطَلة؛ H7-I1 (PG) `asyncio.gather` لطلبي refresh بنفس الكوكي بجلستين ⇒ **بالضبط** واحد 200 وواحد 401، و`SELECT count(*) WHERE revoked_at IS NULL AND user_id=…` = 1.

#### H8. سقف الذاكرة
- `InMemoryRateLimiter(max_keys=10_000)`: كل `hit`/`record_failure` ينفّذ تنظيفاً كسولاً كل N=256 استدعاء (يحذف المفاتيح التي نافذتها فارغة و`blocked_until` منتهٍ)؛ عند بلوغ `max_keys` يحذف **الأقدم** (استخدم `OrderedDict` أو حقل last-seen). `_failures` تُحذف مع النافذة.
- **اختبار H8-U1:** 20,000 مفتاح عشوائي ⇒ `len(_hits) <= 10_000`؛ H8-U2 مفتاح انتهت نافذته يُحذف بعد التنظيف.

---

## 3. معايير القبول

- [ ] CI خضراء على PR #12 (الفحوص الخمسة).
- [ ] 0 فشل؛ تغطية ≥ 95%؛ ruff نظيف.
- [ ] `grep -rn '"access_token"\|"client_secret"\|"input_token"' src/modules/social_connect/meta_client.py` يُظهرها **فقط** داخل `headers=`/`data=` لا `params=` (اذكر الأسطر في §7).
- [ ] `grep -n "__Host-" src` ⇒ فارغ؛ H5-U1 موجود.
- [ ] H2-U2 يراقب الجذر ويمر؛ تجربة المخطط (`/tmp/httpx_leak2.py` في المراجعة) تُعاد وتُظهر `client_secret in log: False`.
- [ ] H1-I1, H3-I1, H4-I1, H6-I1, H7-I1 موجودة بالاسم وناجحة على PG.
- [ ] `grep -rn "f\".*{exc}" src/modules/social_connect/meta_client.py` ⇒ فارغ.
- [ ] كوميتان فوق `main`؛ لا ملف محظور مُسّ.

---

## 4. أوامر

```bash
git checkout work/implementation && git fetch origin && git merge --ff-only origin/main
pip install -e ".[dev]"
export TEST_DATABASE_URL=postgresql+asyncpg://aigos@localhost:5433/ai_growth_os_test
export AUTH_JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
ruff check src tests alembic scripts && ruff format --check src tests alembic scripts
pytest --cov=src --cov-fail-under=95
grep -n "__Host-" -r src; grep -n 'f".*{exc}' src/modules/social_connect/meta_client.py
```

---

## 5. التزامات المنفذ

1. **Part A** كوميت: `fix(security): SPRINT-04.1 Part A — agency page isolation & connection ownership (H1), Graph secrets out of URLs + log redaction (H2), out-of-band reauth status & non-selectable pages (H3), pagination host check & ON CONFLICT inserts (H4)` ⇒ ادفع ⇒ افتح **PR #12** `SPRINT-04.1: security hotfix (8 verified findings)`.
2. **Part B** كوميت: `fix(auth): SPRINT-04.1 Part B — __Secure- refresh cookie (H5), full CurrentUserFresh check (H6), atomic refresh rotation (H7), bounded rate limiter (H8)` ⇒ ادفع.
3. §7 + `WORK_STATE.md` (§1 ⇒ `SPRINT_04_1_DELIVERED_AWAITING_REVIEW`). عند التسليم: *«PR #12 جاهز لمراجعة المخطط»*. **لا تدمج.**
4. أي بند لا يمكن تنفيذه كما حُدد (خاصة H2: إن رفض Graph `Authorization: Bearer` لمسار ما) ⇒ **لا تُعِد السر إلى الرابط**؛ استخدم POST بجسم form لذلك المسار ووثّق في §7.

---

## 6. تقرير المنفذ (يملؤه المنفذ)

- **حالة التنفيذ:** `[ ] مكتمل` / `[ ] جزئي — السبب:`
- **Commit SHA Part A:** `…` — **Part B:** `…` — **docs:** `…`
- **PR #12:** … — **CI:** …
- **الاختبارات:** وحدة … / تكامل … / الكل … — **التغطية:** …%
- **H2: أسطر `headers=`/`data=` التي تحمل الأسرار، وأي مسار Graph احتاج POST بدل Bearer:** …
- **H7-I1: نتيجة السباق على PG:** …
- **انحرافات (بالسطر والسبب):** …
- **أسئلة:** …

---

## 7. ما ينتظر المالك

1. 🔑 **تدوير توكن GitHub — الطلب الثالث.** إلزامي قبل SPRINT-05.
2. 🛡️ حماية `main` بالفحوص الخمسة.
3. بعد اعتماد 04.1: المخطط يحدّث `SECURITY_BASELINE` §2 و`KNOWN_ISSUES` (درس: «رؤية الصفحات غير المختارة» و«الأسرار في query params» كانا ثغرتي توجيه).
