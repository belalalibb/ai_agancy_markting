# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-03 رُوجع واعتُمد ودُمج (PR #7 ⇒ `main` @ `c88a721`)؛ تقريره في [`sprints/SPRINT-03_REVIEW.md`](./sprints/SPRINT-03_REVIEW.md). هذا التوجيه يفتح **SPRINT-04**: إغلاق **الفجوة الأمنية الوحيدة المعروفة** في المنتج — هوية الفاعل المُدَّعاة ذاتياً (`?tenant_id=`) — بوحدة مصادقة حقيقية، دون أي ميزة منتج جديدة.

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-04-AUTH-AND-ACTOR-IDENTITY` |
| **النوع** | أمن تأسيسي — وحدة `auth` + استبدال سطح الهوية المؤقت (B4) |
| **الحالة** | `READY_FOR_EXECUTION` |
| **يبدأ من** | `main` @ `2851c9f` (PR #8 **مدموج بالفعل** — المخطط يدمج توجيهاته بنفسه). أول أمر: `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` |
| **الفرع المستهدف** | `work/implementation` ⇒ **PR جديد #9** إلى `main` |
| **الكوميتات** | **اثنان بالضبط**: Part A (وحدة auth) ثم Part B (استبدال سطح الهوية). كل كوميت يُدفع فوراً |
| **الحجم المتوقع** | Part A ~700 سطر؛ Part B ~400 سطر (معظمه حذف `tenant_id` من المعاملات وتحديث اختبارات) |
| **تاريخ الإصدار** | 2026-10-29 |

---

## 0. لماذا هذا السبرنت؟

1. **N1 من مراجعة SPRINT-03:** أي عميل يرسل `?tenant_id=<uuid>` يصبح ذلك المستأجر. الهيكل (Tenant → Brand → Grants) صحيح لكن **الفاعل غير مُتحقَّق منه**. لا يمكن فتح المنتج لأي مستخدم حقيقي قبل إغلاق هذا.
2. `SECURITY_BASELINE.md` §1-4 يحدد الاتجاه سلفاً: **JWT قصير الأجل (15 دقيقة) + Refresh Token في كوكي HttpOnly/Secure + تحقق صارم من الصلاحيات في كل طلب**. هذا التوجيه ينفّذه حرفياً.
3. التصميم في SPRINT-03 جعل الاستبدال رخيصاً عمداً: الهوية تُقرأ من **مكان واحد** (`tenancy/dependencies.py::ActorTenantId` + حقول `tenant_id` في 4 schemas). Part B يبدّل هذا المكان الواحد فقط.

---

## 1. النطاق

### داخل النطاق
- **Part A — وحدة `src/modules/auth/`:** جدول `users` + `refresh_sessions`، تسجيل/دخول بكلمة مرور (Argon2id)، JWT وصول قصير (HS256، 15 دقيقة)، Refresh دوّار في كوكي HttpOnly، خروج، تبعية `CurrentUser`.
- **Part B — استبدال سطح الهوية:** `ActorTenantId` يصبح مشتقاً من `CurrentUser`؛ حذف `tenant_id` من كل معاملات الاستعلام وأجسام الطلبات؛ `POST /tenants` يصبح جزءاً من التسجيل؛ تحديث كل الاختبارات؛ هجرة `0004`.
- تحديث `.env.template` و`docker-compose.yml` إن لزم (قيم فارغة فقط).

### خارج النطاق (ممنوع)
- OAuth اجتماعي لتسجيل الدخول (Google/Meta Login)، MFA، استعادة كلمة المرور بالبريد (لا بنية بريد بعد)، تحديد المعدل (Rate limiting) بخلاف ما في §2-A8، إدارة أعضاء متعددين داخل المستأجر الواحد (مستخدم واحد = مستأجر واحد في هذا السبرنت — انظر A2).
- أي تغيير في `social_connect` بخلاف استبدال الهوية (Part B).
- LiteLLM / Temporal / WhatsApp / واجهات أمامية.
- الملفات المحظورة: `ARCHITECTURE_LOCK.md`, `SESSION_RECOVERY.md`, `imaster_planner.md`, `README.md`, `AI_Marketing_Platform_Hub/**`, `sprints/**`. (يُسمح: `WORK_STATE.md` و§7 هنا.)

---

## 2. المواصفات الفنية

### Part A — وحدة `auth`

#### A1. التبعيات الجديدة (كلها مقبولة ببوابة D-054)
- `pyjwt>=2.9` (MIT) — JWT HS256.
- `argon2-cffi>=23.1` (MIT) — تجزئة كلمات المرور Argon2id.
- `pydantic[email]` (يجلب `email-validator`, Unlicense/CC0) — التحقق من البريد.
- **لا** `python-jose`, **لا** `passlib` (غير مصانة). بوابة التراخيص في CI يجب أن تبقى خضراء.

#### A2. نموذج الهوية في هذا السبرنت
- **مستخدم واحد ↔ مستأجر واحد** (`users.tenant_id` UNIQUE). التسجيل يُنشئ المستأجر والمستخدم معاً في معاملة واحدة. العضوية المتعددة تأتي في سبرنت لاحق عبر جدول `tenant_members` — **لا تُبنَ الآن**، لكن لا تكتب كوداً يفترض استحالتها (مثلاً: لا تجعل `User.tenant_id` هو المفتاح الأساسي).
- `BrandAccessResolver` و`brand_access_grants` **لا يتغيران**: الفاعل ما زال `tenant_id`، فقط أصبح مُستنتَجاً من مستخدم مُصادَق.

#### A3. الجداول (هجرة `0004_auth.py`)

**`users`**
| العمود | النوع | قيود |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID FK → `tenants.id` ON DELETE RESTRICT | NOT NULL، **UNIQUE** (A2) |
| `email` | CITEXT أو VARCHAR(320) مع فهرس فريد على `lower(email)` | NOT NULL؛ **اختر `VARCHAR(320)` + فهرس فريد `uq_users_email_lower ON users (lower(email))`** (لا تضف امتداد citext — أقل تبعية) |
| `password_hash` | TEXT | NOT NULL (Argon2id PHC string) |
| `is_active` | BOOLEAN | NOT NULL DEFAULT true |
| `created_at`, `updated_at`, `last_login_at` | TIMESTAMPTZ | الأخير NULL |

**`refresh_sessions`**
| العمود | النوع | قيود |
|---|---|---|
| `id` | UUID PK | = `sid` داخل الكوكي |
| `user_id` | UUID FK → `users.id` ON DELETE CASCADE | NOT NULL، index |
| `token_hash` | BYTEA(32) أو TEXT | **SHA-256 للسر العشوائي**؛ السر نفسه لا يُخزَّن أبداً |
| `expires_at` | TIMESTAMPTZ | NOT NULL، index |
| `revoked_at` | TIMESTAMPTZ | NULL = ساري |
| `replaced_by` | UUID NULL FK → `refresh_sessions.id` | سلسلة الدوران (كشف إعادة الاستخدام) |
| `user_agent` | VARCHAR(255) NULL، `ip` INET NULL | بيانات تدقيق فقط |
| `created_at` | TIMESTAMPTZ | |

`downgrade()`: إسقاط الجدولين. (لا بيانات قديمة تُهاجَر.)

#### A4. كلمات المرور
- `argon2.PasswordHasher()` بالإعدادات الافتراضية (RFC 9106 low-memory) عبر واجهة `PasswordHasher` Protocol في `auth/domain/` (قابلة للاستبدال).
- سياسة الحد الأدنى: ≥ 10 أحرف، ≤ 128، لا قائمة تعقيد سخيفة. رفض كلمات مرور تساوي البريد.
- `verify_password` يتبع **توقيتاً ثابتاً** عند عدم وجود المستخدم: احسب `hasher.verify` على hash وهمي ثابت (`_DUMMY_HASH`) كي لا يكشف زمن الاستجابة وجود البريد.
- عند نجاح الدخول وكان `hasher.check_needs_rehash(hash)` صحيحاً ⇒ أعد التجزئة واحفظها.

#### A5. رموز الوصول (Access JWT)
- HS256، سر من `AUTH_JWT_SECRET` (SecretStr، ≥ 32 بايت، **إلزامي** — غيابه يُفشل الإقلاع في غير `development`، وفي `development` يُولَّد عشوائياً مع تحذير واحد).
- Claims: `sub` = `user_id`، `tid` = `tenant_id`، `sid` = `refresh_sessions.id`، `iat`, `exp` (= iat + `AUTH_ACCESS_TTL_SECONDS`، افتراضي **900**)، `iss` = `settings.app_name`، `aud` = `"api"`, `jti` عشوائي.
- فك الرمز يتحقق من `exp`, `iss`, `aud`, الخوارزمية **المحددة صراحةً** (`algorithms=["HS256"]`) — لا `none`.
- **لا يُستشار قاعدة البيانات عند كل طلب** للوصول (هذا هدف JWT)؛ إبطال الجلسة يأخذ مفعوله خلال ≤ 15 دقيقة أو فوراً عبر Refresh. **استثناء:** مسارات الكتابة الحساسة (`DELETE /brands/{id}/grants/*`, `DELETE …/connections/*`, `PUT …/pages/selection`) تستخدم تبعية `CurrentUserFresh` التي تتحقق من `refresh_sessions.revoked_at IS NULL` لـ `sid` — طلب DB واحد.

#### A6. رموز التحديث (Refresh) — دوّارة، في كوكي
- السر: 32 بايت عشوائي (`secrets.token_urlsafe(32)`)؛ يُخزَّن `sha256(secret)` فقط.
- الكوكي: اسم `__Host-refresh` في الإنتاج (يفرض Secure + Path=/ + بلا Domain)، `refresh` في `development`؛ `HttpOnly`, `SameSite=Strict`, `Secure` (إلا development), `Path=/api/v1/auth`, `Max-Age` = `AUTH_REFRESH_TTL_SECONDS` (افتراضي **30 يوماً**).
- **الدوران:** كل `POST /auth/refresh` يُبطل الجلسة الحالية (`revoked_at`, `replaced_by`) ويُنشئ جديدة ويُعيد كوكي جديداً + access جديداً.
- **كشف إعادة الاستخدام:** إن قُدِّم refresh لجلسة `revoked_at IS NOT NULL` ⇒ **أبطل كل جلسات المستخدم** (سرقة محتملة) وأعد 401 `refresh_reuse_detected`.
- `POST /auth/logout`: يُبطل الجلسة الحالية ويمسح الكوكي. `POST /auth/logout-all`: يُبطل كل جلسات المستخدم.
- **CSRF:** لأن refresh في كوكي، يجب أن يحمل الطلب رأس `X-Requested-With: XMLHttpRequest` أو `Origin` ضمن `AUTH_ALLOWED_ORIGINS` (قائمة، فارغة = يُرفض أي `Origin` خارجي؛ غياب `Origin` + وجود الرأس المخصص = مقبول). `SameSite=Strict` طبقة ثانية لا بديل.

#### A7. المسارات (تحت `settings.api_v1_prefix`)

| الطريقة | المسار | الجسم | الاستجابة |
|---|---|---|---|
| POST | `/auth/register` | `{email, password, tenant_slug, tenant_name}` | 201 `{access_token, token_type:"bearer", expires_in, user:{id,email,tenant_id}}` + كوكي refresh؛ 409 `email_taken` / 409 `tenant_slug_taken`؛ 422 لسياسة كلمة المرور |
| POST | `/auth/login` | `{email, password}` | 200 نفس الشكل + كوكي؛ **401 `invalid_credentials` موحَّد** (لا تمييز بريد/كلمة)؛ 403 `user_inactive` |
| POST | `/auth/refresh` | — (كوكي) | 200 نفس الشكل + كوكي جديد؛ 401 `refresh_missing` / `refresh_invalid` / `refresh_expired` / `refresh_reuse_detected` |
| POST | `/auth/logout` | — (كوكي أو Bearer) | 204 + مسح الكوكي |
| POST | `/auth/logout-all` | Bearer | 204 |
| GET | `/auth/me` | Bearer | 200 `{id, email, tenant_id, tenant:{id,slug,name}}` |

- **`POST /tenants` يُحذف** (التسجيل ينشئ المستأجر). `GET /tenants/{tenant_id}` يبقى ويُحمى (المستأجر الخاص فقط ⇒ وإلا 404).
- لا حقل في أي استجابة يحوي `password` أو `hash` أو `secret`؛ حارس Zero-Secret يُوسَّع ليرفض أيضاً `password`, `hash`, `secret` في أسماء حقول **الاستجابات** (الطلبات تحوي `password` بالضرورة).

#### A8. تحديد المعدل الأدنى (داخل العملية، كافٍ لهذا السبرنت)
- `login` و`register` و`refresh`: نافذة منزلقة **10 طلبات/دقيقة لكل IP** + **5 فشل دخول متتالٍ لكل بريد ⇒ 429 لمدة 5 دقائق**. تنفيذ بسيط في الذاكرة خلف Protocol `RateLimiter` (يُستبدل بـ Redis لاحقاً). يُطفأ في الاختبارات بتبعية وهمية.
- رأس `Retry-After` في 429.

#### A9. تبعيات الهوية (`src/modules/auth/dependencies.py`)
```python
CurrentUser      # يفك Bearer JWT ⇒ AuthenticatedActor(user_id, tenant_id, sid) — بلا DB
CurrentUserFresh # CurrentUser + تحقق أن sid غير مُبطَل — طلب DB واحد
```
- غياب/تلف الرمز ⇒ **401** مع `WWW-Authenticate: Bearer`. الرمز صالح لكن الجلسة مُبطَلة (Fresh) ⇒ 401 `session_revoked`.
- الـ`AuthenticatedActor` نموذج Pydantic مجمَّد في `auth/domain/models.py`.

#### A10. الإعدادات الجديدة (`src/core/config.py`) — تُسجَّل في `.env.template` بقيم فارغة
`AUTH_JWT_SECRET`, `AUTH_ACCESS_TTL_SECONDS=900`, `AUTH_REFRESH_TTL_SECONDS=2592000`, `AUTH_COOKIE_SECURE` (افتراضي `true` إلا في development), `AUTH_ALLOWED_ORIGINS` (قائمة مفصولة بفواصل، افتراضي فارغ).

#### A11. الاستثناءات
`auth/exceptions.py`: `EmailTakenError`(409), `InvalidCredentialsError`(401), `UserInactiveError`(403), `RefreshMissingError`/`RefreshInvalidError`/`RefreshExpiredError`/`RefreshReuseDetectedError`(401), `SessionRevokedError`(401), `RateLimitedError`(429), `CsrfRejectedError`(403). خريطة موحدة بنفس أسلوب `to_http` في `tenancy`.

---

### Part B — استبدال سطح الهوية

#### B1. `tenancy/dependencies.py`
- `ActorTenantId` يصبح: `Annotated[UUID, Depends(_actor_tenant_id)]` حيث `_actor_tenant_id(actor: CurrentUser) -> UUID: return actor.tenant_id`. **الاسم يبقى** كي يكون التغيير في الموجِّهات صفرياً، **والتوثيق يُحدَّث** (لم يعد مؤقتاً).
- **اتجاه الاستيراد:** `tenancy` ⇒ `auth.dependencies` مسموح؛ `auth` **لا يستورد** `tenancy` إلا `tenancy.domain` (لإنشاء المستأجر عند التسجيل عبر `TenantRepository`).

#### B2. الـschemas
- حذف `tenant_id` من: `BrandCreate`, `GrantCreate`, `StartConnectionRequest`, `PageSelectionRequest`. (`grantee_tenant_id` يبقى — هو الهدف لا الفاعل.)
- حذف `?tenant_id=` من كل مسارات GET/DELETE. `grep -rn "alias=\"tenant_id\"" src` ⇒ فارغ.

#### B3. الحالة الحساسة — `/social/meta/callback`
- Meta تُعيد المتصفح إلى `callback` **بلا Bearer** (إعادة توجيه عليا). الهوية هنا تأتي حصراً من `state` الموقَّع (`t`, `b`) كما في SPRINT-03 — **هذا يبقى**. لكن يجب إثبات أن `state` أُصدر لمستخدم مُصادَق: `start` الآن يتطلب `CurrentUser` ويضع `t = actor.tenant_id` (لا من الجسم). النتيجة: لا يمكن تزوير `state` بلا سر التوقيع، ولا إصدارها بلا تسجيل دخول.
- أضف claim `u` = `user_id` إلى payload الـ`state` (للتدقيق فقط).

#### B4. `POST /tenants` يُحذف؛ `TenancyService.create_tenant` يبقى ويُستدعى من `AuthService.register` داخل نفس المعاملة (جلسة واحدة مشتركة عبر `get_db_session`).

#### B5. الاختبارات
- `tests/conftest.py`: مصنع `auth_headers(tenant_id)` يُصدر JWT حقيقياً بسر الاختبار (لا تبعية وهمية للهوية — نختبر المسار الحقيقي). عالم Tenancy الثابت (`OWNER_ID/AGENCY_ID/STRANGER_ID`) يكتسب مستخدماً لكل مستأجر.
- كل اختبار كان يمرر `?tenant_id=` أو `"tenant_id":` في الجسم يُحوَّل إلى رأس `Authorization`.
- اختبار جديد لكل مسار محمي: **بلا رأس ⇒ 401** (مولَّد بارامترياً من `app.routes` كي لا يُنسى مسار).

---

## 3. الاختبارات الإلزامية

### Part A — وحدة (fakes/in-memory repos، بلا I/O)
| # | الاختبار |
|---|---|
| A-U1 | التسجيل ينشئ مستأجراً ومستخدماً؛ بريد مكرر (بحالة أحرف مختلفة `A@x.com` vs `a@x.com`) ⇒ 409؛ slug مكرر ⇒ 409 **ولا يُنشأ مستخدم يتيم** |
| A-U2 | سياسة كلمة المرور: < 10 ⇒ 422؛ = البريد ⇒ 422 |
| A-U3 | الدخول: صحيح ⇒ 200 + كوكي بـ`HttpOnly; SameSite=Strict; Path=/api/v1/auth`؛ بريد غير موجود وكلمة خاطئة ⇒ **نفس** 401 `invalid_credentials`؛ مستخدم غير نشط ⇒ 403 |
| A-U4 | التوقيت الثابت: `verify` يُستدعى مرة واحدة في الحالتين (بريد موجود/غير موجود) — يُفحص عبر hasher وهمي يعدّ الاستدعاءات |
| A-U5 | JWT: `exp` = iat+900؛ `aud`/`iss` خاطئان ⇒ 401؛ `alg=none` ⇒ 401؛ رمز منتهٍ ⇒ 401؛ توقيع بسر آخر ⇒ 401 |
| A-U6 | Refresh: الدوران يُبطل القديم ويُنشئ جديداً؛ القديم بعد الدوران ⇒ 401 `refresh_reuse_detected` **وكل جلسات المستخدم مُبطَلة**؛ منتهٍ ⇒ `refresh_expired`؛ بلا كوكي ⇒ `refresh_missing` |
| A-U7 | CSRF: refresh بلا `X-Requested-With` وبـ`Origin` خارجي ⇒ 403؛ مع `Origin` ضمن القائمة ⇒ 200 |
| A-U8 | logout يمسح الكوكي (`Max-Age=0`) ويُبطل الجلسة؛ logout-all يُبطل جميعها؛ `CurrentUserFresh` بعد logout ⇒ 401 `session_revoked`؛ `CurrentUser` (بلا DB) ما زال يقبل الرمز حتى انتهائه — **سلوك موثَّق ومُختبَر** |
| A-U9 | Rate limit: الطلب 11 خلال دقيقة ⇒ 429 + `Retry-After`؛ 5 فشل لبريد ⇒ 429 لذلك البريد فقط |
| A-U10 | `/auth/me` يعيد المستأجر؛ حارس Zero-Secret الموسّع: لا `password`/`hash`/`secret` في أي schema استجابة ولا في `/openapi.json` responses |
| A-U11 | `check_needs_rehash` ⇒ إعادة التجزئة تُحفظ (hasher وهمي يعلن الحاجة) |
| A-U12 | الإعدادات: غياب `AUTH_JWT_SECRET` في `production` ⇒ `ValidationError` عند الإقلاع؛ في `development` ⇒ تحذير واحد وسر عشوائي |

### Part B — وحدة
| # | الاختبار |
|---|---|
| B-U1 | **بارامتري على كل مسار** في `app.routes` تحت `/api/v1` عدا `/auth/register|login|refresh` و`/social/meta/callback` و`/health*`: بلا Bearer ⇒ 401 مع `WWW-Authenticate` |
| B-U2 | المالك (JWT) ينشئ علامة ⇒ `owner_tenant_id == actor.tenant_id`؛ محاولة تمرير `tenant_id` في الجسم ⇒ 422 (`extra="forbid"`) |
| B-U3 | الوكالة (JWT) تبدأ اتصالاً لعلامة ممنوحة ⇒ `state.t == AGENCY_ID`؛ الغريب ⇒ 404 |
| B-U4 | `state` الصادر يحمل `u` = user_id؛ callback بـ`state` صالح يعمل بلا Bearer (مسار إعادة توجيه Meta) |
| B-U5 | `?tenant_id=` مُرسَل مع Bearer ⇒ **يُتجاهَل** (الهوية من الرمز)؛ `grep alias="tenant_id"` فارغ |
| B-U6 | `POST /tenants` ⇒ 404/405 (محذوف) |

### تكامل (PostgreSQL حقيقية)
| # | الاختبار |
|---|---|
| I-A1 | هجرة `0004` صعود/نزول/صعود؛ `uq_users_email_lower` يرفض `A@x.com` بعد `a@x.com` |
| I-A2 | التدفق الكامل: register ⇒ me ⇒ refresh (دوران) ⇒ إعادة استخدام القديم ⇒ 401 + كل الجلسات مُبطَلة في DB ⇒ login جديد ⇒ logout-all ⇒ `refresh_sessions` كلها `revoked_at IS NOT NULL` |
| I-A3 | التسجيل بـslug مكرر ⇒ 409 و`SELECT count(*) FROM users` لم يتغير (ذرية المعاملة) |
| I-A4 | `password_hash` يبدأ بـ`$argon2id$`؛ `token_hash` ≠ قيمة الكوكي؛ لا عمود يحوي السر الخام |
| I-B1 | تدفق SPRINT-03 الكامل (I-B8 السابق) يعمل **بـ JWT** بدل `tenant_id`: register مالك + register وكالة ⇒ brand ⇒ grant ⇒ start (الوكالة) ⇒ callback (respx) ⇒ selection ⇒ revoke ⇒ اتصالات الوكالة 0 |
| I-B2 | `CurrentUserFresh`: بعد `logout` ⇒ `DELETE …/connections/{id}` ⇒ 401؛ `GET …/connections` بنفس الرمز ⇒ 200 حتى الانتهاء (موثَّق) |

---

## 4. معايير القبول

- [ ] CI خضراء على PR #9 (الفحوص الخمسة؛ `licenses` يقبل `pyjwt`, `argon2-cffi`, `email-validator`).
- [ ] 0 فشل (وحدة + تكامل)؛ تغطية ≥ 95%؛ ruff نظيف.
- [ ] `grep -rn 'alias="tenant_id"' src` ⇒ فارغ؛ `grep -rn "TEMPORARY" src` ⇒ فارغ.
- [ ] B-U1 البارامتري يغطي **كل** مسار محمي (عدد الحالات = عدد المسارات المحمية، يُطبع في §7).
- [ ] `/openapi.json`: مخطط أمان `bearerAuth` مُعلَن ومطبَّق على كل المسارات المحمية؛ لا حقل `password|hash|secret|token` في الاستجابات إلا `access_token` و`has_page_token`.
- [ ] لا `python-jose`/`passlib` في `pyproject.toml`.
- [ ] `alembic upgrade head` ⇒ `0004`؛ `downgrade 0003` ⇒ `upgrade head`.
- [ ] كوميتان على `work/implementation` فوق `main` (+ كوميت توثيق اختياري)؛ لا ملف محظور مُسّ؛ gitleaks نظيف.
- [ ] §7 مملوء و`WORK_STATE.md` §1/§3/§4/§5/§6 محدَّثة.

---

## 5. أوامر التشغيل

```bash
git checkout work/implementation && git fetch origin && git merge --ff-only origin/main
pip install -e ".[dev]"
export TEST_DATABASE_URL=postgresql+asyncpg://aigos@localhost:5433/ai_growth_os_test
export AUTH_JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")

ruff check src tests alembic scripts && ruff format --check src tests alembic scripts
pytest -m "not integration" --cov=src --cov-fail-under=95
pytest -m integration
python -m venv /tmp/lic && /tmp/lic/bin/pip install -q . && /tmp/lic/bin/python scripts/check_licenses.py

grep -rn 'alias="tenant_id"' src; grep -rn "TEMPORARY" src
grep -n "python-jose\|passlib" pyproject.toml
git diff --name-only origin/main...HEAD | grep -E "^(ARCHITECTURE_LOCK|SESSION_RECOVERY|imaster_planner|README)\.md$|^AI_Marketing_Platform_Hub/|^sprints/" && echo "FORBIDDEN TOUCHED" || echo "forbidden OK"
```

---

## 6. التزامات المنفذ عند الإنهاء

1. **كوميت Part A**: `feat(auth): SPRINT-04 Part A — users/refresh_sessions, Argon2id, HS256 access JWT (15m), rotating HttpOnly refresh cookie with reuse detection, rate limiting` ⇒ ادفع فوراً ⇒ افتح **PR #9** بعنوان `SPRINT-04: Auth module + verified actor identity` ⇒ تأكد أن CI تعمل.
2. **كوميت Part B**: `refactor(identity): SPRINT-04 Part B — ActorTenantId from JWT, drop tenant_id params/bodies, register creates tenant, auth-required guard for all protected routes` ⇒ ادفع فوراً.
3. املأ **§7**؛ حدّث `WORK_STATE.md` (§1 ⇒ `SPRINT_04_DELIVERED_AWAITING_REVIEW`). كوميت توثيق ثالث مسموح.
4. **لا تدمج PR #9 بنفسك.** المخطط يراجع ثم **يدمجه هو** عند الاعتماد (المالك لا يُطلب منه أي دمج يدوي — قاعدة دائمة من هذا السبرنت). عند التسليم اكتب في §7 وفي ردك على المالك: *«PR #9 جاهز لمراجعة المخطط»* — لا «بانتظار دمج المالك».
5. أسئلة تعيق التنفيذ ⇒ §7 «أسئلة»، ونفّذ ما لا يعتمد عليها. **لا تخمّن في قرارات الأمن** (مدد الصلاحية، أسماء الكوكي، سياسة CSRF) — هي محددة أعلاه.

---

## 7. تقرير المنفذ (يملؤه المنفذ)

- **حالة التنفيذ:** `[ ] مكتمل` / `[ ] جزئي — السبب:`
- **Commit SHA Part A:** `…` — **Part B:** `…` — **docs:** `…`
- **PR #9:** رابط — **رابط تشغيل CI الأخضر:** …
- **`pytest -m "not integration"` / `-m integration` / الكل:** … / … / …
- **التغطية:** …%
- **ruff:** …
- **`check_licenses.py`:** exit … / الحزم الجديدة وتراخيصها: …
- **B-U1 البارامتري:** عدد المسارات المحمية المغطاة = …
- **I-A2 (الدوران + كشف إعادة الاستخدام) على PG:** …
- **اختبارات حُذفت/أُعيدت تسميتها (بالاسم والسبب):** …
- **انحرافات عن التوجيه (بالسطر والسبب):** …
- **أسئلة:** …

---

## 8. ما ينتظر المالك (ليس للمنفذ)

> **قاعدة الدمج الدائمة (قرار المالك 2026-10-30):** المالك **لا يدمج يدوياً**. المخطط يدمج PRs توجيهاته فور فتحها، ويدمج PRs المنفذ بعد مراجعتها واعتمادها. دور المالك: القرارات، التوكن، وإطلاق المنفذ.

1. 🔑 **تدوير توكن GitHub** المستخدم في الجلسات (ظهر في الدردشة مراراً) وإرسال الجديد مع «تابع» القادمة.
2. 🛡️ **حماية الفرع `main`**: الفحوص الخمسة Required + Require PR + up-to-date (الفحوص ظاهرة الآن بعد PR #7).
3. **قرار مؤجل (لا يحجب 04):** العضوية المتعددة داخل المستأجر (`tenant_members` بأدوار) — التوصية: SPRINT-05 أو 06 حسب أولوية المحتوى.
4. **قرار مؤجل:** تفويض `MANAGER ⇒ VIEWER` — مرفوض حالياً (مراجعة 03 §4-س2)؛ يُعاد فتحه مع `tenant_members`.
5. بعد اعتماد SPRINT-04 ينقل المخطط إلى Hub: `DECISION_LOG` (D-056/057/058 + Tenancy + Auth)، `SECURITY_BASELINE` (Fernet exception، JWT/Refresh كما نُفّذ، certifi MPL-2.0)، `DATA_AND_INTEGRATIONS` (المخطط الحالي)، `DEFERRED_DECISIONS` (البندان 3 و4).
