# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** PR #55 (SPRINT-13 + 13.1-FIX) **دُمج في `main @ f566b09`** بعد تحقق حي مستقل على PostgreSQL 17.11 ([`sprints/SPRINT-13_REVIEW.md` §8](./sprints/SPRINT-13_REVIEW.md)). المهمة الآن: **SPRINT-14-A — الانضمام الذاتي بالعربية، الجزء الأول (Zero-API Self-Serve Onboarding Part A)**.

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | **`SPRINT-14-A`** (التسجيل · أول علامة + قطاع · إكمال ربط Meta · WhatsApp Cloud API · شريط التقدّم) |
| **الحالة** | `ISSUED` — المنفذ يبدأ فور دمج هذا التوجيه في `main` (D-063) |
| **يبدأ من** | `main` @ **SHA دمج هذا التوجيه** (مُسجَّل في `WORK_STATE.md` §6): `git checkout -B work/implementation origin/main` |
| **الفرع المستهدف** | PR جديد `work/implementation → main` بعنوان `SPRINT-14-A: self-serve onboarding part A` — كوميت واحد `feat(sprint-14a): …` + كوميت docs §6 |
| **الهجرة** | **لا هجرة.** لا تغيير في `alembic/`، لا عمود جديد، لا جدول جديد |
| **التبعيات** | **لا تبعية جديدة** (D-054) — Jinja2 + httpx الحاليان يكفيان |
| **الحجم** | **≤ 900 سطر مُضاف** في `src/ui/**/*.py` + `src/ui/templates/**/*.html` مجتمعةً (الأمر في §1-ب)؛ `ar.json` و`tests/` خارج العدّ |
| **CI** | 6/6 قبل طلب المراجعة؛ المخطط يعيد مسار المتصفح الكامل (§3-1) حياً |
| **المالك** | 🔑 **تدوير التوكن (≥ 30 ظهوراً)** · `--workers 1` · بوابة TTFV ≤ 30 دقيقة + Meta App Review **مفتوحة الآن** (§7) |

---

## 0. قواعد ثابتة (لا تتغير)

1. **لا تلمس:** `src/modules/auth/*`, `src/core/contract.py`, `src/core/public_fetch.py`, `pyproject.toml`, `src/modules/brand_brain/internal_metrics.py`, `alembic/versions/*`. **ولا تلمس أي `src/modules/**` في هذا السبرنت** إلا الاستثناء الوحيد المذكور في §4-ج (سطر واحد في `social_connect/router.py` ممنوع أيضاً — انظر القرار).
2. **D-068 حرفياً:** `src/ui` يتكلم مع النظام عبر `ApiClient` فقط. لا `sqlalchemy`، لا `httpx` مباشر، لا استيراد من `src.modules.*` داخل `src/ui` (اختبار AST موجود — يبقى أخضر). **لا منطق أعمال في `src/ui`**: تحويل نموذج ⇒ JSON، عرض أخطاء، إعادة توجيه — فقط.
3. **D-055/D-069:** لا أحداث جديدة ولا نص عميل في السجلات — الواجهة لا تكتب شيئاً مباشرةً أصلاً.
4. **لا `ErrorCategory` جديدة.** أكواد الأخطاء تُعرض من `ar.json` بالمفتاح `errors.<code>`؛ كل كود جديد تراه الواجهة يُضاف إلى `ar.json`.
5. كل كوميت يُدفع فوراً؛ المنفذ لا يدمج (D-063). مسابير `_probe_*.py` لا تُرتكب.
6. **`PROTECTED_ROUTE_COUNT == 98` ثابت** (لا مسار API جديد). عدّاد `/app` يرتفع من 29 إلى **38 بالضبط** (§2).

---

## 1. SPRINT-14-A — النطاق (مرتَّب الأولوية؛ A-1..A-4 لا تُقطع، A-5 يُختزل عند ضيق الحجم)

