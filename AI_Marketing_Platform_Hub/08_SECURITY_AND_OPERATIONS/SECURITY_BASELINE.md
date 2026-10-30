# 🔐 خط الأمان الأساسي وإدارة الأسرار (Security Baseline)
**تاريخ الاعتماد:** 28 أكتوبر 2026  
**المرجعية:** ميثاق الخدمة F1 ومعايير التنفيذ F4

---

## 🛡️ 1. المبادئ الأمنية الصارمة

1. **الالتزام الكامل بانعدام الأسرار داخل الكود والتوثيق (Zero-Secret Policy):**
   - يُحظر تماماً تخزين أي مفاتيح API، أو كلمات مرور، أو رموز JWT السرية داخل الكود المصدري أو ملفات التوثيق أو مستودع Git.
   - الاعتماد الحصري على متغيرات البيئة المشفرة (`.env`) المحمية بملف `.gitignore`.
2. **محرك تعقيم وتطهير الأسرار التلقائي (Secret Sanitizer):**
   - تفعيل كود التطهير المدمج في `libs/envelope` الذي يقوم بفحص النصوص آلياً وحذف أي نص يطابق نمط المفاتيح المشهورة (مثل `gsk_`, `sk-`, `hf_`, `Bearer `) قبل تسجيلها أو نقلها.
3. **تشفير مفاتيح العملاء (BYOK Encryption at Rest):**
   - عندما يُدخل العميل مفتاحه الخاص (مثل مفتاح OpenAI أو ElevenLabs)، يُشفر المفتاح في قاعدة البيانات باستخدام خوارزمية **AES-256-GCM** مع مفتاح تشفير رئيسي مخزن في خادم إدارة الأسرار.
   - **استثناء مرحلي موثَّق (D-058، قرار المالك 2026-10-29):** التنفيذ الحالي لتوكنات Meta في `src/core/security.py` يستخدم `MultiFernet` (AES-128-CBC + HMAC-SHA256، تشفير مُوثَّق مع تدوير مفاتيح)، محصوراً خلف واجهة `TokenEncryptionService`. الانتقال إلى AES-256-GCM يتم بتبديل التنفيذ خلف الواجهة + هجرة إعادة تشفير، دون تغيير أي مستهلك.
4. **المصادقة والتحكم بالصلاحيات (RBAC & JWT):**
   - استخدام رموز JWT قصيرة الأجل (15 دقيقة) مع Refresh Tokens مشفرة في ملفات تعريف ارتباط آمنة (HttpOnly, Secure Cookies).
   - التحقق الصارم من صلاحيات المستخدم في كل طلب برمجياً لضمان عدم وصول أي مستخدم لبيانات شركة أخرى.
   - **المواصفة التنفيذية المعتمدة (SPRINT-04):** Argon2id لكلمات المرور (`argon2-cffi`)؛ JWT وصول HS256 بعمر 900 ثانية مع `iss`/`aud`/خوارزمية صريحة؛ Refresh **دوّار** في كوكي `__Host-refresh` (HttpOnly, Secure, SameSite=Strict, Path=/api/v1/auth) يُخزَّن منه `sha256` فقط؛ **كشف إعادة الاستخدام يُبطل كل جلسات المستخدم**؛ CSRF برأس مخصص أو Origin ضمن قائمة؛ دخول بتوقيت ثابت؛ المسارات الحساسة تتحقق من الجلسة في DB (`CurrentUserFresh`). ممنوع `python-jose` و`passlib`.

---

## 📊 2. حالة التنفيذ الفعلية (تُحدَّث من مراجعات المخطط)

