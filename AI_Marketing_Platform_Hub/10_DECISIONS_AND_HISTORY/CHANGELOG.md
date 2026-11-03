# ⏳ السجل الزمني لتطور المشروع (Changelog & Historical Evolution)
**الفترة الزمنية:** نوفمبر 2024 — أكتوبر 2026

---

## 📅 المحطات التاريخية الكبرى للمشروع

### المحطة 1: نوفمبر 2024 — ولادة فكرة SocialAI Pro
- كتابة المستند الشامل وخطة الـ 10 أسابيع في `socialidias/impooooortannnnnnnt.md`.
- تحديد المفهوم الأساسي: منصة عربية لإدارة السوشيال ميديا بالذكاء الاصطناعي مع "نمط التسويق الذاتي".

### المحطة 2: أواخر 2025 — النماذج الأولية وأتمتة واتساب
- تجارب أولية في `AI_FULL` و `ai_agents` بلغة بايثون و Flask ونشر تجريبي على Novita sandbox.
- تطوير الإصدارات الأولى من بوت واتساب التسويقي (`whatsapp_ai`).

### المحطة 3: مطلع 2026 — نضوج الواجهات و BrandMind AI
- تطوير `first_lunch/ai-marketing-platform` بإطار Next.js 14 ودعم كامل للغة العربية (RTL) وقاعدة بيانات Supabase.
- تطوير `first_lunch/brandmind-ai` مع نظام أمان متقدم (HMAC SHA-256) ونشر سحابي على Cloudflare Pages.
- تطوير OmniReply AI Pro v3.0 مع تقنيات توزيع الرسائل لتجنب الحظر على واتساب.

### المحطة 4: منتصف 2026 — أبحاث الفيديو والذكاء متعدد المزودين
- إعداد معمارية إنتاج الفيديو الشاملة في `JS_AI_CORE/ai_video_saas_architecture.md`.
- تجارب ربط وتجاوز لمزودي الذكاء الاصطناعي في `ai_mdule-master`.

### المحطة 5: مطلع أكتوبر 2026 — دستور AI Growth OS والشريحة الأولى
- صياغة وتجميد دستور العائلة F0 وميثاق الخدمة F1 وبوابات الجودة F3.
- تطوير أول كود مشترك ومختبر: `libs/envelope` و `libs/explog` و `chat_adapter` بنجاح **29 اختباراً**.
- حصر كنز المصادر المفتوحة v2 واعتماد القرارات المعمارية D-001 إلى D-055.

### المحطة 6: أواخر أكتوبر 2026 — التدقيق التقني وتأسيس المرجع المركزي الموحد
- إنجاز تقرير التدقيق الفني الشامل للمصادر المفتوحة وتراخيص الـ AGPL (25 أكتوبر 2026).
- **28 أكتوبر 2026:** تأسيس هذا المركز المعرفي المركزي الموحد (`AI_Marketing_Platform_Hub`) ليكون المرجع الرسمي والنهائي للمنظومة.

