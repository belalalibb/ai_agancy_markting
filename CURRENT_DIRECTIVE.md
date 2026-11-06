# CURRENT DIRECTIVE — SPRINT-14-B

> المصدر الوحيد للمهمة الحالية — 2026-11-06. PR #60 مقبول ومدموج في `e51958a8dbbb793bfc93efcaeba358e12e7da7f6`؛ [المراجعة المستقلة](./sprints/SPRINT-14-A_REVIEW.md). العيوب S14A-1..3 **مفتوحة** حتى B-0. المخطط يصدر التوجيه ولا ينفذ كود المنتج؛ المنفذ لا يدمج (D-063).

## بطاقة المهمة

| البند | القرار |
|---|---|
| المهمة | `SPRINT-14-B` — النصف الثاني والأخير للانضمام الذاتي: B-0 ثم B-1..B-6؛ PR واحد |
| الحالة | ISSUED؛ لا يبدأ التنفيذ إلا بإطلاق المالك بعد دمج التوجيه وتسجيل نقطة البداية |
| البداية | **`adaf10611e3abd274581ee815243c4d998fa8d99`** — SHA دمج PR [#61](https://github.com/belalalibb/ai_agancy_markting/pull/61)، مسجل في `WORK_STATE.md` §6. هذا خط أساس التوجيه؛ مزامنة origin/main تجلب نقطة التحقق اللاحقة أيضاً. ليس SHA دمج #60 ولا SHA نقطة التحقق |
| الفرع | `work/implementation`؛ شجرة نظيفة ثم `git fetch origin && git merge --ff-only origin/main`. إن تعذّر FF توقف وأبلغ؛ لا reset مدمراً |
| التسليم | B-0 كوميت مستقل يُدفع أولاً مع اختباراته؛ ثم B-1..B-5 ثم B-6؛ تقرير المنفذ §9؛ PR واحد إلى main |
| الحجم | ≤900 سطر مضاف في Python الإنتاج تحت `src/` + HTML مجتمعة؛ الاختبارات وJSON خارج العد. هجرة0018 وحدها ≤90 سطراً إضافياً. لا حذف ميزات لتصغير العد؛ اطلب قراراً قبل تجاوز السقف |
| الهجرة | 0018 فقط، توسيع قيد قطاع brand_profiles؛ لا تعديل 0001..0017 |
| العدادات | API PROTECTED=98 والعامة دون تغيير؛ UI الصحيح 37 +9 =46، وليس38 |
| البوابات | CI الست خضراء، ruff check/format، وحدات بتغطية ≥95.00% بدقة2، تكامل PG17، هجرة ذهاب/إياب واختبارات المتصفح |

## 0. الحدود المقفلة

1. D-054: لا تبعية جديدة. ممنوع `auth/*`, `contract.py`, `public_fetch.py`, `pyproject.toml`, `internal_metrics.py` والهجرات0001..0017. لا ErrorCategory جديد.
2. D-068: الواجهة عبر `ApiClient` فقط؛ لا SQL/ORM/HTTP مباشر، ولا استيراد أعمال جديد. استيراد `auth.jwt` الموروث استثناء سابق في اختبار العزل؛ لا توسّعه.
3. خارج `src/ui/**` الإنتاج المسموح حصراً: `src/modules/lifecycle/service.py` حرفية B-0.4 فقط؛ `src/modules/brand_brain/domain/models.py` لتوسيع Sector؛ `src/modules/brand_brain/playbooks.py` للغة والقطاع؛ JSON الأدلة؛ هجرة0018 جديدة. الاختبارات ووثائق التسليم مسموحة. لا router/service خلفي آخر ولا API جديد.
4. D-055: إنشاء draft لا يعتمد/يجدول/ينشر تلقائياً؛ إنشاء ⇒ إرسال للموافقة ⇒ اعتماد OWNER ⇒ جدولة، أفعال بشرية منفصلة. لا تعديل آلة الحالات/الحصص/D-070.
5. لا أسرار في الملفات أو التقرير أو HTML. حقول WA تبقى `wa_*`، password بلا value؛ لا echo لمدخلات ValidationError أو تفاصيل المزود. لا GitHub token في المستودع.
6. CSRF على POST المحمي بجلسة و403 قبل الأثر؛ تفويض وعزل المستأجر upstream. كل commit يدفع فوراً؛ احذف `_probe_*.py` قبل commit. المنفذ لا يدمج.

## 1. B-0 — الإغلاقات الإلزامية أولاً

### B-0.1 حفظ Meta callback عبر تسجيل الدخول (S14A-1)
- `src/ui/session.py::login_target`: استخدم `quote(next_path, safe="/")` مرة واحدة؛ أبقِ حارس prefix `/app/` ومنع الرابط الخارجي و`//host`. لا تغيير verifier أو auth.
- GET login يفك query مرة واحدة عبر FastAPI؛ hidden next يعرض عبر autoescape؛ POST يحافظ على code وstate كاملين، دون ترميز مزدوج.
- اختبار **السلسلة الكاملة**: بدء OAuth من حساب صحيح ⇒ إزالة الكوكيز ⇒ bridge ⇒ اتباع GET login ⇒ استخراج hidden next ⇒ POST login ⇒ اتباع303 bridge ⇒ اختيار الصفحات؛ لا missing_parameters. اختبر roundtrip لمعاملات فيها `&`, `+`, `%` بترميز صحيح. state مفقود/تالف ⇒ رفض بلا Graph، وnext خارجي مرفوض. اختبار Location وحده غير كافٍ. إن احتاج اختبار العزل اسم urllib اسمح stdlib فقط دون تخفيف الحظر.

### B-0.2 دورة WhatsApp (S14A-2)
- أضف `POST /app/b/{brand_id}/settings/whatsapp/disable` ⇒ API `DELETE /brands/{id}/whatsapp/account` (204) ⇒303 برسالة عربية؛ OWNER وCSRF وتأكيد صريح مع شرح أنه تعطيل وليس حذف المحادثات.
- active يعرض rotate/disable **فقط**؛ disabled/absent يعرض connect/reconnect ولا rotate. لا نموذج تحديث active يستدعي connect، ولا اختراع PUT account.
- API POST connect يقبل أول ربط201 وإعادة ربط disabled200 **بنفس account id**؛ active409 يظل رسالة صحيحة عند السباق.
- `_onboarding`: WA يحتسب قناة فقط إذا status=active؛ أو صفحة Meta مختارة في اتصال active. وجود object معطل لا يكفي.
- اختبار متصفح connect⇒rotate⇒disable⇒disabled⇒reconnect⇒active بنفسID، شكل النماذج، غياب الأسرار عند النجاح/الفشل، غير OWNER وCSRF مفقود بلا أثر.

### B-0.3 ترجمة ضعف كلمة المرور (S14A-3)
- `_auth_code` يصنف قائمة422 تصنيفاً ضيقاً: loc ينتهي password + string_too_short ⇒ password_too_weak؛ خطأ نموذج value_error برسالته الثابتة `password must not equal the email address` ⇒ نفس الكود، دون عرض الرسالة أو input.
- اختبر مع `RegisterRequest` الحقيقي: قصيرة/مطابقة للبريد ⇒422 ورسالة `error.password_too_weak` وليس http_422؛ بريد غير صالح أو slug غير صالح ليس ضعف كلمة مرور. الأطول من الحد fallback عربي آمن. لا نسخ قواعد auth إلى UI ولا تعديلها.

### B-0.4 حارس التصدير
- في `lifecycle/service.py` بدّل فقط `"gAAAA"` داخل FORBIDDEN_FRAGMENTS إلى `"gaaaa"` لأن المقارنة على k.lower(). لا تغيير allowlist أو فحص قيم نصوص العملاء.
- اختبار build_zip: مفتاح عمود مختلط الأحرف ببادئة Fernet يرفض بـExportFailedError؛ نص عميل يحوي هذه الكلمات في body يصدّر بنجاح. هذا وصف حرفيات لا أسرار حقيقية.

## 2. B-1 — ملف العلامة

- `GET/POST /app/b/{brand_id}/brain`، قالب brain.html ورابط nav.brain في التنقل والإعدادات. GET profile عبر API؛ POST ⇒ PUT نفسه.
- **If-Match من hidden version الذي رآه المستخدم في GET**؛ ممنوع جلب النسخة الأخيرة عند POST ثم الكتابة فوق تعديل غيره.
- حقول عربية طبيعية، لا raw JSON مطلوباً من العميل:
  - products≤20: name/description/price_hint/url؛ حافظ على كل الصفوف، وأتح الإضافة حتى السقف (صفوف فارغة أو JS محلي صغير بلا مكتبة).
  - audience قوائم سطر/عنصر: segments≤10، regions≤10، languages≤5، age_bands≤8.
  - tone: voice، formality casual|neutral|formal، emoji_policy none|light|free، banned_phrases≤50.
  - goals≤5: kind awareness|leads|sales|retention، target، horizon_days1..365.
  - sector restaurants|clinics|beauty_salons أو غير محدد⇒null؛ حدود طول الحقول من schemas القائمة.
- جسم PUT فقط products/audience/tone/goals/sector؛ **لا tone.reply_templates** (مرفوضة في ToneInput، ويحافظ عليها API).
- نجاح303؛ 412 profile_version_conflict يعيد مدخلات المستخدم ورسالة تعارض ورابط إعادة تحميل؛ لا blind retry ولا استبدال version بآخر ثم إعادة الكتابة. نسخة مفقودة رسالة428 آمنة. VIEWER قراءة فقط بلا banned_phrases أو نماذج كتابة؛ غير العضو404؛ اختبارات CSRF/أدوار/تعارض حقيقي وعزل المستأجر.

## 3. B-2 — المنافسون والأدلة

- في brain عرض `GET /brands/{id}/brain/competitors` بشكل `{items,limit:3}`؛ إضافة اسم/نوع وروابط عامة وقائمة allowed_hosts صريحة (حدود schema، المضيفون≤3)؛ حد المنافسين3 upstream لا وعد محلي مستقل.
- العمليات UI POST التالية تستهلك API المقابل دون backend جديد:
  - `brain/competitors` ⇒ POST `/brands/{id}/brain/competitors`.
  - `brain/competitors/{cid}/delete` ⇒ DELETE `/brands/{id}/brain/competitors/{cid}`.
  - `brain/competitors/{cid}/refresh` ⇒ POST `/brands/{id}/brain/competitors/{cid}/refresh`.
  - `brain/evidence/refresh` ⇒ POST `/brands/{id}/brain/evidence/refresh`.
- **تحديث المنافس الفردي يعني تحديث أدلته**؛ لا API PUT/PATCH منافس موجود؛ لا تخترع endpoint أو delete/recreate ضمنياً.
- GET evidence عبر العقد القائم؛ وسم measured/inferred/estimated والثقة، لا وصف تقدير بأنه قياس. D-074 وpublic_fetch والـrobots/SSRF تبقى upstream.
- أخطاء409/422/429 بالعربية، الحفاظ على Retry-After؛ اختبار حد3 والرابع مرفوض، حذف/refresh مفرد وعام، CSRF وأدوار وعزل المستأجر وفشل المصدر القانوني دون HTTP مباشر.

## 4. B-3 — خطة محتوى مسودات

- زر مستقل في diagnosis: `POST /app/b/{id}/diagnosis/content-plan` ⇒ API POST `/brands/{id}/brain/content-plan` **دون body إلزامي**؛ الموافقة على التعلم/الحصة/الذكاء upstream.
- شكل النجاح الحقيقي `{items:[{recommendation_id,item_id,channel,title,status:"draft"}],items_dropped,prompt_version}`. اعرض عدد المنشآت/المستبعدات وروابط المسودات أو303 برسالة واضحة إلى content؛ لا تفترض count أو recommendations بدلاً من items.
- لا اعتماد أو جدولة أو publish jobs تلقائياً.503 AI غير مهيأ و429/402/502 بالعربية دون trace أو مفاتيح؛ CSRF/VIEWER/غير عضو بلا أثر. اختبار PG يثبت draft فقط وصفر jobs وأحداث اعتماد.

## 5. B-4 — المسودة ثم الاعتماد والجدولة

- نموذج في content + `POST /app/b/{id}/content/new` ⇒ API POST `/brands/{id}/content/items` مع `{channel,body,target_page_id,media_refs:[]}`؛ **لا scheduled_for في الإنشاء**. النجاح201 ثم303 إلى draft برسالة؛ نص المستخدم محفوظ عند خطأ آمن.
- `GET /channels` يعيد `{channels:[…]}`؛ اختر من القنوات المتاحة، لا تعد بنشر LinkedIn/X/TikTok غير المهيأة.
- `<select>` الصفحات من connections: اتصال active وصفحة is_selected فقط؛ value=page_id، ليس connection_id ولا instagram_user_id. IG فقط للصفحات ذات ارتباطInstagram، مع image_url اختياري HTTPS يحوّل إلى media_refs من النوع image حسب schema. لا upload أو مكتبة جديدة.
- إنشاء draft بلا Page مسموح مع تحذير ورابط connect؛ الجدولة غير مسموحة قبل target صالح. لا تستبدل موافقة OWNER ضمن الإنشاء.
- عدّل نموذج schedule الموجود من Page نص حر إلى dropdown نفسه؛ الفعل الحالي `/content/{item_id}/schedule` ⇒ API schedule `{scheduled_for:"…+00:00",target_page_id:"…"}`.
- datetime-local موسوم صراحة **UTC**؛ حوّله إلى aware ISO+00:00. إدخال غير صالح400 عربي، مبكر عن now+5min⇒422 upstream؛ لا naive datetime يؤدي500. لا تخمين المنطقة الزمنية.
- الأفعال منفصلة: create draft ⇒ submit pending_approval ⇒ approve OWNER ⇒ schedule scheduled. اختبار المتصفح الكامل بالكوكيز/CSRF، ثم SQL يثبت job queued وattempts0؛ لا provider publish. اختبر أدوار VIEWER/EDITOR، صفحة غير مختارة/غير نشطة/علامة أخرى، IG بلا صورة، عزل المستأجر.
- اكتمال draft يجعل data-step draft=true ويخفي الشريط عند اكتمال الخطوات؛ redirect home لعلامة واحدة إلى today مقبول إذا تحقق الاختبار من أثر الاكتمال.

## 6. B-5 — نتائج Batch والترجمة

- `_translate_batch` موجود: وسّع اختباره ولا تعاود بناء ما يعمل. الرد `{results:[{item_id,status:"approved"|"skipped",error?}]}`؛ كل صف معلوم الهوية ومترجم، لا إخفاء skipped أو طباعة dict/raw code.
- اختبار نتائج مختلطة approved/skipped لكل من انتقال غير صالح/حصة/عنصر غير مسموح، والأدوار والعزل؛ تبقى D-055 أحداث كل عنصر upstream.
- اختبار AST دون import تنفيذي: اجمع literal Assign/AnnAssign باسم error أو code من **كل** `src/modules/**/exceptions.py`؛ لا تستثنِ base classes لتصغير الجرد. خط الأساس أثناء المراجعة90 كوداً،26 بلا ترجمة؛ أعد القياس وسجّل الفروق.
- تحقق أن `src/ui/i18n/ar.json` يغطي `error.<code>` **بالمفرد**، مع أخطاء public/AI التي تستهلكها الواجهة فعلاً. لا errors.*؛ لا ابتلاع enum أو كود غائب بصمت.

## 7. B-6 — Playbooks عربية وbeauty_salons وهجرة0018

- احفظ المطاعم والعيادات الإنجليزيتين الحاليتين كـrestaurants_en.json وclinics_en.json **قبل الترجمة**؛ لا تغير النسخة المرجعية الإنجليزية إلا إضافة lang=en إن لزم التحقق. الملفات النشطة restaurants.json/clinics.json عربية.
- Playbook يستخدم extra=forbid ولا lang حالياً: أضف `lang: Literal["ar","en"] = "ar"`؛ النشطة الثلاث lang=ar. لا glob يحمل *_en افتراضياً؛ loader يتبع SECTORS فقط. تبقى حدود التحقق وفشل الإقلاع على فساد الملف.
- ترجم النصوص البشرية title/content_pillars/reply_templates/calendar.label/tips؛ **لا تترجم machine IDs**: sector، tone enum، metrics keys أو مفاتيح JSON. لا وعود مبيعات أو أرقام أداء مختلقة.
- أضف beauty_salons.json بالعربية بالشكل نفسه وحدوده، ووسّع domain Sector وplaybooks.SECTORS وUI SECTORS/ترجمة القطاع والقوائم (بما فيها أول علامة). لا قطاع رابع ولا API جديد.
- 0018: down_revision=0017؛ وسّع ck_brand_profiles_sector إلى restaurants/clinics/beauty_salons/NULL. downgrade يحول beauty_salons إلى NULL **قبل** إعادة القيد القديم. وثّق فقد التصنيف فقط في downgrade؛ ممنوع حذف profile/brand/history.
- اختبار PG فعلي upgrade0018⇒حفظ beauty⇒downgrade0017 (السجل باق والقطاعNULL)⇒upgrade0018؛ قطاع غير معروف يرفض. تحقق من preservation للبيانات الأخرى والقيد، وبقاء رأس واحد.
- اختبارات الكتب3 langar، نسخ en موجودة، نصوص عربية مرئية، IDs/حدود صحيحة، startup يفشل عند فساد. لا network أو توليد AI للمعرفة المرجعية.

## 8. الجرد والبوابات

| UI جديد | العدد |
|---|---|
| WhatsApp disable | 1 |
| brain GET/POST | 2 |
| competitors add/delete/refresh + evidence refresh | 4 |
| diagnosis/content-plan | 1 |
| content/new | 1 |
| الإجمالي | **9 ⇒ UI46** |

ضع المسارات الثابتة قبل catch-all؛ عمليات approve/submit/schedule موجودة لا تُعد جديدة. API98/PUBLIC ثابتان؛ عدّل inventories UI لا حارسAPI لتسكين اختبار فاشل.

الأوامر (احتفظ برمز الخروج الكامل؛ لا pipe tail يخفي الفشل):
```bash
git diff --check
python -m ruff check .
python -m ruff format --check .
python scripts/verify_hub.py
python -m pytest tests --ignore=tests/integration --cov=src --cov-report=term --cov-fail-under=95 --cov-precision=2 -p no:cacheprovider
TEST_DATABASE_URL="$TEST_DATABASE_URL" python -m pytest tests/integration -q -p no:cacheprovider
# URL لقاعدة اختبار PostgreSQL17 UTF8 فقط؛ لا SQLite ولا قاعدة إنتاج.
python -m alembic -x database_url="$TEST_DATABASE_URL" upgrade head
python -m alembic -x database_url="$TEST_DATABASE_URL" downgrade 0017
python -m alembic -x database_url="$TEST_DATABASE_URL" upgrade head
# اختبر دورة الهجرة ببيانات beauty كما في B-6، لا مخطط فارغ فقط.
git diff --numstat "$START_SHA"..HEAD -- 'src/*.py' 'src/**/*.py' 'src/**/*.html'
git diff --stat "$START_SHA"..HEAD -- src/modules/auth src/core/contract.py src/core/public_fetch.py src/modules/brand_brain/internal_metrics.py pyproject.toml
find tests -name '_probe_*.py' -print
```

جمع numstat الإنتاج py+html≤900؛ الهجرة≤90 منفصلة. افحص كل تغييرات خارج ui مقابل قائمة §0، والهجرات القديمة0 diff. grep القوالب `gAAAA|system_token|api_key|app_secret|verify_token`=0؛ افحص HTML الفعلي بقيم سرية صناعية لا أسماء فقط. اختبارات العزل/D-068 والـ98 محمياً وCSRF كلها خضراء.

إذا نفدت الذاكرة: عمليات لكل ملف/دالة، استكمل كل collected IDs وسجل exit codes؛ التغطية إما قياس محلي سليم قابل للجمع أو دليل CI واضح النسبة، لا تنسب CI إلى المحلي. لا تخفّض اختبارات/حدود التغطية. CI: lint/unit/integration/secrets/licenses/restore-drill جميعها نجاح على HEAD المراجع.

اختبار PG عبر المتصفح register⇒brand عربي⇒profile(version)⇒competitor⇒evidence⇒plan drafts⇒create⇒submit⇒approve⇒schedule. Graph/AI شبكات وهمية فقط، الخدمات والمستودعات PG حقيقية؛ لا API مباشر داخل رحلة المتصفح. تحقق SQL لاحق، بلا نشر حي. لا تزعم TTFV حقيقياً من fixture.

## 9. تقرير المنفذ عند التسليم

أضف تحت هذا العنوان ولا تمحُ التوجيه:
1. start SHA الكامل، code/docs SHAs، رابطPR وCI؛ ترتيب B-0 أولاً وما يثبته.
2. جدول B-0..B-6: الملفات، الاختبارات، الحالة، الانحرافات وأسبابها دون ادعاء إغلاق غير مختبر.
3. أوامر البوابات وexit codes وأعداد الوحدات/التكامل ونسخةPG والترميز؛ التغطية المحلية مميزة عنCI، وحساب كل collected IDs إذا قُسمت.
4. numstat بالامتداد، مجموع py+html والهجرة منفصلة؛ protected0 وUI46/API98/PUBLIC ثابت؛ greps والعزل وHTML-secret scan.
5. أدلة callback بعد login كاملاً، WA دورة بنفسID، If-Match تعارض412 من النموذج، نتائجbatch، حفظbeauty ودورةdowngrade، رحلةcontent/job attempts0.
6. أسئلة قرارات حقيقية فقط؛ لا تغيّر upstream أو تتجاوز السقف بصمت. حدّث WORK_STATE دون حذف التاريخ.

## 10. المالك وحد التوقف

- توكن GitHub مكشوف: إلغاؤه/تدويره فوراً؛ الاعتمادات في البيئة أو remote فقط، لا تكررها في الوثائق. لا يحتاج التقرير قيم Meta/WABA حقيقية.
- التشغيل الحالي `--workers 1`؛ PLATFORM_ADMIN_USER_IDS للمالك؛ RETENTION_DRY_RUN=true ونسخ احتياطي. Redirect URI `/app/social/meta/callback` في Meta والبيئة.
- TTFV≤30 دقيقة وتجربة3علامات وفيديوAppReview وربطWABA الحقيقي مسؤولية المالك، ليست مثبتة بالمسبار. حواف login/WA تبقى غير مكتملة حتى قبولB-0.
- نموذج علامة ثانية مؤجل: `TenancyService.create_brand` لا يفرض max_brands حالياً رغم وجوده في limits؛ يلزمه توجيه خلفي مستقل. لا حصصUI ولا توسيع UX يوهم بفرضها، وليس مانعاً جديداً لـ14-A.
- **المخطط يتوقف بعد دمج التوجيه ونقطة start SHA بانتظار إطلاق المالك؛ لا ينفذ14-B. المنفذ يتوقف بعد PR وCI والتقرير؛ المخطط وحده يراجع حياً ويدمج.**