| # | الشاشة/البند | المسارات الجديدة في `/app` | يستهلك (موجود — لا تعديل) | معيار القبول |
|---|---|---|---|---|
| **A-1** | **التسجيل** `register.html` (بريد، كلمة مرور ×2، اسم المنشأة بالعربية، slug مخفي) | `GET /app/register` · `POST /app/register` | `POST /auth/register` (201 `TokenOut` + كوكي refresh) | 303 ⇒ `/app/` بجلسة صالحة **من استجابة التسجيل مباشرة** (§4-أ)؛ `email_taken`/`tenant_slug_taken`/`password_too_weak`/`rate_limited` بالعربية بنفس كود الاستجابة؛ كلمتا المرور غير متطابقتين ⇒ 400 محلي بلا نداء API؛ رابط «سجّل» في `login.html` ورابط «ادخل» في `register.html` |
| **A-2** | **أول علامة** — `brands.html` الحالة الفارغة تصبح نموذج «أنشئ علامتك الأولى» (الاسم بالعربية، **القطاع** `restaurants`/`clinics` أزرار راديو، slug مخفي خلف «متقدم») | `POST /app/brands` | `POST /brands` ثم `PUT /brands/{id}/brain/profile` مع `If-Match` من `GET …/brain/profile` | 303 ⇒ `/app/b/{id}/settings#connect`؛ `brand_profiles.sector` محفوظ؛ `slug_taken` ⇒ إعادة المحاولة التلقائية ثم رسالة عربية (§4-ب)؛ العلامة تُنشأ **قبل** القطاع — فشل القطاع لا يُفشل الإنشاء بل يُظهر تنبيهاً |
| **A-3** | **إكمال ربط Meta**: صفحة اختيار الصفحات بعد OAuth + جسر المتصفح | `GET /app/social/meta/callback` · `GET /app/b/{id}/settings/connections/{cid}/pages` · `POST /app/b/{id}/settings/connections/{cid}/pages` | `GET /social/meta/callback` (JSON 201) · `GET …/connections/{cid}` · `PUT …/connections/{cid}/pages/selection` | الجسر (§4-ج) ينتهي 303 إلى صفحة الاختيار؛ الصفحة تعرض كل الصفحات مع checkbox (المختارة مسبقاً `checked`)؛ POST فارغ ⇒ خطأ عربي بلا نداء؛ نجاح ⇒ 303 `/app/b/{id}/settings` + flash؛ `user_denied`/`invalid_oauth_state`/`token_expired` بالعربية؛ **`PUBLIC` لا يتغير** (مسار الجسر تحت `/app` يتطلب جلسة) |
| **A-4** | **WhatsApp Cloud API** بطاقة في `settings.html`: ربط (إن لم يوجد حساب) / تحديث + **تدوير** (إن وجد) | `POST /app/b/{id}/settings/whatsapp` · `POST /app/b/{id}/settings/whatsapp/rotate` | `GET/POST/PUT …/whatsapp/account` · `POST …/whatsapp/account/rotate` | أسماء حقول النموذج **حسب §4-د حصراً**؛ الحقول السرّية `type="password"` `autocomplete="new-password"` بلا `value=`؛ صفحة النجاح تعرض `phone_number_id` و`display_phone` و`status` و`quality_rating` فقط؛ أخطاء التحقق (422) تُترجم إلى «الحقل X قصير/غير صالح» بالعربية |
| **A-5** | **شريط تقدّم الانضمام** في `/app/` (brands.html أعلى القائمة): حساب ✓ · علامة · قناة (Meta صفحة مختارة **أو** WA مربوط) · أول مسودة | — | `GET /brands` · `GET …/connections` · `GET …/whatsapp/account` (404 = لا) · `GET …/content/items?limit=1` | 4 خطوات `data-step="account|brand|channel|draft" data-done="true|false"`؛ كل خطوة غير مكتملة تحمل رابط الإجراء التالي؛ **يختفي كلياً** عند اكتمال الأربع؛ يُحسب لأول علامة فقط؛ أي فشل في نداء فرعي ⇒ الخطوة «غير معروفة» لا 500 |