| البند | الحالة (2026-10-30، `main` @ `20a18fc` بعد SPRINT-06) | المرجع |
|---|---|---|
| Zero-Secret في الكود والمستودع | ✅ فحص أسرار يدوي في كل مراجعة + `gitleaks` في CI على كامل التاريخ | `.github/workflows/ci.yml` |
| Zero-Secret في واجهات API | ✅ حارس اختباري على `/openapi.json`: لا حقل باسم يحوي `token` إلا `has_page_token` | `tests/modules/social_connect/test_router.py` |
| تشفير توكنات Meta المخزَّنة | ✅ MultiFernet خلف `TokenEncryptionService` (استثناء D-058) | `src/core/security.py` |
| `state` في OAuth | ✅ HMAC موقَّع `{t, b, n, iat}` بمدة صلاحية؛ اختبار تلاعب حتمي على مستوى البايت | `src/core/oauth_state.py` |
| هوية Meta | ✅ من `/me` + `appsecret_proof`؛ `/debug_token` مُتحقَّق (`is_valid`, `app_id`, `user_id`) | `src/modules/social_connect/service.py` |
| عزل المستأجرين | ✅ `brand_id` + `BrandAccessResolver` (404 عند انعدام الوصول) | `src/modules/tenancy/` |
| **هوية الفاعل** | ✅ Argon2id + JWT HS256 (15 د) + Refresh دوّار في كوكي `__Secure-refresh` (HttpOnly/Strict/Secure، `Path=/api/v1/auth`)؛ كشف إعادة الاستخدام يُبطل كل الجلسات؛ إبطال-أولاً بـ`rowcount==1` (سباق مُثبَت على PG: `[200,401]`)؛ `CurrentUserFresh` يفحص `revoked_at`+`expires_at`+`users.is_active` | `src/modules/auth/`, `sprints/SPRINT-04.1_REVIEW.md` |
| أسرار Graph في الطلبات الصادرة | ✅ `client_secret`/التوكنات في `Authorization: Bearer` أو جسم POST فقط؛ `appsecret_proof` وحده في الرابط؛ `paging.next` يُنقّى ويُقيَّد بـ`https` + مضيف Graph؛ `SecretRedactingFilter` على مسجّل الجذر و`httpx`/`httpcore` على WARNING — **مُثبَت تجريبياً** (تجربة تسريب httpx: لا سر في السجل) | `src/modules/social_connect/meta_client.py`, `src/core/logging.py` |
| عزل الوكالة داخل العلامة الواحدة | ✅ الصفحات غير المختارة لا يراها إلا مالك الاتصال (`connected_by_tenant_id`)؛ `is_connection_owner` في الاستجابة؛ اختيار/فصل من غير المالك ⇒ 403 | `src/modules/social_connect/schemas.py`, `service.py` |
| علامة `reauth_required` | ✅ تُكتب خارج معاملة الطلب (`PostgresStatusMarker`) فلا يبتلعها `rollback` | `src/modules/social_connect/infrastructure/status_marker.py` |
| سباقات الفهارس الفريدة | ✅ 409 حتمي لا 500 (اختبارات تزامن حقيقية على PG) | `tests/integration/test_postgres_persistence.py` |
| تراخيص شجرة التشغيل | ✅ 34–37 حزمة كلها MIT/BSD/Apache/PSF؛ `certifi` بـ **MPL-2.0** مقبولة بوعي (ترخيص ملفّي ضعيف الـcopyleft، لا يمسّ النواة) | `scripts/check_licenses.py` |
| تحديد معدل الطلبات | ⚠️ داخل العملية (نافذة منزلقة، محدود الذاكرة `max_keys=10_000` بطرد الأقدم) — لا يتشارك بين النسخ؛ Redis بند مؤجَّل #7 | `src/modules/auth/infrastructure/rate_limit.py` |
| `POST /debug_token` | ✅ جسم `{input_token, method=GET}` + Bearer للـapp token (SPRINT-05 A-0)؛ كود 100 أو خطأ غير مصنَّف ⇒ `ERROR` + عدّاد `meta_debug_token_failures` (لا ابتلاع صامت) | `src/modules/social_connect/meta_client.py`, `src/core/metrics.py` |
| القراءات التشغيلية (`/posts`, `/media`, `resync`) | ✅ الصفحات **المختارة** فقط (غير المختارة ⇒ 404 موحَّد لكل الأدوار)؛ توكن الصفحة يُفكّ لحظة الاستخدام ويُحذف؛ Bearer + `appsecret_proof` فقط؛ `paging.next` لا يُعاد ولا يُتبع (مؤشر `after` فقط)؛ `resync` لمالك الاتصال وحده — **مُثبَت بالتقاط الطلبات الفعلية** | `sprints/SPRINT-05_REVIEW.md` §1 |
| تمييز «فارغ مؤكَّد» عن «مجهول» في النطاقات | ✅ `granted_scopes=[]` المؤكَّدة تُكتب + عمود `scopes_verified` (هجرة 0005)؛ `resync` على `reauth_required` ⇒ 409 قبل أي نداء Graph؛ `required_action=reconnect` موحَّد (SPRINT-06 A-1/A-2) | `sprints/SPRINT-06_REVIEW.md` §1-أ |
| **الموافقة البشرية على المحتوى (D-055)** | ✅ آلة حالات صريحة 42 خلية مُختبَرة؛ `approve`/`reject` لمالك العلامة فقط (MANAGER ⇒ 403)؛ انتقالات CAS `rowcount==1` ⇒ سباق approve/reject على PG `[200,409]`؛ تعديل النص بعد `draft` ينشئ مراجعة ويسحب الموافقة تلقائياً | `src/modules/content/domain/lifecycle.py`, `sprints/SPRINT-06_REVIEW.md` |
| **بوابة الذكاء الاصطناعي (D-056)** | ✅ كل نداء نموذج عبر `AIGateway` Protocol وحده؛ مفتاح المزوّد في `Authorization: Bearer` فقط (مُثبَت بالتقاط الطلب: لا مفتاح في الرابط/`params`/السجل)؛ `sk-…` في مرشّح التنقيح؛ لا نص برومبت/مخرجات في سجلات INFO؛ حدّ 20 طلباً/ساعة/علامة | `src/modules/ai_gateway/`, `src/core/logging.py` |
| **رؤية حقول `provenance`** | ⚠️ `ContentRevisionPublic.provenance` يمرّ كاملاً (provider/model/tokens/latency) لكل الأدوار بينما `generation_log` يُخفى عن VIEWER — خرق جدول الرؤية D-064 (لا سرّ) — **يُغلق في SPRINT-07 A-1** | `sprints/SPRINT-06_REVIEW.md` §4 O1 |
| **ترقيم المراجعات تحت التزامن** | ⚠️ `max+1` بلا قفل ⇒ `IntegrityError` 500 للخاسر (مُثبَت على PG 3/3) — **يُغلق في SPRINT-07 A-3** بقفل صف العنصر `FOR UPDATE` | `sprints/SPRINT-06_REVIEW.md` §4 O3 |
