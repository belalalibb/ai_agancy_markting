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

| البند | الحالة (2026-10-31، `main` @ `0ceb125` بعد SPRINT-07) | المرجع |
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
| **رؤية حقول `provenance`** | ✅ طبقتان (D-066): `PUBLIC_PROVENANCE_KEYS={kind, generated_at, prompt_version}` لكل الأدوار؛ `provider/model/prompt_hash/tokens/latency` في `generation_log` لـOWNER/MANAGER فقط؛ حارس اختباري على جسد VIEWER (تفصيل + قائمة) ∩ المفاتيح الخاصة = ∅ — أُغلق في SPRINT-07 A-1 | `src/modules/content/domain/models.py::public_provenance`, `sprints/SPRINT-07_REVIEW.md` §1-أ |
| **ترقيم المراجعات تحت التزامن** | ✅ `get_for_update` (`SELECT … FOR UPDATE` على صف العنصر) ثم `COALESCE(MAX(revision_no),0)+1` داخل القفل؛ A3-I1: 5 طلبات متزامنة ⇒ 5 مراجعات متتالية بلا 500 — أُغلق في SPRINT-07 A-3 | `src/modules/content/infrastructure/postgres_repository.py:70-77,160` |
| **No-Bypass للبوابة (D-068)** | ✅ `ruff` `TID251 banned-api` لـ`httpx/openai/anthropic/litellm/google.generativeai` خارج `ai_gateway/**`, `social_connect/meta_client.py`, `core/**` + اختبار AST `test_no_bypass`؛ `import httpx` في 4 ملفات بالضبط | `pyproject.toml [tool.ruff.lint.flake8-tidy-imports]`, `tests/modules/ai_gateway/` |
| **عقد المزوّد (D-067)** | ✅ 12 فئة خطأ مجمَّدة باختبار؛ `credential_mode ∈ {platform, user_key}`؛ الواجهة تترجم أي شكل استجابة إلى فئة؛ HTTP ثابت للعميل (429/503/422/502) **بلا نص المزوّد**؛ `Retry-After` على `rate_limited` | `src/modules/ai_gateway/contract.py`, `facades/openai_compatible.py`, `content/router.py::_AI_HTTP` |
| **ميزانية رموز يومية لكل علامة** | ✅ `AI_DAILY_TOKEN_BUDGET_PER_BRAND` محسوبة من `provenance` ⇒ 429 `ai_budget_exceeded` + `Retry-After` — حماية تكلفة مستقلة عن حدّ 20/ساعة داخل العملية | `src/modules/content/service.py` (A-5) |
| **النشر الفعلي بموافقة بشرية (D-055)** | ✅ `schedule` يتحقق من الدور/الحالة/الهدف/نطاقي النشر (`pages_manage_posts`, `instagram_content_publish` — غير مُتحقَّق ⇒ 409 `reauth_required`) ويُنشئ `publish_jobs` في **المعاملة نفسها**؛ فهرس جزئي `uq_publish_jobs_active_item` يمنع مهمتين نشطتين؛ `published`/`publish_failed` انتقالات **نظامية** خارج `transition()` بلا فاعل بشري (`ck_approval_events_actor_required`)؛ هوية من أذن في `publish_jobs.scheduled_by_*` | `alembic/versions/0006_publishing.py`, `src/modules/content/domain/lifecycle.py::SYSTEM_TRANSITIONS` |
| **لا تكرار صامت للنشر** | ✅ `FOR UPDATE SKIP LOCKED` + `lease_owner`؛ صف `publish_attempts(outcome NULL)` يُلتزم **قبل** نداء Graph؛ إيجار منتهٍ بمحاولة مفتوحة ⇒ `ambiguous` + `publish_failed` **بلا نداء** وإعادة محجوبة (409 `manual_verification_required`)؛ `unknown` فشل نهائي لا يُعاد؛ عاملان على محرّكين ⇒ كل مهمة مرة واحدة بالضبط (B6-I3 3/3) | `src/modules/publishing/scheduler.py`, `tests/integration/test_scheduler_pg.py` |
| **توكن الصفحة في النشر** | ✅ يُفكّ لحظة النداء داخل `PostgresPageTokenProvider` (اتصال `active` + صفحة مختارة فقط) و`del` في `finally`؛ `ResolvedPageTarget.page_token` بـ`repr=False`؛ Bearer + `appsecret_proof` فقط؛ `token_invalid` يقلب الاتصال `reauth_required` **خارج** معاملة النتيجة | `src/modules/publishing/infrastructure/meta_publisher.py` |
| **أخطاء Meta في النشر** | ✅ تصنيف بالكود أولاً إلى 8 فئات داخلية؛ `graph_error_code/subcode/trace_id` (أرقام ومعرّف) لـOWNER/MANAGER، **لا نص Meta** في أي نموذج أو سجل | `meta_publisher.py::classify_graph_error`, `PublishStatusDetail` |
| **حلقة المجدول في العملية** | ⚠️ `SCHEDULER_ENABLED` (افتراضي `false`) داخل `lifespan`؛ القاعدة التشغيلية: **نسخة واحدة** فقط في V1 (`SKIP LOCKED` شبكة أمان لا تصميم توازٍ)؛ فصلها إلى `worker` مستقل بند M9 (مؤجَّل #15). إقلاع بلا `TOKEN_ENCRYPTION_KEY` مع المجدول مفعَّلاً ⇒ **يُرفض** (SPRINT-08 A-1) | `src/main.py:67-82`, `sprints/SPRINT-07_REVIEW.md` §3-3, §4-أ |