**مجموع مسارات `/app` الجديدة: 9 ⇒ 29 + 9 = 38.**

### 1-ب. أوامر التحقق (ينسخها المنفذ إلى §6 مع نتائجها)

```bash
# الحجم (≤ 900)
git diff --numstat origin/main..HEAD -- 'src/ui/*.py' 'src/ui/**/*.py' 'src/ui/templates/*.html' 'src/ui/templates/**/*.html' | awk '{a+=$1} END{print "ui py+html added:", a}'
# لا تغيير خارج الواجهة والاختبارات والترجمة
git diff --stat origin/main..HEAD -- src/modules src/core src/main.py alembic pyproject.toml | tail -1   # يجب أن يكون فارغاً
# D-068 — لا استيراد وحدات داخل src/ui
grep -rn "from src.modules\|import sqlalchemy\|import httpx" src/ui/ | grep -v "src/ui/api.py" ; echo "exit=$? (يجب 1)"
# لا أسرار/أسماء حقول محظورة في القوالب
grep -rnE "gAAAA|system_token|api_key|app_secret|verify_token" src/ui/templates/ ; echo "exit=$? (يجب 1)"
# العدّادات
python -m pytest tests/test_auth_guard.py -q -p no:cacheprovider 2>&1 | tail -1          # 98 ثابت
python - <<'EOF'
from src.main import create_app
print("/app routes:", sum(1 for r in create_app().routes if getattr(r,"path","").startswith("/app")))
EOF
# البوابات
python -m ruff check . && python -m pytest tests -q -p no:cacheprovider --ignore=tests/integration 2>&1 | tail -1
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_growth_os_test python -m pytest tests/integration -q -p no:cacheprovider 2>&1 | tail -1
```

---

## 2. الاختبارات المطلوبة (`tests/ui/test_ui_onboarding.py` — على fakes `tests/ui/conftest.py`)

1. **المسار الكامل بالمتصفح** (`TestClient` بالكوكيز، `follow_redirects=False`): `GET /app/register` ⇒ `POST /app/register` 303 ⇒ `GET /app/` 200 بشريط 4 خطوات (`account=true`) ⇒ `POST /app/brands` (اسم عربي + قطاع) 303 ⇒ `GET /app/b/{id}/settings` 200 ⇒ `POST …/settings/whatsapp` 303 ⇒ `GET /app/` ⇒ `channel=true`. **صفر نداء API مباشر من الاختبار.**
2. **A-1 أخطاء:** بريد مكرر ⇒ 409 + النص العربي لـ`email_taken`؛ كلمتا مرور مختلفتان ⇒ 400 دون أن يلمس `FakeStore`؛ `rate_limited` ⇒ 429 بالعربية.
3. **A-2 slug:** اسم «مطعم النيل» ⇒ slug `brand-xxxxxxxx` (8 hex) مقبول من `Slug`؛ اسم `Nile Coffee 2` ⇒ `nile-coffee-2`؛ تعارض slug ⇒ محاولة ثانية بلاحقة ثم نجاح؛ القطاع محفوظ في `FakeBrandBrainRepository`.
4. **A-3 جسر:** `GET /app/social/meta/callback?code=x&state=y` بجلسة ⇒ 303 إلى `…/connections/{cid}/pages`؛ بلا جلسة ⇒ 303 `/app/login?next=…` (الـ`next` يحفظ `code`/`state`)؛ `?error=access_denied` ⇒ صفحة الإعدادات برسالة عربية؛ صفحة الاختيار تُظهر الصفحات وPOST بـ`page_ids` يصل إلى `PUT …/pages/selection` ⇒ 303.
5. **A-4 سرّية:** بعد POST ناجح `GET settings` لا يحوي أي قيمة سرّية أُرسلت (assert `secret_value not in html`)؛ أسماء الحقول في HTML مطابقة لـ§4-د؛ 422 من API ⇒ 400 عربي يحدد الحقل.
6. **A-5:** حالات (0 علامات) ⇒ `brand=false`؛ (علامة بلا قناة) ⇒ `channel=false` مع رابط `#connect`؛ (مكتمل) ⇒ الشريط غائب (`data-onboarding` غير موجود).
7. **CSRF:** كل POST جديد يرفض 403 بلا `csrf` (اختبار معيَّش لكل المسارات التسعة — `test_ui_hardening.py` يُوسَّع).
8. **i18n:** اختبار يجمع كل `t("…")` من القوالب الجديدة ويؤكد وجودها في `ar.json`.