### المحطة 7: 29–30 أكتوبر 2026 — أول كود إنتاجي مُراجَع وبوابات دمج آلية
- **SPRINT-01 → 03** على `main` بمراجعات مستقلة للمخطط (تشغيل فعلي على PostgreSQL 17 + pgvector في كل مراجعة): موصل Meta (OAuth v26.0، `state` موقَّع، هوية من `/me`، تخزين مشفَّر)، نموذج Tenant/Brand/Grants، اختيار الصفحات صراحةً، **315 اختباراً، تغطية 99.68%**.
- **CI** بخمسة فحوص منفصلة (lint / unit / integration على pgvector:pg16 / gitleaks / بوابة تراخيص D-054) — القرار D-062.
- حوكمة: إعادة ترقيم D-056/D-057، تسجيل استثناء التشفير D-058، نقل هوامش الربح إلى فرضيات غير مُتحقَّق منها، حذف نسخة الميثاق المكررة، قاعدة «المالك لا يدمج يدوياً» (D-063).
- **SPRINT-04** (المصادقة + هوية الفاعل المُتحقَّق منها: Argon2id، JWT، Refresh دوّار) مقبول مشروطاً بعد تدقيق خارجي أثبت 8 ملاحظات؛ **SPRINT-04.1-HOTFIX** أغلقها كلها (عزل الوكالة، الأسرار في Bearer/الجسم، `StatusMarker` خارج المعاملة، 409 للسباقات، `__Secure-refresh`، `CurrentUserFresh`، إبطال-أولاً، محدِّد محدود الذاكرة) — `main` @ `32c20cb`، **474 اختباراً، 99.57%**، مُتحقَّق مستقلاً (تجربة تسريب httpx + سباق refresh على PG).
- **SPRINT-05** (القراءات التشغيلية: `token_health`، `resync`، `/posts`، `/instagram/media`؛ إصلاح `debug_token` بـ`method=GET`) **مقبول ودُمج** — `main` @ `d026a2e`، **523 اختباراً، 99.62%**، مُتحقَّق مستقلاً بالتقاط الطلبات الفعلية. قرارات D-064/D-065. خريطة المحطات حتى النسخة التشغيلية الأولى في `WORK_STATE.md` §2-ب.
- **SPRINT-06** (تصحيحات O2/O3 + توحيد `required_action` + `attachments` + نواة المحتوى: `content_items`/`content_revisions`/`approval_events` بهجرة 0005، آلة حالات 42 خلية، موافقة المالك فقط D-055، CAS للانتقالات، وبوابة `AIGateway` D-056 بمزوّد وهمي + OpenAI-compatible و`/generate` بـ`provenance`) **مقبول ودُمج** — `main` @ `20a18fc`، **645 اختباراً، 99.12%**، مُتحقَّق مستقلاً (سباق المراجعات على PG). ملاحظات المالك الأربع ثبتت كلها ⇒ SPRINT-07 الجزء A. قرارات **D-066** (`provenance` عام/خاص)، **D-067** (عقد المزوّد ثلاثي الطبقات + platform/BYOK)، **D-068** (No-Bypass قابل للفحص)، **D-069** (حلقة تعلّم منقّاة من إشارات الجودة). الرأي المعماري في `AI_PROVIDER_STRATEGY.md` §3–§5 ومحكمة المراجع في `REPOSITORY_EVALUATIONS.md` 5–9.
- **SPRINT-07** (A: إغلاق O1–O4 + ميزانية رموز يومية + `prompt_version`؛ B-0: عقد المزوّد D-067 بـ12 فئة مجمَّدة + No-Bypass D-068 بـ`ruff TID251` واختبار AST؛ B: **M4 الجدولة والنشر الفعلي** — هجرة 0006 (`publish_jobs`/`publish_attempts`/`published_at`/أحداث نظامية بلا فاعل)، آلة حالات 56 خلية، `PostgresScheduler` بثلاث معاملات و`SKIP LOCKED`، نشر Facebook Page/Instagram عبر توكن الصفحة، تراجع 60/300/900، نتيجة مبهمة بلا تكرار، `GET/POST …/publish[/retry]`) **مقبول ودُمج** — `main` @ `0ceb125`، **792 اختباراً، 98.58%**، مُتحقَّق مستقلاً (B6-I3 «مرة واحدة بالضبط» 3/3، B1-M1 يدوياً). قرارات المخطط على أسئلة المنفذ: إعادة «تحقّقتُ يدوياً» **مؤجَّلة** (#14)؛ `max_attempts` **عمود المهمة مصدر الحقيقة + إعداد عند الإنشاء**؛ `SCHEDULER_ENABLED` على **نسخة واحدة** في V1 (#15). دروس 15–18 في `KNOWN_ISSUES.md`.
- **SPRINT-08** (A: رفض الإقلاع بمجدول بلا مفتاح تشفير + تتبّع greenlet للتغطية + `PUBLISH_MAX_ATTEMPTS` + تسمية B7-U3؛ B: **M5 جاهزية تعدد المنصات** — `Channel` ككيان أول، `Publisher` لكل منصة خلف سجل، Meta مرجعاً، محوّلات **هيكلية** لـLinkedIn/X/TikTok بلا اعتمادات ولا نداء شبكي، مصفوفة التفعيل، `GET /channels` بالقدرات) **مقبول** — `main` @ `e414c5a` (PR #26 دُمج بواسطة المخطط؛ 830 اختباراً، 98.87%). المخطط أثبت بالتجربة ثلاث ثغرات كامنة من SPRINT-07 (F1 CHECK مقابل الأرشفة؛ F2 إعادة POST ذي أثر جانبي؛ F3 قفل عبر الشبكة + مسح `media_refs`) ⇒ SPRINT-09 A.
- **SPRINT-09** (A: إغلاق F1/F2/F3 — هجرة 0008 + D-070؛ B: **M6 — WhatsApp Cloud API**: Webhook موقَّع، تكرار `wamid`، نافذة 24 ساعة/قوالب، صندوق وارد موحَّد، ردّ مقترح بالذكاء الاصطناعي خلف موافقة بشرية + إشارة تعلّم؛ عميل خفيف D-071؛ كل الاختبارات بلا WABA حية) **مقبول** — `main` @ `e1e2fca` (PR #29 دُمج بواسطة المخطط؛ **950 اختباراً، 98.05%**، CI 5/5 ×2، هجرات 0008/0009، 46 مساراً محمياً). المخطط أثبت على PG خمس ملاحظات تدقيق من المالك (N1 كتابات تُمحى بـrollback الطلب؛ N2 لا إعادة ربط + 500 عند تضارب الرقم؛ N3 تحديث الحالة بلا نطاق علامة ولا رتابة؛ N4 ميزانية غير متماثلة؛ N5 تصلّب webhook + سباق المسودة) ⇒ SPRINT-10 A. قرارات جديدة D-072 (ثبّت ثم أبلغ)، D-073 (طبقة الباقات)، D-074 (Brand Brain بأدلة ثلاثية)؛ مؤجَّل #18/#19؛ دروس 22–24.
- **SPRINT-10** (A: إغلاق N1–N5 + جوابا المنفذ (wamid خاص، `language_detected`) + هجرة 0010؛ B: **M7 — Brand Brain**: ملف العلامة، `brand_evidence` بثلاث درجات، مراقبة ≤ 3 منافسين من مصادر عامة، توصيات/خطة محتوى كمسودات تحت الموافقة، المزوّد الحقيقي الأول عبر `openai_compatible` + سجل تكلفة) صادر. — **مقبول ودُمج `b633540`** (PR #32؛ 1207/0/98.33%؛ ملاحظات المالك الست B1–B6 مُثبَتة على PG ⇒ Part A القادم؛ SPRINT-11 مؤجَّل لجلسة المالك الاستراتيجية — `sprints/SPRINT-10_REVIEW.md`).
- أرشيف المراجعات: `sprints/SPRINT-0X_REVIEW.md`.

### المحطة 8: 31 أكتوبر – 1 نوفمبر 2026 — M7 Brand Brain مقبول، خطة ما بعد M7، وقرارات المالك Q1–Q17
- SPRINT-10 (M7 Brand Brain + إغلاق N1–N5) رُوجع مستقلاً على PG 17 وقُبل ودُمج (`main` @ `b633540`)؛ 1,207 اختباراً، 98.33%، هجرات 0001–0012، 62 مساراً محمياً. ستة بنود B1–B6 مُثبَتة ومرحَّلة.
- `sprints/POST_M7_STRATEGIC_PLAN.md` (PR #35/#36 ⇒ `main` @ `d7e7a70`): استراتيجية/اقتصاد/معمارية حتى V1 مع أدلة حيّة (تسعير Meta WhatsApp وOpenAI، GitHub API لـ12 مستودعاً).
- **المالك اعتمد Q1–Q17** ⇒ D-075–D-082 + مراجعة D-017/D-056/D-057 (LiteLLM وTemporal مرفوضان في V1)؛ تسعير بالجنيه المصري (1,490/3,990/9,900)؛ `ai_cost_log` مصدر وحيد + `BudgetGuard`؛ كتالوج مزودين بتحكم إداري كامل (SPRINT-11.1)؛ Safe Removability؛ `learning_consent`؛ الاحتفاظ؛ حدود واتساب.
- **SPRINT-11 صدر** في `CURRENT_DIRECTIVE.md` (Part A: B2–B6 + Part B: المالية/B1 + Part C: entitlements + CTWA referral + learning_consent + Removability)؛ `SPRINT-11.1` (ProviderCatalog + `/admin/ai/*` + probe) يتبعه قبل L1.

### المحطة 9: 1–2 نوفمبر 2026 — SPRINT-11 مقبول (المالية D-075 + الباقات D-073 + الإزالة الآمنة D-076) وإصدار SPRINT-11.1
- **SPRINT-11** (A: إغلاق B2–B6 — SSRF بـ`getaddrinfo`/`is_global`، robots متدفق، `RevisionPublic`، سباق `reply_template`، سقف `phone_number_id=8` قبل HMAC؛ B: `ai_cost_log` ملك `ai_gateway` + هجرة 0013 + `BudgetGuard.reserve/settle/refund` + fail-closed + سقف المنصة $25 + حذف `UNION ALL`؛ C: `entitlements` + هجرة 0014 + `PlatformAdmin` + `learning_consent` + إحالة CTWA + `REMOVABILITY.md`) رُوجع مستقلاً على PG 17.11 UTF8 وقُبل ودُمج (PR #39 ⇒ `main` @ `8fbbe8c`): 1,331 اختباراً، 98.11%، 66 مساراً محمياً، هجرات 0001–0014.
- مراجعة المخطط أثبتت سبع نتائج غير مانعة بمِجسّ حي (E1–E5, F1, F2 — `sprints/SPRINT-11_REVIEW.md`) ⇒ تُغلق في **SPRINT-11.1 Part A**.
- **SPRINT-11.1 صدر** في `CURRENT_DIRECTIVE.md`: Part A الإغلاقات ⇒ Part B كتالوج المزودين (D-077: `ai_providers`/`ai_models`/`ai_routing` + هجرة 0015 + تسعير رموز/صوت + بدائل مرتّبة + `FEATURE_PROVIDER_CATALOG_ENABLED`) ⇒ Part C `/api/v1/admin/ai/*` (11 مساراً + probe) ⇒ `PROTECTED_ROUTE_COUNT` 77.
- نظافة GitHub: حُذفت فروع `wip/*` السبعة بعد الدمج.

### المحطة 10: 2 نوفمبر 2026 — SPRINT-11.1 مقبول (كتالوج المزودين D-077 + تحكم الإدارة) وإصدار SPRINT-12 بتقسيم G6
- **SPRINT-11.1** (A: إغلاق E1–E5/F1/F2 عبر منافذ يملكها المستهلك `CounterGuard` (D-016) + `--cov-precision=2`؛ B: `ai_providers`/`ai_models`/`ai_routing` + هجرة 0015 + `CatalogPriceTable` + `CatalogGatewayResolver` (fallback ≤ 3) + مفتاح قتل `FEATURE_PROVIDER_CATALOG_ENABLED`؛ C: `/api/v1/admin/ai/*` 11 مساراً + probe + Zero-Secret + `REMOVABILITY.md`) — **مقبول ودُمج `ffb3f5d`** (PR #42؛ 121 تكامل + وحدات بلا DB ≥ 95% على PG 17.11 UTF8؛ `PROTECTED_ROUTE_COUNT` 77؛ المالك قبِل تجاوز صمّام الحجم +32 سطراً).
- مراجعة المخطط أثبتت **ست فرضيات H1–H6 بمِجسّ حي** (`sprints/SPRINT-11.1_REVIEW.md` §3) — لا واحدة مانعة لأنها محصورة خلف الافتراضيين: 🔴 H1 `plan_inactive` ⇒ 500 للـwebhook مع فقدان الرسالة · 🟡 H2 أوراكل حصة قبل التفويض · 🟡 H3 Brand Brain يتجاوز الكتالوج · 🟠 H4 SSRF عبر DNS في `validate_provider_host` · 🟡 H5 `CatalogPriceTable` تتجاهل `enabled` · H6a–d ⇒ تُغلق في **SPRINT-12 Part A**.
- **SPRINT-12 صدر** في `CURRENT_DIRECTIVE.md` بعد **بوابة G6**: خارطة POST_M7 (≈ 3,600–4,200 سطر) قُسمت إلى **12** خلفية (A إغلاقات ⇒ B حلقة الليد + `pulse` SQL + أسقف D-082 + هجرة 0016 ⇒ C `/d/{token}` + Batch Approval) و**12.1** واجهة L1 الست + Umami + Playbooks. قرار `[DECISION]` H1: webhook موقَّع ⇒ 200 دائماً؛ الصادر ⇒ 402 `entitlement_plan_inactive`.
- 🔴 **حظر تشغيلي حتى دمج SPRINT-12 Part A:** لا تفعيل للكتالوج، لا `PLATFORM_ADMIN_USER_IDS`، لا `suspend` لعلامة لها webhook حيّ.
- نظافة GitHub: حُذف `wip/sprint11.1-chain` و`wip/sprint11.1-squash`.

### المحطة 12: 3 نوفمبر 2026 — SPRINT-12.1-A مقبول (S-1 + Batch Approval + أساس واجهة L1) وإصدار SPRINT-12.1-B بتصليب الجلسة أولاً
- **SPRINT-12.1-A** (A: `PUBLIC_STATUSES` في `/d/{token}` + 409 `recommendation_dismissed` + `POST …/content/approvals/batch` ≤ 50 بصفّ `approval_events` لكل عنصر D-055؛ B: `src/ui` — Jinja2 BSD-3 + htmx 2.0.4 0BSD + Pico 2.0.6 MIT مُلزَمة محلياً بـsha256، جلسة cookie `Path=/app`، CSRF، RTL، i18n `ar.json`، شاشات login/brands/today/inbox، `build_in_process_client` في `core/http.py` حفاظاً على D-068، `REMOVABILITY.md` + اختبار عزل) **قُبل ودُمج** (PR #49 ⇒ `main` @ `278de36`). PG 17.11 UTF8: تكامل 142/0؛ CI 1,472/0 · 95.25%؛ 84 مساراً محمياً / 7 عامة / 11 `/app/*`؛ A+B = 885 سطراً ≤ صمّام 1,400.
- مراجعة المخطط (`sprints/SPRINT-12.1-A_REVIEW.md`) بمِجسّ حي **بالمحدِّد الحقيقي**: 🔴 **U1-a** refresh لكل طلب `/app/*` يشارك دلو `10/min/IP` مع login ⇒ الطلب العاشر بعد الدخول يطرد المشغّل ويمسح الكوكي وإعادة الدخول 429 · 🔴 **U1-b** طلبان بنفس الكوكي (استطلاع 30s + نقرة) ⇒ `revoke_all_for_user` ⇒ خروج من كل الأجهزة · 🟡 **U2** `POST /app/logout` بلا فحص CSRF؛ ختم CSRF ثابت على `user_id` · 🟡 **U3** `flash` غائب من جزئية «اليوم»؛ 303 بلا `HX-Redirect` ⇒ صفحة الدخول تُحقَن داخل `#today` · 🟢 **U4** `severity.info` غير مترجَم + شريط الباقة بلا محادثات. **كلها محصورة في `src/ui`** ⇒ دمج + إغلاق إلزامي أول.
- **SPRINT-12.1-B صدر:** **C-0** تصليب بلا هجرة (كوكي `app_access` بالتوكن القصير يُفكّ محلياً؛ refresh عند الانتهاء فقط مع قفل داخل العملية؛ 429 لا يمسح الجلسة؛ nonce CSRF لكل دخول في `app_csrf` ويُفحص على كل POST بما فيه logout؛ `HX-Redirect`/`HX-Retarget`؛ flash في الجزئيات؛ اختبار i18n ⊇ كل enums؛ `Cache-Control` للـvendor) ⇒ **C** شاشات content (batch) / diagnosis (`evidence_grade` + رابط عام) / settings (اتصالات، واتساب، باقة، consent، منح) ⇒ **D** `/app/admin/ai` (D-077: مزودون/نماذج/توجيه/probe بلا سرّ في HTML) + `/app/admin/entitlements` (InstaPay + wa-caps/kill switch) + Umami اختياري + Playbooks (مطاعم/عيادات) · صمّام G6 ≤ 1,600 مرتَّب الأولوية · `PROTECTED_ROUTE_COUNT` ⇒ 85 مع `GET /api/v1/playbooks`.
- 🔴 **قرار تشغيلي:** لا تجربة حيّة لـ`/app`، لا تصوير App Review، لا قياس TTFV قبل دمج C-0.
- قرارات المالك المسجَّلة: `SameSite=Lax` تحت `/app` ✅ · `StaticFiles` بلا SRI لـL1 ✅ · آلية الجلسة/CSRF الحالية **ليست نهائية** ⇒ C-0.

### المحطة 11: 3 نوفمبر 2026 — SPRINT-12 مقبول (إغلاق H1–H6 + حلقة الليد D-079 + Pulse-SQL + أسقف D-082 + الرابط العام) ورفع الحظر التشغيلي وإصدار SPRINT-12.1
- **SPRINT-12** (A: إغلاق H1–H6 — webhook موقَّع ⇒ 200 دائماً + علامة `plan_inactive`؛ الصادر ⇒ 402 `entitlement_plan_inactive`؛ تفويض قبل الحصة؛ Brand Brain عبر الكتالوج؛ SSRF عبر DNS + تثبيت العنوان في `core/netguard.py`؛ `CatalogPriceTable` بمفتاح مزوّد+اسم وتجاهل المعطَّل؛ B: هجرة 0016 (`lead_attributions`, `pulse_alerts`, `wa_number_caps` + 5 أعمدة + 11 قيداً) + `lead-stage` + attribution `measured` من referral + وحدة `pulse` (8 فحوص SQL، idempotent حتى ack، `REMOVABILITY.md` + عزل AST) + أسقف 300 ردّ/رقم/يوم و423 kill switch + أدمن `wa-caps`؛ C-1: `/d/{token}` عام بعلامة مائية، sha256 فقط، 60/دقيقة/IP) — **مقبول ودُمج `b98cffe`** (PR #45؛ وحدات بلا DB 1,421/0 · 95.28%؛ تكامل 140/0 على PG 17.11 UTF8؛ `PROTECTED_ROUTE_COUNT` 83 / عامة 7؛ 12 انحرافاً مقبولة؛ **صمّام الحجم أُطلق بحق** ⇒ Batch Approval ⇒ 12.1).
- مراجعة المخطط (`sprints/SPRINT-12_REVIEW.md`) بمِجسّ حي من 16 فرضية: 14 ✅ · **S-1** ⚠️ التوصيات `dismissed` تظهر في البطاقة العامة (غير مانع — لا PII؛ يُغلق في 12.1 A-1) · **S-2** ℹ️ `ack` ثانٍ ⇒ 200 idempotent (مقبول).
- ✅ **الحظر التشغيلي الثلاثي مرفوع** (كتالوج / مشرف منصة / suspend). تحذير باقٍ: افتراضات `FEATURE_PULSE`/`FEATURE_LEAD_LOOP`=`true`، `PULSE_INTERVAL_SECONDS=900`، سقف 300.
- **SPRINT-12.1 صدر** بتقسيم G6 إلى نصفين: **12.1-أ** (S-1 + Batch Approval + أساس L1: جلسة cookie + CSRF + login/today/inbox بـJinja2 BSD-3 + htmx 0BSD + Pico MIT مُلزَمة محلياً بلا CDN) ⇒ مراجعة ودمج ⇒ **12.1-ب** (content/diagnosis/settings + تبويبا الأدمن + Umami MIT اختياري + Playbooks JSON) ⇒ قياس TTFV ≤ 30 دقيقة.
- ملاحظة بيئة: بوابة الوحدات الواحدة (1,421 اختباراً + coverage branch) تتجاوز ذاكرة ساندبوكس 1 GB ⇒ تُشغَّل على نصفين بـ`--cov-append` (CI غير متأثر).

