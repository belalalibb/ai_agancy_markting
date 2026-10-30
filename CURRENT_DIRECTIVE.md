# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-04.1-HOTFIX رُوجع مستقلاً و**قُبل** (`main` @ `32c20cb`؛ التقرير في [`sprints/SPRINT-04.1_REVIEW.md`](./sprints/SPRINT-04.1_REVIEW.md)). هذا التوجيه يفتح **أول قدرة تشغيلية** فوق الربط: قراءة منشورات الصفحات ووسائط Instagram، وإعادة مزامنة الاتصال وصحة التوكن — **بلا تغيير مخطط بيانات**، وبقاعدتين لا تنازل عنهما استُخلصتا من 04.1: **قناة الأسرار** و**جدول الرؤية لكل حقل** (§2-0).

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-05-META-OPERATIONAL-READS` |
| **النوع** | Part A: تصحيح بروتوكولي إلزامي (A-0, A-1) · Part B: ميزة (قراءة تشغيلية + إعادة مزامنة) |
| **الحالة** | `ISSUED` — جاهز للتنفيذ فور دمج هذا التوجيه في `main` (المخطط يدمجه بنفسه — D-063) |
| **يبدأ من** | `main` @ *(SHA دمج هذا التوجيه — يُقرأ من WORK_STATE §6)*. أول أمر: `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` |
| **الفرع المستهدف** | `work/implementation` ⇒ PR إلى `main` بعنوان `SPRINT-05: Meta operational reads (resync, posts, IG media) + debug_token protocol fix` |
| **الكوميتات** | **اثنان**: Part A (A-0 + A-1) ثم Part B (B1–B4). كل كوميت يُدفع فوراً. |
| **تغيير مخطط البيانات** | **لا شيء.** لا هجرة جديدة. `last_synced_at` و`token_expires_at` و`granted_scopes` موجودة منذ 0001. |
| **الحجم المتوقع** | ~650 سطر كود + ~700 سطر اختبارات |
| **تاريخ الإصدار** | 2026-10-30 |

---

## 0. لماذا؟

1. **A-0 (بروتوكولي، ثبت بنيوياً):** `meta_client.py:185-190` يرسل `POST /debug_token` بجسم `{input_token}` + Bearer. `/debug_token` نقطة **قراءة**؛ Graph يقبل POST على نقاط القراءة فقط مع تجاوز الطريقة `method=GET` في الجسم، وإلا أعاد `(#100) Unsupported post request`. الأسوأ أن `service.py:302-321` (`_inspect_token`) يلتقط أي خطأ غير 190 كـ`WARNING` ويكمل بـ`expires_at=None` و`scopes=[]` — خلل تكوين **يُبتلع بصمت**، فيُحفظ كل اتصال بانتهاء مجهول ونطاقات فارغة. `respx` لا يحاكي هذه القاعدة فمرّت الاختبارات. (مراجعة 04.1 §4.)
2. **A-1:** قرار المخطط في مراجعة 04.1 §3-ب (هوية Meta مختلفة = اتصال **جديد** لا يرث اختياراً) صحيح في الكود ضمنياً لكنه **غير مثبَّت باختبار**.
3. **Part B:** الربط وحده لا قيمة تشغيلية له. أقل قدرة مفيدة للعلامة/الوكالة هي: رؤية منشورات الصفحة ووسائط IG المرتبطة، ومعرفة صحة التوكن وتجديد المزامنة قبل انتهاء الـ60 يوماً — وهي الأساس الذي سيبني عليه الجدولة والنشر (FR-E10) والتشخيص (D-055) لاحقاً.

---

## 1. النطاق

**داخل:** A-0، A-1، B1 صحة التوكن المحسوبة، B2 إعادة المزامنة اليدوية، B3 قراءة منشورات الصفحة، B4 قراءة وسائط Instagram؛ اختباراتها؛ تحديث `WORK_STATE.md` §1 و§6 من المنفذ.
**خارج (ممنوع):** النشر/الجدولة، الرؤى (`/insights` تحتاج `read_insights` — سبرنت لاحق)، أي تخزين للمنشورات/الوسائط (قراءة مباشرة فقط — D-065)، تغيير نطاقات OAuth الافتراضية، أي هجرة، Redis/Temporal/LiteLLM، الملفات المحظورة (`ARCHITECTURE_LOCK.md`, `SESSION_RECOVERY.md`, `imaster_planner.md`, `README.md`, `AI_Marketing_Platform_Hub/**`, `sprints/**`).

---

## 2. المواصفات

### 2-0. قاعدتان حاكمتان لكل نقطة Graph في هذا السبرنت (درس 04.1 — إلزاميتان)

**(أ) قناة الأسرار — جدول إلزامي يُطبَّق حرفياً:**

| نقطة Graph | الطريقة | ما في الرابط (query) | ما في الرأس | ما في الجسم |
|---|---|---|---|---|
| `/debug_token` | `POST` | **لا شيء** | `Authorization: Bearer {app_id}\|{app_secret}` | `input_token`, **`method=GET`** |
| `/me/accounts` (موجود) | `GET` | `fields`, `appsecret_proof(user_token)`, `limit` | `Authorization: Bearer <user_token>` | — |
| `/{page_id}/posts` | `GET` | `fields`, `limit`, `after`, `appsecret_proof(page_token)` | `Authorization: Bearer <page_token>` | — |
| `/{ig_user_id}/media` | `GET` | `fields`, `limit`, `after`, `appsecret_proof(page_token)` | `Authorization: Bearer <page_token>` | — |

- `appsecret_proof` = HMAC-SHA256(`app_secret`, **التوكن المستخدم في Bearer نفسه**). ليس سراً بذاته لكنه مشتق؛ لا يُسجَّل.
- `paging.next` الذي يعيده Graph **يحمل `access_token`** — لا يُعاد إلى العميل أبداً ولا يُتبع تلقائياً في B3/B4؛ يُستخرج `paging.cursors.after` فقط.
- كل اختبار لنقطة جديدة يلتقط الطلب الفعلي عبر `respx` ويؤكد: `"access_token" not in str(request.url)` و`request.headers["authorization"].startswith("Bearer ")`.

**(ب) جدول الرؤية لكل حقل — من يرى ماذا (يُطبَّق في الـschemas لا في الـrouter):**

| البيان | OWNER العلامة | MANAGER = مالك الاتصال | MANAGER غير مالك | VIEWER |
|---|---|---|---|---|
| منشورات/وسائط **الصفحات المختارة** (B3/B4) | ✅ قراءة | ✅ قراءة | ✅ قراءة | ✅ قراءة |
| أي شيء عن **صفحة غير مختارة** (B3/B4) | ❌ 404 `page_not_selected` | ❌ 404 `page_not_selected` | ❌ 404 | ❌ 404 |
| `token_health` (B1) | ✅ | ✅ | ✅ | ✅ |
| إعادة المزامنة (B2) | ❌ 403 `not_connection_owner` (إلا إن كان هو مالك الاتصال) | ✅ | ❌ 403 | ❌ 403 `insufficient_role` |
| `removed_selected_page_ids` في استجابة B2 | — | ✅ | — | — |

> الصفحة غير المختارة تُعامَل كـ**غير موجودة** للجميع (404 لا 403) حتى لمالك الاتصال — لأن 403 يكشف وجودها لغير المالك، والمالك يراها أصلاً في `pages` بوصفها `is_selected=false`.

---

### Part A — إلزامي قبل أي ميزة

#### A-0. `/debug_token` بتجاوز الطريقة + إنهاء الابتلاع الصامت
1. `meta_client.debug_token()`: الجسم يصبح `{"input_token": input_token, "method": "GET"}`. لا تغيير على Bearer. **اختبار عقد `A0-U1`**: يلتقط الطلب ويؤكد `request.method == "POST"`، الجسم يحوي `method=GET` و`input_token=…`، والرابط **خالٍ** من `input_token`/`access_token`.
2. `service._inspect_token()`: يبقى التدهور الرشيق لأخطاء الشبكة/العابرة (`MetaNetworkError`, `MetaTransientError`, `MetaRateLimitError`) على `WARNING`. أما **`MetaGraphError` بكود `100` أو أي `MetaGraphError` عام غير مصنَّف** ⇒ `logger.error("debug_token misconfiguration: …", code, http)` + زيادة عدّاد `src/core/metrics.py::counters["meta_debug_token_failures"]` (قاموس بسيط آمن للخيوط، لا تبعية جديدة) ثم الإكمال بالتدهور نفسه (لا نكسر الربط بسبب خلل مراقبة). **`A0-U2`**: يؤكد مستوى `ERROR` والعدّاد لكود 100، و`WARNING` بلا عدّاد لـ`MetaTransientError`.
3. غطِّ الفرعين غير المغطيين الوحيدين: `service.py:300` و`tenancy/service.py:62` (**`A0-U3`**, **`A0-U4`**).

#### A-1. هوية Meta مختلفة = اتصال جديد
**`A1-I1`** (PG حقيقية): علامة واحدة، اتصال قائم بـ`provider_user_id="u1"` وصفحتان إحداهما مختارة؛ يجري OAuth جديد على العلامة نفسها بـ`/me` يعيد `id="u2"` ويعيد الصفحتين نفسيهما ⇒ صفّ اتصال **ثانٍ** بـ`u2`، لا صفحة مختارة فيه (إلا إن كانت صفحة واحدة — سياسة B6)، واتصال `u1` لم يتغير (اختياره باقٍ). ثم إعادة OAuth بـ`u1` من مستأجر آخر مخوَّل ⇒ نقل `connected_by_tenant_id` **مع بقاء الاختيار** (تثبيت قرار 04.1 §3-ب) — **`A1-I2`**.

---

### Part B — القدرة التشغيلية

#### B1. صحة التوكن المحسوبة (بلا تخزين)
في `ConnectedAccountPublic` حقل جديد `token_health: Literal["valid","expiring_soon","expired","unknown","reauth_required"]` يُحسب في `from_domain(…, now=…)`:
- `status == REAUTH_REQUIRED` ⇒ `reauth_required` (له الأولوية).
- `token_expires_at is None` ⇒ `unknown` (D-055: لا نزعم صلاحية لا نعرفها).
- `expires_at <= now` ⇒ `expired`؛ `expires_at - now <= 7 أيام` ⇒ `expiring_soon`؛ وإلا `valid`.
- حقل `last_synced_at` يُكشف كما هو (كان موجوداً في النموذج، تأكد من ظهوره).
**`B1-U1`** يغطي الحالات الخمس بـ`now` محقون.

#### B2. إعادة المزامنة اليدوية — `POST /api/v1/brands/{brand_id}/social/meta/connections/{connection_id}/resync` ⇒ `200 ConnectedAccountPublic` + `removed_selected_page_ids`
1. `CurrentUserFresh`؛ `_authorise(CONNECTION_WRITE_ROLES)`؛ ثم **مالك الاتصال فقط** (`NotConnectionOwnerError` ⇒ 403) — قبل أي نداء Graph.
2. الخدمة `resync(brand_id, account_id, *, actor_tenant_id)`:
   - فكّ `encrypted_user_token` (داخل الخدمة فقط؛ لا يغادرها).
   - `debug_token(user_token)` عبر `_inspect_token` ⇒ تحديث `token_expires_at` و`granted_scopes` (إن عاد `None` تبقى القيم القديمة). `is_valid == False` أو 190 ⇒ `status_marker.mark_out_of_band(REAUTH_REQUIRED)` ثم ارفع ⇒ **401 `required_action: reconnect`** (نفس مسار H3 — **`B2-I1`** يثبت بقاء العلامة بعد الـrollback).
   - `get_user_pages(user_token)` ⇒ قائمة Meta الحالية. **سياسة الدمج:** الاختيار يُحفظ لكل `page_id` كان مختاراً **وما زال** يعود من Meta بتوكن؛ توكنات الصفحات المختارة **تُستبدل** بالجديدة (مشفَّرة)؛ صفحة كانت مختارة ولم تعد تعود ⇒ تُحذف مع البقية (`replace_pages` بالجملة) وتُدرج في `removed_selected_page_ids` + `logger.warning` بالمعرّفات فقط؛ صفحة غير مختارة ⇒ بيانات وصفية بلا توكن (ثابت B6). حالة الصفحة الواحدة ⇒ تُختار تلقائياً (B6).
   - `last_synced_at = now`؛ `status = ACTIVE` إن كان `ACTIVE` أصلاً (لا تُعيد اتصالاً `REVOKED` إلى الحياة — 409 `connection_revoked`).
   - أضف إلى `ConnectedAccountRepository` ما يلزم فقط (مرشَّح: `update_token_metadata(brand_id, account_id, *, token_expires_at, granted_scopes, last_synced_at)`) في الـProtocol + PG + Fake.
3. **اختبارات:** `B2-U1` الحفاظ على الاختيار واستبدال التوكن؛ `B2-U2` صفحة مختارة اختفت ⇒ `removed_selected_page_ids` وبلا توكن يتيم؛ `B2-U3` غير المالك 403 بلا نداء Graph (`respx` route `.called is False`)؛ `B2-U4` `REVOKED` ⇒ 409؛ `B2-I1` (أعلاه)؛ `B2-I2` على PG: ثابت `CHECK (selected ⇒ token)` لا يُخرق بعد المزامنة.

#### B3. منشورات الصفحة — `GET /api/v1/brands/{brand_id}/social/meta/connections/{connection_id}/pages/{page_id}/posts?limit=25&after=`
1. `CurrentUser` (stateless يكفي للقراءة) ⇒ `_authorise` بأي دور (`OWNER|MANAGER|VIEWER`).
2. الخدمة: الصفحة يجب أن تكون **مختارة وبتوكن** وإلا `PageNotSelectedError` ⇒ **404 `page_not_selected`**. `status == REAUTH_REQUIRED` ⇒ **409 `reauth_required`** بلا نداء Graph. `REVOKED` ⇒ 409 `connection_revoked`.
3. `meta_client.get_page_posts(page_token, page_id, *, limit, after)` ⇒ `GET /{page_id}/posts` بـ`fields=id,message,created_time,permalink_url,status_type,full_picture,is_published`، `limit` (افتراضي 25، أقصى 100 — `Query(ge=1, le=100)`)، `after` اختياري (نص ≤ 512 حرفاً، يُمرَّر كما هو). Bearer + `appsecret_proof` حسب 2-0(أ).
4. الاستجابة `PagePostsPage`: `items: list[PagePostPublic]` (`id, message|None, created_time, permalink_url|None, status_type|None, full_picture|None, is_published|None`)، `next_cursor: str|None` (= `paging.cursors.after` فقط إن وُجد `paging.next`)، `has_more: bool`. **لا `paging.next`، لا توكن، لا رابط Graph خام.**
5. 190 أثناء القراءة ⇒ `mark_out_of_band(REAUTH_REQUIRED)` ⇒ 401 reconnect. `MetaPermissionError` ⇒ 403 `required_action: reconnect`. `MetaRateLimitError` ⇒ 429 + `Retry-After`. العابرة/الشبكية ⇒ 502 بلا تفاصيل.
6. **اختبارات:** `B3-U1` الطلب الفعلي: Bearer، `appsecret_proof` للتوكن الصحيح (أعد حساب HMAC في الاختبار)، لا سر في الرابط؛ `B3-U2` التصفح: `next_cursor` من `cursors.after` و`paging.next` غائب عن الاستجابة وعن أي سجل؛ `B3-U3` صفحة غير مختارة ⇒ 404 لمالك الاتصال **ولغيره** بالنص نفسه؛ `B3-U4` VIEWER يقرأ 200؛ `B3-U5` `REAUTH_REQUIRED` ⇒ 409 و`respx` لم يُستدعَ؛ `B3-U6` 190 ⇒ 401 والعلامة كُتبت عبر الـmarker؛ `B3-U7` `limit=101` ⇒ 422؛ `B3-I1` على PG: المسار كاملاً من OAuth ⇒ اختيار ⇒ قراءة منشورات بالصفحة المختارة.

#### B4. وسائط Instagram — `GET …/connections/{connection_id}/pages/{page_id}/instagram/media?limit=25&after=`
1. نفس التفويض والشروط في B3، زائد: الصفحة بلا `instagram` ⇒ **404 `instagram_not_linked`**.
2. `meta_client.get_instagram_media(page_token, ig_user_id, *, limit, after)` ⇒ `GET /{ig_user_id}/media` بـ`fields=id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count` (كلها ضمن `instagram_basic`). توكن **الصفحة** المرتبطة هو المستخدم (IG Graph API).
3. الاستجابة `InstagramMediaPage`: `items: list[InstagramMediaPublic]` (`id, caption|None, media_type, media_url|None, thumbnail_url|None, permalink|None, timestamp, like_count|None, comments_count|None`)، `next_cursor`, `has_more` — نفس قواعد B3.
4. **اختبارات:** `B4-U1` الطلب الفعلي (Bearer/proof/لا سر)؛ `B4-U2` بلا IG ⇒ 404 `instagram_not_linked`؛ `B4-U3` تصفح؛ `B4-U4` `MetaPermissionError` ⇒ 403 reconnect؛ `B4-I1` على PG.

#### B5. ضوابط مشتركة
- كل توكن صفحة يُفكّ تشفيره في الخدمة لحظة الاستخدام ولا يُحفظ في متغير يتجاوز نطاق الاستدعاء؛ لا يظهر في أي `repr`/سجل/استثناء (`SecretRedactingFilter` يبقى على الجذر).
- أضف `"input_token"` و`"method"` إلى قائمة التنقية `_URL_SECRET_PARAMS` إن لم تكن (الأول موجود).
- وثّق في docstring كل نقطة Graph جديدة: الطريقة، الحد الأقصى لـ`limit`، النطاق المطلوب، وما يحمله `paging.next`.

---

## 3. معايير القبول

- [ ] CI خضراء (الفحوص الخمسة) على الـPR.
- [ ] 0 فشل؛ تغطية ≥ 95% (الهدف الفعلي: لا تراجع عن 99%)؛ `ruff check` + `ruff format --check` نظيفان.
- [ ] `grep -n '"method"' src/modules/social_connect/meta_client.py` يُظهر `"method": "GET"` داخل `data=` لـ`/debug_token`.
- [ ] `grep -rn "paging.next\|paging\[\"next\"\]" src/modules/social_connect/schemas.py src/modules/social_connect/router.py` ⇒ فارغ (لا يُعاد إلى العميل).
- [ ] `grep -n "params=" src/modules/social_connect/meta_client.py | grep -i "token\|secret" | grep -v appsecret_proof` ⇒ فارغ.
- [ ] الاختبارات بالأسماء: `A0-U1..U4`, `A1-I1`, `A1-I2`, `B1-U1`, `B2-U1..U4`, `B2-I1`, `B2-I2`, `B3-U1..U7`, `B3-I1`, `B4-U1..U4`, `B4-I1` — موجودة وناجحة على PG حقيقية.
- [ ] لا هجرة جديدة؛ `alembic/versions/` يحوي 0001–0004 فقط.
- [ ] كوميتان فوق `main`؛ لا ملف محظور مُسّ؛ لا تبعية جديدة في `pyproject.toml`.
- [ ] §6 أدناه مملوء بالسطر؛ `WORK_STATE.md` §1 ⇒ `SPRINT_05_DELIVERED_AWAITING_REVIEW`.

---

## 4. أوامر

```bash
git checkout work/implementation && git fetch origin && git merge --ff-only origin/main
pip install -e ".[dev]"
export TEST_DATABASE_URL=postgresql+asyncpg://aigos@localhost:5433/ai_growth_os_test
export AUTH_JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
ruff check src tests alembic scripts && ruff format --check src tests alembic scripts
pytest --cov=src --cov-fail-under=95
grep -n '"method"' src/modules/social_connect/meta_client.py
grep -rn 'paging.next' src/modules/social_connect/schemas.py src/modules/social_connect/router.py
grep -n "params=" src/modules/social_connect/meta_client.py | grep -i "token\|secret" | grep -v appsecret_proof
ls alembic/versions/
```

---

## 5. التزامات المنفذ

1. **Part A** كوميت: `fix(meta): SPRINT-05 Part A — debug_token method=GET override + visible misconfiguration (A-0), new-identity/ownership-transfer selection tests (A-1)` ⇒ ادفع ⇒ افتح الـPR بالعنوان في بطاقة المهمة.
2. **Part B** كوميت: `feat(social): SPRINT-05 Part B — token_health (B1), manual resync (B2), page posts (B3), Instagram media (B4) — read-through, Bearer-only, selected pages only` ⇒ ادفع.
3. املأ §6 بالسطر، وحدّث `WORK_STATE.md` (§1 ⇒ `SPRINT_05_DELIVERED_AWAITING_REVIEW`؛ سطر جديد في §6 أعلى الجدول). عند التسليم اكتب حرفياً: **«PR #N جاهز لمراجعة المخطط»**. **لا تدمج** (D-063).
4. أي بند لا يمكن تنفيذه كما حُدد ⇒ **لا تُعِد سراً إلى الرابط ولا تُعِد `paging.next` إلى العميل** تحت أي ذريعة؛ وثّق الانحراف بالسطر والسبب في §6.
5. إن رفض Graph (في بيئة حقيقية إن توفرت) أي حقل من `fields` المذكورة ⇒ احذف الحقل، اجعله `None` في الـschema، ووثّق.

---

## 6. تقرير المنفذ (يملؤه المنفذ)

- **حالة التنفيذ:** `[ ] مكتمل` / `[ ] جزئي — السبب:`
- **Commit SHA Part A:** — **Part B:** — **docs:**
- **PR:** #— — **CI:**
- **الاختبارات:** وحدة — / تكامل — / الكل — — **التغطية:** —%
- **A-0:** سطر `"method": "GET"` في `meta_client.py`: — ؛ سطر `logger.error` + العدّاد: —
- **B3/B4: أسطر `headers=`/`params=` للنقطتين الجديدتين:** —
- **B2: سياسة الدمج كما نُفّذت (سطر):** —
- **انحرافات (بالسطر والسبب):**
- **أسئلة:**

---

## 7. ما ينتظر المالك

1. 🔑 **تدوير توكن GitHub — الطلب الخامس.** التوكن الحالي ظهر في المحادثة في كل دور؛ يُعدّ **مكشوفاً**. يُدوَّر الآن ويُرسل الجديد مرة واحدة فقط.
2. 🛡️ حماية `main`: الفحوص الخمسة إلزامية + منع الدفع المباشر. (ملاحظة: PR #13 دُمج يدوياً قبل مراجعة المخطط — D-063 تقول إن المخطط يدمج بعد المراجعة؛ لا ضرر هذه المرة لأن المراجعة اللاحقة أثبتت السلامة.)
3. قرار منتظر لسبرنت لاحق (ليس الآن): إضافة `read_insights` إلى نطاقات OAuth الافتراضية (يغيّر شاشة الموافقة ويتطلب إعادة ربط الاتصالات القائمة).