---

## 3. معايير القبول النهائية

1. §2 كلها خضراء + CI 6/6 + تكامل PG 163 (لا تغيير متوقَّع في عدد التكامل).
2. `ui py+html added ≤ 900` · `src/modules|src/core|src/main.py|alembic` **0 سطر** · PROTECTED 98 · `/app` 38.
3. grep الأسرار في القوالب = لا نتائج؛ grep الاستيراد في `src/ui` = لا نتائج.
4. المخطط يعيد المسار §2-1 حياً على PG17 مع `FakeGraph` ويقيس TTFV تقريبياً (عدد الشاشات ≤ 6 من التسجيل حتى قناة مربوطة).

---

## 4. القرارات المعمارية الملزمة (أربع حقائق عبر الوحدات — مُحسومة هنا)

### 4-أ. التسجيل = دخول (لا نداء `/auth/login` ثانٍ)
`POST /api/v1/auth/register` (`auth/router.py:179-206`) يعيد **201 `TokenOut` ويضع كوكي refresh** بنفس آلية `/auth/login`، ويشترك معه في `IP_BUCKET`. ⇒ `POST /app/register` يستدعي `/auth/register` مرة واحدة ثم يستخدم **نفس كود `login_submit`** (`ui/router.py:419-434`): `refresh_value = response.cookies.get(settings.auth_refresh_cookie_name)`، `access_token` من الجسم، `set_session(...)`، 303. **نداء login ثانٍ محظور** (يحرق حصة rate-limit ويضاعف جلسات refresh). استخرج الجزء المشترك إلى `_establish_session(response, settings, next) -> Response` واستعمله في الاثنين (هذا إعادة هيكلة محلية لا تغيّر السلوك — اختبارات `test_ui_session.py` تبقى خضراء).

### 4-ب. الأسماء العربية مقابل `Slug [a-z0-9-]{3,64}`
`RegisterRequest.tenant_slug` و`BrandCreate.slug` كلاهما `Slug` (`tenancy/domain/models.py:21`). القاعدة (دالة واحدة `derive_slug(name: str, prefix: str) -> str` في `src/ui/slug.py`، **بلا منطق أعمال — تحويل نصي فقط**):
1. حوّل الاسم إلى ASCII صغير، أبقِ `[a-z0-9]`، استبدل أي تسلسل آخر بـ`-`، قصّ الشرطات الطرفية، اقطع عند 64.
2. إن كانت النتيجة ≥ 3 أحرف ⇒ استعملها. وإلا (اسم عربي كامل) ⇒ `f"{prefix}-{secrets.token_hex(4)}"` حيث `prefix ∈ {"org","brand"}`.
3. الحقل `slug` في النموذجين **مخفي افتراضياً** خلف `<details><summary>متقدم</summary>` ويُعبَّأ بالقيمة المشتقة عبر `value=`؛ إن أرسله المستخدم غير فارغ يُستعمل كما هو (التحقق يبقى في API — 422 ⇒ رسالة عربية «المعرّف اللاتيني: أحرف صغيرة وأرقام وشرطات، 3–64»).
4. عند 409 `tenant_slug_taken`/`slug_taken` **ولم يكن المستخدم قد كتب slug يدوياً**: أعد المحاولة مرة واحدة بلاحقة `-2`، ثم مرة بـ`-<4hex>`، ثم أظهر الخطأ العربي. إن كتبه يدوياً ⇒ خطأ فوراً بلا إعادة.

