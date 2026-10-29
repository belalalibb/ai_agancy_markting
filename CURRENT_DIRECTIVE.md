# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **ملف التوجيه المباشر بين المخطط والمنفذ:** يكتب المخطط مهمة الدورة هنا مباشرة، ويقوم المنفذ بقراءتها وتنفيذها دون أي حاجة للنسخ واللصق من جانب المالك.

---

## بطاقة المهمة (Task Card)

- **معرف المهمة (Task ID):** `SPRINT-01-FASTAPI-META-FOUNDATION`
- **الحالة:** `READY_FOR_EXECUTION` (جاهز للتنفيذ فوراً)
- **الفرع المستهدف:** `work/implementation`
- **المخطط المسؤول:** AI Master Planner
- **المنفذ المستهدف:** AI Executor Agent
- **تاريخ الإصدار:** 2026-10-29

---

## 1. الهدف التنفيذي (Objective)

تأسيس الهيكل الأساسي لتطبيق **FastAPI** بنمط الـ Modular Monolith، وتجهيز وحدة تكامل منصات التواصل الرسمية الأولى (**Meta Graph API** لفيسبوك وإنستغرام) مع تطبيق تشفير التوكنات وتدفق OAuth 2.0 وعقود الاختبارات المعزولة.

---

## 2. النطاق المسموح به والممنوع (Scope & Boundaries)

### مسموح بإنشائه وتعديله:
- `src/` أو `backend/`
  - `src/core/` (إعدادات التطبيق، التشفير، الاتصال بقاعدة البيانات)
  - `src/modules/social_connect/` (محول Meta Graph API، نماذج Pydantic، مسارات OAuth)
- `tests/`
  - `tests/modules/social_connect/` (اختبارات الوحدات والـ Mocks لـ Meta API)
- `requirements.txt` أو `pyproject.toml`
- تحديث هذا الملف (`CURRENT_DIRECTIVE.md`) في قسم تقرير المنفذ عند الانتهاء.

### محظور منعاً باتاً:
- تعديل أي ملف في `AI_Marketing_Platform_Hub/` أو `ARCHITECTURE_LOCK.md`.
- وضع أي مفاتيح API أو أسرار حقيقية في الكود؛ استخدام Mocking وبيئة وهمية للاختبارات.
- استخدام أي مكتبة خاضعة لرخصة AGPL-3.0.
- الكوميت المباشر على فرع `main`.

---

## 3. المواصفات الفنية التفصيلية (Technical Specifications)

1. **إعدادات المشروع والحزم:**
   - Python 3.12+
   - `fastapi >= 0.110.0`
   - `uvicorn >= 0.28.0`
   - `pydantic >= 2.6.0`
   - `pydantic-settings >= 2.2.0`
   - `httpx >= 0.27.0` (للطلبات غير المتزامنة لـ Meta API)
   - `cryptography >= 42.0.0` (لتشفير توكنات الوصول قبل حفظها)
   - `pytest >= 8.0.0` و `pytest-asyncio`

2. **نموذج بيانات Meta Connect (Pydantic Schemas):**
   - `MetaAuthRequest`: يحتوي على كود التفويض `code` و `redirect_uri`.
   - `MetaTokenResponse`: يحتوي على التوكن قصير المدى، وطويل المدى، ومدة الصلاحية.
   - `MetaPageInfo`: معرف الصفحة، اسم الصفحة، معرف حساب Instagram Business المرتبط، وصلاحيات الوصول.

3. **خدمة التكامل (MetaGraphClient):**
   - تبادل الـ Authorization Code بـ Long-Lived User Access Token.
   - استدعاء `GET /me/accounts` لاسترجاع الصفحات وحسابات إنستغرام التابعة لها.
   - معالجة أخطاء الصلاحيات وانتهاء التوكن و Rate Limiting بأسلوب موثوق.

4. **طبقة التشفير (TokenEncryptionService):**
   - دالة تشفير `encrypt_token(raw_token: str) -> str` باستخدام Fernet / AES-GCM.
   - دالة فك تشفير `decrypt_token(cipher_token: str) -> str`.

---

## 4. معايير القبول والاختبارات الإلزامية (Acceptance Criteria)

- [ ] تشغيل تطبيق FastAPI وظهور صفحة Swagger UI عند استدعاء `/docs`.
- [ ] وجود اختبارات تغطي:
  - نجاح تشفير وفك تشفير التوكنات ومطابقة الأصل.
  - نجاح دورة OAuth وتبادل التوكن باستخدام Mock للـ Meta Graph API.
  - استرجاع قائمة الصفحات وحسابات إنستغرام بشكل صحيح.
  - المعالجة السليمة لخطأ `OAuthException` عند إرسال توكن منتهي الصلاحية.
- [ ] تشغيل `pytest` بنجاح كامل: **Zero Failures**.
- [ ] كود نظيف ومتوافق مع قواعد PEP 8 مع كتابة Type Hints كاملة.

---

## 5. أمر التحقق والتشغيل (Run Command)

```bash
pytest -v tests/
```

---

## 6. تقرير المنفذ عند الانتهاء (يملؤه الوكيل المنفذ بعد إتمام المهمة)

*يتم ملء هذا القسم من قِبل المنفذ فور الانتهاء وقبل تسليم المهمة للمالك والمخطط:*

