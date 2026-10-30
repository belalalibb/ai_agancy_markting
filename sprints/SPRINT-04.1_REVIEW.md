# 📋 SPRINT-04.1-HOTFIX — تقرير مراجعة المخطط

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-04.1-HOTFIX` (8 ملاحظات رقابية مُتحقَّق منها: H1–H8) |
| **المُراجِع** | AI Master Planner |
| **تاريخ المراجعة** | 2026-10-30 |
| **الفرع المُراجَع** | `work/implementation` @ `1737d6b` (Part A `51ac0fe`، Part B `1b7a046`، docs `1737d6b`) فوق `main` @ `20da426` |
| **Pull Request** | [#13](https://github.com/belalalibb/ai_agancy_markting/pull/13) — 32 ملفاً، +1881/−276 |
| **الحكم النهائي** | 🟢 **مقبول.** الثمانية مُغلقة فعلياً لا ادعاءً؛ لا ميزة ولا تغيير مخطط بيانات ولا ملف محظور مُسّ. سؤالا المنفذ محسومان أدناه (§3). ملاحظة بروتوكولية واحدة من المالك ثبتت بنيوياً وتُغلق في `SPRINT-05` الجزء A (§4). |
| **حالة الدمج** | PR #13 **دُمج بيد المالك** (`merged_by: belalalibb`, 2026-10-30T12:19Z ⇒ `main` @ `32c20cb`) قبل خطوة دمج المخطط. القبول هنا **لاحق للدمج** ومبني على تحقق مستقل أُعيد **مرتين** في بيئتين طازجتين (الثانية على `main` @ `32c20cb` نفسه: 474 ناجحاً / 99.57%، تجربة التسريب `False` لكل الأسرار الخمسة، سباق H7-I1 3/3). لو كشفت المراجعة عيباً لكان المسار: توجيه تصحيح فوري على `main`. تذكير D-063: المالك لا يدمج — المخطط يدمج بعد المراجعة. |

---

## 1. ما تحقق منه المخطط فعلياً (بيئة طازجة بعد إعادة ضبط الـsandbox؛ PostgreSQL 17 + pgvector أُعيد تثبيتهما)

| الفحص | نتيجة المخطط | ادعاء المنفذ | تطابق |
|---|---|---|---|
| **CI على `1737d6b`** (Checks API) | الفحوص الخمسة `success` | ✓ | ✅ |
| `ruff check` + `ruff format --check` (91 ملفاً) | نظيفان | ✓ | ✅ |
| الحزمة كاملة على PG حقيقية + تغطية | **474 passed، 0 فشل، 99.57%** (2238 عبارة، 3 غير مغطاة) في 88.8 ث | 447 + 27 = 474 / 99.57% | ✅ |
| **تجربة تسريب `httpx` (أُعيدت)** — مسجّل جذر على `DEBUG` + تشغيل `exchange_for_long_lived_token` / `get_user_pages` / `debug_token` تحت `respx` | `client_secret in log: False`؛ ولا واحد من `SHORT_TOKEN_ABC` / `USER_TOKEN_LEAK` / `DEBUG_TOKEN_LEAK` / `EAAlong` ظهر في السجل (سطر واحد فقط: `Using selector: EpollSelector`). الطلبات الفعلية: `POST /oauth/access_token` بجسم form يحمل `client_secret`؛ `GET /me/accounts?appsecret_proof=…` + `Authorization: Bearer`؛ `POST /debug_token` بجسم `input_token=…` + Bearer للـapp token. **لا سر في أي رابط.** | ✓ | ✅ (كانت `True` عند `0db7828`) |
| **سباق H7-I1 (أُعيد 5 مرات متتالية)** `tests/integration/…::test_h7_i1_concurrent_refresh_with_same_cookie_exactly_one_wins` | 5/5: `codes=[200, 401]`، `live_sessions_after=0`، الخاسر يعيد `refresh_reuse_detected`. الاختبار حقيقي: تطبيقان مستقلان (`create_app` ×2 ⇒ محركان) + `asyncio.gather` كحاجز. | 4/4 نفس النتيجة | ✅ |
| الملفات المحظورة (`ARCHITECTURE_LOCK`, `SESSION_RECOVERY`, `README`, `AI_Marketing_Platform_Hub/**`, `sprints/**`) | لم تُمس | ✓ | ✅ |
| تبعيات جديدة | لا شيء | ✓ | ✅ |

### 1-أ. التحقق بنداً بنداً (بالسطر @ `1737d6b`)

| # | البند | ما قرأه المخطط | الحكم |
|---|---|---|---|
| **H1** | عزل الوكالة: الصفحات غير المختارة لمالك الاتصال فقط؛ `is_connection_owner`؛ 403 على اختيار/فصل غير المالك | `schemas.py:233-248` `from_domain(cls, account, *, viewer_tenant_id)` يرشّح الصفحات؛ `router.py:236,250,271` يمرّر `viewer_tenant_id=tenant_id`؛ `service.py:227` و`:285` يرفعان `NotConnectionOwnerError` (قبل أي نداء Graph)؛ `service.py:169-177` يسجّل نقل الملكية | ✅ |
| **H2** | لا سر في query params؛ Bearer أو جسم POST؛ تنقيح السجلات | `meta_client.py:57` `_URL_SECRET_PARAMS`؛ `:146-148`, `:166-169` `data=` يحمل `client_secret`؛ `:185-190` Bearer + جسم؛ `:265` فحص `https` + المضيف لـ`paging.next`؛ `src/core/logging.py` `SecretRedactingFilter` على الجذر + `httpx`/`httpcore` على `WARNING`؛ لا `f"...{exc}"` يحمل توكناً | ✅ (مؤكَّد تجريبياً أعلاه) |
| **H3** | تعليم `reauth_required` خارج معاملة الطلب | `service.py:295-296` `self._status_marker.mark_out_of_band(...)`؛ `infrastructure/status_marker.py` `PostgresStatusMarker` بمصنع جلسات مستقل؛ الـrouter يحقنه دائماً | ✅ |
| **H4** | سباقات الفهارس الفريدة ⇒ 409 لا 500 | `postgres_repository.py:56` `on_conflict_do_update`؛ اختبارات `test_h4_i1_concurrent_inserts_with_same_slug_…` و`…_concurrent_register_same_email_…` على PG حقيقية | ✅ |
| **H5** | `__Secure-refresh` بدل `__Host-` مع `Path=/api/v1/auth` | `config.py:151-158`؛ `grep -rn "__Host-" src` فارغ | ✅ |
| **H6** | `CurrentUserFresh` يفحص `revoked_at` + `expires_at` + `users.is_active` باستعلام واحد؛ `/auth/me` عليه | `dependencies.py:142,147`؛ `postgres_repository.py:94-103` (`JOIN users`, `expires_at > now`, `is_active IS TRUE`)؛ `auth/router.py:305` | ✅ |
| **H7** | إبطال أولاً مع `rowcount == 1` وإلا إعادة استخدام؛ ربط `replaced_by` | `link_replacement` في الـProtocol + PG + Fake؛ `revoke()` تعيد `int`؛ الاختبارات `test_h7_u1_…` و`test_h7_refresh_revokes_before_issuing_…` + H7-I1 | ✅ |
| **H8** | محدِّد المعدّل محدود الذاكرة | `auth/infrastructure/rate_limit.py:47-52` `max_keys` + `OrderedDict` بطرد الأقدم؛ مسح كل 256 عملية | ✅ |

---

## 2. تقييم تقرير المنفذ (§6 @ `1737d6b`)

- التقرير **دقيق ومطابق** للكود في كل ما أمكن التحقق منه (14 انحرافاً مُعلَناً بالسطر والسبب — كلها مقبولة وبعضها تحسينات صحيحة لم يطلبها النص: تنقية `paging.next` من `access_token` الذي يعيده Meta، تركيب مرشّح التنقيح على الجذر وعلى handlers وعلى `httpx`/`httpcore` معاً).
- الانحراف 14 (رخصة `soxr/LGPL` من `librosa` المثبّتة في صورة الـsandbox لا في `pyproject.toml`): صحيح، CI تعمل في venv نظيف ومرّت بوابة `licenses`. لا إجراء.
- الانحراف 11 (`/auth/me` لمستخدم معطَّل ⇒ 401 `session_revoked` بدل 403): **مقبول** — الرفض عند بوابة الهوية أصحّ من الرفض داخل الخدمة.

---

## 3. حسم سؤالَي المنفذ

### 3-أ. H7-I1 — «0 جلسات حية» بعد سباق refresh بكوكي واحد

**القرار: يُعتمد السلوك الصارم كما هو (0 جلسات) ويُدمج الآن.** المبررات:
1. توكن تحديث قُدِّم مرتين لا يمكن للخادم تمييز «تبويبان شرعيان» عن «سارق + صاحب»؛ الافتراض الآمن هو الإبطال الشامل (نفس منطق A6 في SPRINT-04).
2. النتيجة حتمية على PG (قفل الصف يُسلسل الخاسر خلف التزام الفائز) — لا سلوك عشوائي.
3. لا واجهة أمامية بعد؛ المشكلة العملية (تبويبان يحدّثان في نفس اللحظة) لا تظهر إلا مع متصفح حقيقي. **تُسجَّل كبند مؤجَّل #8 في `DEFERRED_DECISIONS.md`** (نافذة تسامح قصيرة ≤ 10 ث يُعاد فيها إصدار نفس الزوج للخاسر بدل `revoke_all`) وتُفتح مع سبرنت الواجهة الأمامية لا قبله.

### 3-ب. H1-3 — هل يُمسح اختيار الصفحات عند انتقال `connected_by_tenant_id` إلى مستأجر آخر؟

**القرار: لا يُمسح — يُحفَظ الاختيار (السلوك الحالي).** المبررات:
1. `find_by_identity(brand_id, provider, me.id)` يعني أن **هوية Meta نفسها** (نفس `provider_user_id`) أعادت OAuth من مستأجر آخر مخوَّل على نفس العلامة (OWNER/MANAGER بمنحة قائمة). الصفحات هي نفسها فعلياً؛ الاختيار كان قرار العلامة لا قرار المستأجر.
2. مسح الاختيار سيوقف مزامنة `SPRINT-05` فجأة عند إعادة ربط مشروعة (انتهاء توكن 60 يوماً) — انحدار تشغيلي أسوأ من المخاطرة النظرية.
3. الحماية المطلوبة قائمة: نقل الملكية **مُسجَّل** (`service.py:171`)، والمستأجر السابق يفقد رؤية غير المختارة فوراً (H1).
**شرط واحد يُضاف في SPRINT-05:** إن اختلف `me.id` (هوية Meta مختلفة لنفس العلامة) فهذا اتصال **جديد** لا نقل ملكية؛ لا يرث أي اختيار — وهذا ما يفعله الكود الآن ضمنياً (لا `existing`)، ويُثبَّت باختبار صريح.

---

## 4. ملاحظة المالك البروتوكولية — `POST /debug_token` بلا `"method": "GET"`

**ثبتت بنيوياً.** `meta_client.py:185-190` يرسل `POST /debug_token` بجسم `{"input_token": …}` + Bearer للـapp token. `/debug_token` نقطة **قراءة** في Graph؛ الـPOST عليها يحتاج تجاوز الطريقة `method=GET` في الجسم (الآلية الرسمية لـGraph لإرسال طلبات القراءة بجسم POST) وإلا أعاد الخادم الحقيقي `(#100) Unsupported post request`. والأخطر أن `service.py:_inspect_token` (`:302-320`) يلتقط أي `MetaGraphError` غير 190 ويسجّل `WARNING` ثم يكمل بـ`expires_at=None` و`scopes=[]` — أي أن الخلل **يُبتلع بصمت** وكل اتصال يُحفظ بلا انتهاء معروف ولا نطاقات.

- لماذا مرّت الاختبارات؟ `respx` يطابق المسار والطريقة فقط ولا يحاكي قاعدة Graph هذه. (لا يمكن للمخطط تأكيدها ضد `graph.facebook.com` الحقيقي بلا بيانات تطبيق — لذلك تُعامَل كحقيقة بروتوكولية موثَّقة وتُصلَّح احترازياً.)
- **الإصلاح (SPRINT-05 الجزء A-0):** إبقاء POST (كي لا يعود `input_token` إلى الرابط — H2) مع إضافة `"method": "GET"` إلى الجسم؛ اختبار عقد يُثبت الجسم؛ و`_inspect_token` يرفع مستوى السجل إلى `ERROR` لأخطاء الكود 100 ويعدّ عدّاداً كي لا يُبتلع خلل تكوين مرة أخرى.

---

## 5. القرار

🟢 **PR #13 مقبول** (مدموج سلفاً بيد المالك — انظر «حالة الدمج» أعلاه). تحديثات المخطط التابعة (في PR المخطط التالي — هذا الـPR):
- `SECURITY_BASELINE.md` §2: هوية الفاعل ✅، أسرار Graph في Bearer/جسم ✅، عزل الوكالة (H1) ✅، بادئة الكوكي ✅، محدِّد المعدّل داخل العملية ⚠️ (Redis مؤجَّل).
- `KNOWN_ISSUES.md`: درسان من فجوات التوجيه (رؤية الصفحات غير المختارة؛ الأسرار في query params) + فخّ `__Host-` + فخّ `rollback` لعلامة الحالة + فخّ `respx` لا يحاكي قواعد Graph.
- `DEFERRED_DECISIONS.md`: #7 Redis للمحدِّد، #8 نافذة تسامح التبويبين.
- `DATA_AND_INTEGRATIONS.md`: 0004 منجز.
- `CURRENT_DIRECTIVE.md` ← `SPRINT-05` (وظائف السوشيال ميديا التشغيلية).

---

## 6. ملاحظات للمنفذ (لا تؤثر على القبول)

1. `service.py:300` (`_inspect_token` فرع غير مغطى) و`tenancy/service.py:62` — الفرعان غير المغطيان الوحيدان؛ يُغطَّيان ضمن A-0.
2. صياغة التسليم المطلوبة من الآن: «PR #N جاهز لمراجعة المخطط» — المنفذ لا يدمج (D-063). التزم بها المنفذ هذه المرة ✅.
3. تذكير التوكن: المنفذ أورد التذكير الرابع في §6؛ المخطط يكرره للمالك (§7 من التوجيه الجديد).