### 4-ج. جسر المتصفح لـMeta OAuth — بلا لمس `social_connect` وبلا كسر اختبارات API
`GET /api/v1/social/meta/callback` (`social_connect/router.py:207-251`) يعيد 201 JSON ويبقى **كما هو حرفياً** (اختبارات API وMeta App Review تعتمده). `meta_oauth_redirect_uri` قيمة إعدادات واحدة (`config.py:97`) تُرسل إلى Meta وتُستعمل في تبادل الكود؛ **لا تغييرها في الكود**. الحل:
1. مسار واجهة جديد `GET /app/social/meta/callback?code&state&error&error_description` (جلسة مطلوبة؛ بدونها ⇒ `/app/login?next=<المسار الكامل مع الاستعلام>`).
2. يستدعي عبر `ApiClient` نفس `GET /social/meta/callback` **بنفس معاملات الاستعلام** بتوكن المستخدم (التفويض B9 يبقى في API). عند 201 ⇒ يقرأ `id` و`brand_id` من `ConnectedAccountPublic` ⇒ 303 إلى `/app/b/{brand_id}/settings/connections/{id}/pages`. عند 4xx ⇒ يستخرج `brand_id` من **لا شيء** (الحالة مجهولة) ⇒ 303 إلى `/app/` مع flash عربي للكود (`user_denied`, `invalid_oauth_state`, `provider_error`, `missing_parameters`).
3. **تهيئة المالك (وثّقها في §7 وفي `.env.example` تعليقاً فقط — لا تغيير قيمة افتراضية):** في الإنتاج يضبط المالك `META_OAUTH_REDIRECT_URI=https://<host>/app/social/meta/callback` ويسجّل نفس العنوان في تطبيق Meta. بما أن تبادل الكود يستخدم نفس القيمة (`service.py:151`) يبقى التبادل صحيحاً. **في الاختبارات** لا يتغير شيء لأن الواجهة تستدعي API بالمعاملات نفسها بغضّ النظر عن القيمة.
4. نموذج «اربط» في `settings.html` يبقى `target="_blank"`؛ أضف تحته نصاً «بعد الموافقة ستعود إلى اختيار الصفحات».

### 4-د. أسماء حقول WhatsApp في القوالب (تجنّب تصادم grep الأمان)
`WhatsAppAccountCreate` يتطلب `phone_number_id, waba_id, system_token(≥16), app_secret(≥8), verify_token(≥8)` (+ `display_phone` اختياري). معيار القبول يمنع `gAAAA|system_token|api_key|app_secret|verify_token` في القوالب. ⇒ **أسماء الحقول في HTML حصراً:**

| اسم الحقل في `<input name>` | يُطابَق في الراوتر إلى | النوع |
|---|---|---|
| `wa_pnid` | `phone_number_id` | `text` |
| `wa_waba` | `waba_id` | `text` |
| `wa_phone` | `display_phone` | `tel` |
| `wa_sys` | `system_token` | `password` |
| `wa_sec` | `app_secret` | `password` |
| `wa_verify` | `verify_token` | `password` |

التطابق يتم في دالة واحدة `_wa_payload(form) -> dict` داخل `ui/router.py` (الأسماء الحقيقية تظهر في **Python فقط**، وهذا مسموح لأن الـgrep على القوالب). التدوير يرسل الحقول الثلاثة السرّية إلى `…/account/rotate` (الفارغ منها يُحذف من الجسم — `extra="forbid"`). لا `value=` على أي حقل password، و`autocomplete="new-password"`. تسميات `ar.json`: `wa.pnid`, `wa.waba`, `wa.phone`, `wa.sys`, `wa.sec`, `wa.verify`, `wa.connect`, `wa.update`, `wa.rotate`, `wa.connected_as`.

