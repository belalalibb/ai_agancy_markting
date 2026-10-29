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

- **حالة التنفيذ:** `[ ] مكتمل ومختبر`
- **رقم الكوميت (Commit SHA):** `...`
- **نتيجة تشغيل pytest:** `...`
- **الملفات التي تم إنشاؤها:** `...`
- **ملاحظات أو أسئلة:** `...`