- **حالة التنفيذ:** `[x] مكتمل ومختبر`
- **رقم الكوميت (Commit SHA):** `118c0bbae1882012e1825fc1595d24db19489657` (كوميت الكود الموحّد على `work/implementation`؛ يليه كوميت توثيقي لهذا التقرير و `WORK_STATE.md`)
- **نتيجة تشغيل pytest:** `pytest -v tests/` → **123 passed, 0 failed** (≈31s) — التغطية الإجمالية **99.6%** (`--cov=src`, الحد الأدنى المضبوط 85%) — `ruff check` + `ruff format --check`: **All checks passed** (PEP 8 + Type Hints كاملة).
- **الملفات التي تم إنشاؤها:**
  - **الإعدادات والحزم:** `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `.env.template` (بدون أي أسرار؛ `.env` مستثنى من Git).
  - **النواة `src/core/`:** `config.py` (Settings مكتوبة بـ pydantic-settings، أسرار بنوع `SecretStr`)، `security.py` (`TokenEncryptionService` بـ Fernet/MultiFernet: `encrypt_token` / `decrypt_token` / `rotate` + مصنع يفشل مبكراً عند غياب المفتاح)، `database.py` (محرك SQLAlchemy كسول لـ PostgreSQL 16+ وفق D-032؛ لا اتصال عند الاستيراد).
  - **وحدة `src/modules/social_connect/`:** `schemas.py` (`MetaAuthRequest`, `MetaTokenResponse`, `MetaPageInfo`, `MetaConnectionResult`, `MetaAuthorizeUrlResponse`, `MetaDebugTokenInfo` — التوكنات مقنّعة `***` في JSON/repr)، `exceptions.py` (تحويل أخطاء Graph API إلى استثناءات مكتوبة: `MetaOAuthError`/`MetaTokenExpiredError` لكود 190، `MetaPermissionError`، `MetaRateLimitError`، `MetaTransientError`، `MetaNetworkError`)، `meta_client.py` (`MetaGraphClient`: بناء رابط Facebook Login مع `state` CSRF، تبادل الـ code → توكن قصير → توكن طويل المدى، `GET /me/accounts` مع تتبّع الصفحات (pagination) و`appsecret_proof`، إعادة محاولة محدودة مع backoff أُسّي واحترام `Retry-After`)، `service.py` (`MetaConnectService`: التدفق الكامل وتشفير التوكن قبل مغادرة الطبقة)، `router.py` (مسارات `/api/v1/social/meta/authorize-url|token|connect` مع تحويل الأخطاء إلى 401/403/429/502/400/503).
  - **نقطة الدخول:** `src/main.py` (مصنع التطبيق، `/docs`, `/redoc`, `/health`).
  - **الاختبارات `tests/`:** `conftest.py` (بيئة معزولة + Mock لـ Graph API عبر `respx`)، `core/test_config.py`, `core/test_security.py`, `core/test_database.py`, `modules/social_connect/test_schemas.py`, `test_exceptions.py`, `test_meta_client.py`, `test_service.py`, `test_router.py`.
  - **أدوات التشغيل:** `scripts/backup_progress.sh` (نسخة احتياطية قابلة للاسترجاع: git bundle + patch للتغييرات غير المُكمَّتة → محلياً + AI Drive + دفع للفرع البعيد؛ وضع `--restore` للاستعادة).
- **ملاحظات أو أسئلة:**
  1. **معايير القبول — كلها محققة:** `/docs` يعمل على خادم uvicorn فعلي (HTTP 200 + Swagger UI)؛ اختبارات التشفير/فك التشفير ومطابقة الأصل (+ رفض العبث وتدوير المفاتيح)؛ دورة OAuth كاملة بـ Mock؛ استرجاع الصفحات وحسابات Instagram Business (مع pagination)؛ معالجة `OAuthException` كود 190/463 للتوكن المنتهي → `MetaTokenExpiredError` → HTTP 401 ببنية خطأ موحّدة تتضمن `fbtrace_id`.
  2. **قرار تصميمي يحتاج تأكيد المخطط:** أخطاء الصلاحيات التي يُرجعها Graph بنوع `OAuthException` (مثل `(#200) Requires pages_show_list`) تُصنَّف كـ `MetaOAuthError` → 401 (يتطلب إعادة تفويض) وليس 403؛ أما كود 10/200-299 بدون نوع OAuth فتُصنَّف 403. يمكن عكس الأولوية بسطر واحد في `map_graph_error` إن رغب المخطط.
  3. **خارج نطاق هذا السبرنت (مقترح للمهمة التالية):** تخزين `MetaConnectionResult` خلف واجهة Repository (جدول `social_connections` في PostgreSQL)، مسار `/callback` خادمي يتحقق من `state` ويستدعي `/connect`، وتوثيق صلاحيات App Review المطلوبة لدى Meta.
  4. **الأمن:** لا توجد أي أسرار في الكود أو الاختبارات أو رسائل الكوميت؛ الاختبارات تستخدم قيماً وهمية صريحة ومفتاح Fernet يُولَّد لحظياً. ملاحظات bandit المتبقية (S105/S107) إيجابيات كاذبة مُعلَّقة بـ `noqa` مع التبرير.
  5. **ملاحظة بيئة:** `pytest-asyncio` بوضع `auto`؛ تحذير Starlette حول `httpx2` تحذير توافقي من المكتبة ولا يؤثر على النتائج.