### 4-هـ. قرارات مُرحَّلة سارية
- **لا `DELETE FROM content_items` أبداً** (14-C). · OWNER لعلامة موقوفة يصدّر ويحذف (D-081). · `RETENTION_DRY_RUN=true` يبقى.
- `ApiClient` يفتقر `put`/`delete` ⇒ أضفهما (سطران كلٌّ) — ضمن حجم 14-A.
- `lifecycle/service.py:49` `"gAAAA"` كبيرة الأحرف مقابل `k.lower()` — **يُصحَّح في 14-B** (ملف lifecycle ممنوع هنا).
- **القطاع الثالث `beauty_salons`** يُضاف في **14-B** بهجرة 0018 — 14-A يعرض القطاعين الحاليين فقط.

---

## 5. التزامات المنفذ
- لا تغيير في `src/modules`, `src/core`, `src/main.py`, `alembic`, `pyproject.toml` — **0 سطر** (الأمر في §1-ب).
- `ar.json` يغطي كل مفتاح جديد؛ لا نص عربي صلب في القوالب أو Python.
- §6 بالأسماء من الكود + أرقام CI + الحجم المقاس بالأمر أعلاه + لقطة HTML (نصية) لشريط التقدّم في الحالتين.
- إن تجاوز الحجم 900: اختزل A-5 إلى 3 خطوات (بلا `draft`) ووثّق ذلك؛ لا تقطع A-1..A-4.

---

## 6. تقرير المنفذ (يملؤه المنفذ)

### 6-أ. الكوميتات
| SHA | الرسالة | ملفات |
|---|---|---|
| | | |

### 6-ب. ما نُفِّذ (A-1..A-5)
| البند | الملفات/الدوال | ملاحظات |
|---|---|---|
| A-1 | | |
| A-2 | | |
| A-3 | | |
| A-4 | | |
| A-5 | | |

### 6-ج. أوامر التحقق (§1-ب) — النتائج
| الأمر | النتيجة |
|---|---|
| ui py+html added | |
| diff خارج الواجهة | |
| grep D-068 | |
| grep الأسرار | |
| PROTECTED / `/app` | / |
| ruff / unit / integration | |

### 6-د. الحدود (§0) — مُحترَمة
- [ ] 0 سطر في `src/modules|src/core|src/main.py|alembic|pyproject.toml`
- [ ] لا تبعية جديدة
- [ ] CSRF على كل POST جديد
- [ ] `/app` = 38 · PROTECTED = 98

### 6-هـ. انحرافات/ملاحظات للمخطط
-

---

## 7. ما ينتظر المالك (بوابة مفتوحة بعد دمج #55)
1. 🔑 **تدوير GitHub token فوراً** (ظهر ≥ 30 مرة في المحادثات).
2. **TTFV ≤ 30 دقيقة** على 3 علامات حقيقية بعد دمج 14-A (يُقاس من `/app/register` إلى أول مسودة معتمدة — المحتوى يأتي في 14-B؛ حتى ذلك الحين يُقاس حتى «قناة مربوطة»).
3. **Meta App Review:** تسجيل `https://<host>/app/social/meta/callback` كـRedirect URI إضافي في تطبيق Meta وضبط `META_OAUTH_REDIRECT_URI` بنفس القيمة في الإنتاج (§4-ج-3). الصلاحيات: `pages_manage_posts`, `pages_read_engagement`, `instagram_content_publish`, `instagram_basic`.
4. `--workers 1` يبقى شرط التشغيل (N1) · `RETENTION_DRY_RUN=true` · نسخة احتياطية يومية مفعَّلة (`scripts/backup_*`).
5. اعتراض (إن وُجد) على القطاع الثالث `beauty_salons` قبل إصدار 14-B.
