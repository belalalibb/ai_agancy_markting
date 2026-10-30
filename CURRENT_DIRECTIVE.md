# 🎯 CURRENT DIRECTIVE — التوجيه التنفيذي النشط

> **المصدر الوحيد للمهمة الحالية.** SPRINT-05 رُوجع مستقلاً و**قُبل ودُمج** بواسطة المخطط (`main` @ `d026a2e`؛ التقرير في [`sprints/SPRINT-05_REVIEW.md`](./sprints/SPRINT-05_REVIEW.md)). هذا التوجيه يفتح **المحطة M3 — نواة المحتوى والموافقة البشرية** (خريطة المحطات في `WORK_STATE.md` §2-ب): أول جدول أعمال (`content_items`) بدورة حياة محكومة، وبوابة الذكاء الاصطناعي (D-056) خلف واجهة واحدة بمزوّد وهمي قابل للاستبدال — مع الجزء A الإلزامي لإغلاق ما ثبت في مراجعة 05.

---

## بطاقة المهمة (Task Card)

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-06-CONTENT-CORE-AND-APPROVAL` |
| **النوع** | Part A: تصحيحات إلزامية (A-1..A-3) · Part B: ميزة (وحدة `content` + بوابة `ai_gateway`) · **هجرة واحدة** `0005_content` |
| **الحالة** | `ISSUED` — جاهز للتنفيذ فور دمج هذا التوجيه في `main` (المخطط يدمجه بنفسه — D-063) |
| **يبدأ من** | `main` @ `34bb948` (دمج PR #17 — هذا التوجيه). أول أمر: `git checkout work/implementation && git fetch origin && git merge --ff-only origin/main` |
| **الفرع المستهدف** | `work/implementation` ⇒ PR إلى `main` بعنوان `SPRINT-06: content core + human approval + AI gateway (D-055/D-056) + SPRINT-05 review fixes` |
| **الكوميتات** | **ثلاثة**: Part A ⇒ Part B-1 (هجرة + وحدة `content`) ⇒ Part B-2 (`ai_gateway` + التوليد). كل كوميت يُدفع فوراً، **وكل ملف يُدفع فور اكتماله** (`wip:` مسموح ثم يُسحق) |
| **تبعيات جديدة** | **لا شيء.** LiteLLM Proxy **لا يُثبَّت** في هذا السبرنت — تُبنى واجهته فقط (`AIGateway` Protocol) بمزوّد `FakeProvider` و`HttpOpenAICompatibleProvider` عبر `httpx` القائم؛ تشغيل Proxy حقيقي في SPRINT-07 |
| **الحجم المتوقع** | ~1100 سطر كود + ~1000 سطر اختبارات |
| **تاريخ الإصدار** | 2026-10-30 |

---

## 0. لماذا؟

1. **A-1 (O3، ثبت تجريبياً):** `service.py:379` `granted_scopes=list(debug.scopes) if debug and debug.scopes else None` — النطاقات **الفارغة المؤكَّدة** (المستخدم سحب الصلاحيات) تُعامَل كـ«مجهول» فتبقى القديمة معروضة؛ و`service.py:180` تعكس الخطأ (الفشل يُكتب `[]` كحقيقة). يخالف D-055.
2. **A-1 (O2، ثبت جزئياً):** `service.py:331-339` `resync` على اتصال `REAUTH_REQUIRED` ينجح بـ200 ويترك الحالة، فتبقى `/posts`/`/media` محجوبة بـ409 — تناقض من زاوية العميل.
3. **A-2 (سؤال المنفذ 1):** `required_action` له قيمتان (`reconnect` / `reauthorize_with_additional_scopes`) للإجراء العملي نفسه.
4. **A-3 (O1، لم تثبت لكن وقائي):** `status_type` تراثي؛ `attachments{…}` هو المصدر المعتمد للوسائط منذ v3.3.
5. **Part B:** المنصة بلا **جدول أعمال**: لا يمكن إنشاء منشور، ولا تمريره للموافقة البشرية (D-055 بند 2 — **كل إجراء خارجي يتطلب موافقة صريحة**)، ولا جدولته. هذه هي نواة دورة المحتوى التي يقف عليها النشر (M4) والمحادثات (M6). وبوابة الذكاء الاصطناعي تُبنى كواجهة واحدة الآن كي لا تتسرب استدعاءات مزوّدين مباشرة إلى الوحدات لاحقاً (D-056).

---

## 1. النطاق

**داخل:** A-1..A-3؛ هجرة `0005_content`؛ وحدة `src/modules/content/` (`content_items` + `content_revisions` + `approval_events`) بدورة الحياة والـAPI؛ وحدة `src/modules/ai_gateway/` (Protocol + Fake + OpenAI-compatible HTTP + سجل استخدام)؛ توليد مسودة نصية عبر البوابة؛ اختباراتها؛ تحديث `WORK_STATE.md` §1/§6 من المنفذ.
**خارج (ممنوع):** النشر الفعلي إلى Meta (M4)، الجدولة الزمنية التنفيذية/Temporal (M4)، توليد الصور، WhatsApp، الواجهة الأمامية، تثبيت LiteLLM/Redis/Temporal، تغيير نطاقات OAuth، الملفات المحظورة (`ARCHITECTURE_LOCK.md`, `SESSION_RECOVERY.md`, `imaster_planner.md`, `README.md`, `AI_Marketing_Platform_Hub/**`, `sprints/**`).

---

## 2. المواصفات

### 2-0. القواعد الحاكمة (D-064 / D-055 — إلزامية)

**(أ) قناة الأسرار للبوابة:** مفتاح المزوّد (`AI_PROVIDER_API_KEY`, `SecretStr`) يسافر في `Authorization: Bearer` فقط؛ لا يُسجَّل؛ يُضاف نمطه إلى قائمة التنقية (`sk-` بادئة). نصوص المستخدم (prompts) **لا تُسجَّل** على INFO (DEBUG فقط، مقتطعة 200 حرفاً).

**(ب) جدول الرؤية لكل حقل (وحدة `content`):**

| البيان | OWNER | MANAGER | VIEWER |
|---|---|---|---|
| قراءة `content_items` + المراجعات + سجل الموافقات للعلامة | ✅ | ✅ | ✅ |
| إنشاء/تعديل مسودة (`draft`) | ✅ | ✅ | ❌ 403 `insufficient_role` |
| إرسال للموافقة (`submit`) | ✅ | ✅ | ❌ |
| **موافقة/رفض** (`approve`/`reject`) | ✅ | ❌ 403 `approval_requires_owner` | ❌ |
| جدولة (`schedule`) عنصر `approved` | ✅ | ✅ | ❌ |
| أرشفة | ✅ | ✅ (مسوداته هو فقط: `created_by_tenant_id` = الفاعل) | ❌ |
| `generation_log` (تكلفة التوكنات، المزوّد) | ✅ | ✅ | ❌ (الحقل يُحذف من الاستجابة) |

> الموافقة **حق المالك وحده** في هذه المرحلة (الوكالة تقترح، العميل يعتمد — جوهر D-055). الفاعل لا يعتمد مسودته هو إن كان MANAGER؛ OWNER يعتمد أي شيء بما فيه مسوداته (شركة فردية).

**(ج) Evidence Schema للتوليد (D-055 بند 1):** كل مخرَج توليد يُخزَّن مع `provenance = {"kind": "ai_generated", "provider", "model", "prompt_hash", "generated_at"}`؛ لا يُعرض أي نص مولَّد بوصفه «قياساً» أو «حقيقة».

---

### Part A — إلزامي قبل أي ميزة (كوميت واحد)

#### A-1. ثلاث حالات لا اثنتان + اتساق `resync`
1. `service.py:379`: `granted_scopes=list(debug.scopes) if debug is not None else None` — الفارغة **تُكتب**. اختبار **`A1-U1`**: `debug_token` يعيد `is_valid=true, scopes=[]` ⇒ بعد `resync` تصبح `granted_scopes == []`؛ **`A1-U2`**: `debug_token` يفشل (transient) ⇒ تبقى القديمة.
2. `service.py:180` (`complete_connection`): يبقى `[]` في العمود (NOT NULL) لكن يُضاف حقل محسوب في `ConnectedAccountPublic`: `scopes_verified: bool` = هل جاءت النطاقات من `debug_token` ناجح. لتجنب هجرة: يُشتق من `token_expires_at is not None or granted_scopes != []`… **لا** — هذا تخمين. القرار: **أضف عموداً** `scopes_verified BOOLEAN NOT NULL DEFAULT false` إلى `social_connections` ضمن هجرة `0005` (نفس الهجرة؛ backfill: `true` حيث `granted_scopes <> '[]'`). `resync` و`complete_connection` يكتبانه. **`A1-U3`** يغطي القيمتين.
3. `resync` على `REAUTH_REQUIRED` ⇒ `ReauthRequiredError` ⇒ **409 `reauth_required`** قبل أي نداء Graph (نفس سلوك B3/B4). حدّث docstring `:331-332`. **`A1-U4`**: `respx` route لم يُستدعَ. **`A1-I1`** على PG: الحالة تبقى `reauth_required` والصفحات لم تُمس.

#### A-2. توحيد `required_action`
`router.py:493-500`: `MetaPermissionError` ⇒ `"reconnect"` في **كل** المسارات؛ احذف `reauthorize_with_additional_scopes` والمعامل `operational_read` إن لم يعد له استخدام آخر غير 502 الثابت (أبقِ 502 الثابت `{"error":"upstream_unavailable"}` لكل مسارات Meta — توحيد أيضاً). حدّث `test_router.py:402, 1005`. **`A2-U1`** يثبت القيمة الواحدة على 4 مسارات.

#### A-3. `attachments` لمنشورات الصفحة
`POST_FIELDS` += `attachments{media_type,type,url,media{image{src}}}`؛ `PagePostPublic` += `attachments: list[PostAttachmentPublic]` (`media_type|None, type|None, url|None, image_src|None`) مع تجاهل أي مفتاح آخر. `status_type` و`full_picture` يبقيان. **`A3-U1`** اختبار عقد: قائمة الحقول المطلوبة ⊆ قائمة مسموح بها ثابتة في الاختبار مستقاة من مرجع Post v26.0 (المذكورة في مراجعة 05 §4-O1) ولا تحوي أي حقل من المهملة في v3.3 (`caption, description, link, name, object_id, picture, source, type` — **على مستوى الجذر**؛ داخل `attachments{}` مسموح).

---

### Part B-1 — وحدة `content` + هجرة `0005_content`

#### B1. مخطط البيانات (هجرة `0005_content`، صعوداً ونزولاً، مُختبَرة على PG)
```
content_items
  id UUID PK
  brand_id UUID NOT NULL → brands(id) ON DELETE CASCADE
  created_by_tenant_id UUID NOT NULL → tenants(id) ON DELETE RESTRICT
  channel VARCHAR(16) NOT NULL CHECK (channel IN ('facebook_page','instagram'))
  target_page_id VARCHAR(64) NULL            -- Page المختارة؛ يُتحقق عند schedule لا عند draft
  status VARCHAR(24) NOT NULL CHECK (status IN ('draft','pending_approval','approved','rejected','scheduled','archived'))
  current_revision_id UUID NULL → content_revisions(id) (FK تُضاف بعد إنشاء الجدولين)
  scheduled_for TIMESTAMPTZ NULL             -- CHECK: (status='scheduled') = (scheduled_for IS NOT NULL)
  created_at / updated_at TIMESTAMPTZ NOT NULL
  INDEX (brand_id, status), INDEX (brand_id, scheduled_for) WHERE status='scheduled'

content_revisions
  id UUID PK
  item_id UUID NOT NULL → content_items(id) ON DELETE CASCADE
  revision_no INT NOT NULL, UNIQUE (item_id, revision_no)
  body TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 5000)
  media_refs JSONB NOT NULL DEFAULT '[]'      -- روابط فقط؛ لا رفع ملفات في هذا السبرنت
  provenance JSONB NOT NULL                   -- {"kind":"human"} أو {"kind":"ai_generated",...} (2-0-ج)
  created_by_tenant_id UUID NOT NULL → tenants(id)
  created_at TIMESTAMPTZ NOT NULL

approval_events
  id UUID PK
  item_id UUID NOT NULL → content_items(id) ON DELETE CASCADE
  revision_id UUID NOT NULL → content_revisions(id)
  action VARCHAR(16) NOT NULL CHECK (action IN ('submitted','approved','rejected','withdrawn'))
  actor_tenant_id UUID NOT NULL → tenants(id)
  actor_user_id UUID NOT NULL → users(id)
  note TEXT NULL CHECK (length(note) <= 1000)
  created_at TIMESTAMPTZ NOT NULL
  INDEX (item_id, created_at)

social_connections += scopes_verified BOOLEAN NOT NULL DEFAULT false   (A-1)
```
**`B1-M1`** اختبار هجرة: upgrade ⇒ إدراج بيانات حدية (نص 5000 حرف، `status='scheduled'` بلا `scheduled_for` ⇒ يفشل CHECK) ⇒ downgrade ⇒ upgrade.

#### B2. دورة الحياة (آلة حالات صريحة في `domain/lifecycle.py` — نقية، بلا I/O)
```
draft ──submit──▶ pending_approval ──approve──▶ approved ──schedule──▶ scheduled
  ▲                     │  │                        │                      │
  │◀──────withdraw──────┘  └──reject──▶ rejected ───┘(edit ⇒ new revision ⇒ draft)
  │                                                                        │
  └──────────── edit على approved/scheduled ⇒ مراجعة جديدة ⇒ draft ◀───────┘ (unschedule)
أي حالة غير archived ──archive──▶ archived (نهائية)
```
- كل انتقال دالة `transition(item, action, *, actor_role, actor_tenant_id, now) -> ContentItem | LifecycleError` بجدول مسموح ثابت؛ **`B2-U1`** يختبر **كل** خلية في مصفوفة (حالة × إجراء) — 6 حالات × 7 إجراءات = 42 حالة، كلها مسماة.
- **تعديل الجسم** بعد `pending_approval` أو بعده ⇒ مراجعة جديدة + العودة إلى `draft` + حدث `withdrawn` تلقائي (الموافقة تخص مراجعة بعينها — `approval_events.revision_id`). **`B2-U2`**.
- `approve`/`reject`: OWNER فقط (2-0-ب)؛ `reject` يتطلب `note` (≥ 3 أحرف) ⇒ 422 بدونه. **`B2-U3`**.
- `schedule(scheduled_for)`: الحالة `approved`؛ `scheduled_for > now + 5 دقائق` وإلا 422 `schedule_too_soon`؛ `target_page_id` **يجب** أن يكون صفحة **مختارة بتوكن** في اتصال `active` للعلامة نفسها (استعلام عبر `ConnectedAccountRepository.list_by_brand`) وإلا 422 `target_page_not_selected`؛ للقناة `instagram` يجب أن تكون للصفحة `instagram` مرتبط وإلا 422 `instagram_not_linked`. **`B2-U4`**, **`B2-I1`** على PG.
- **لا تنفيذ للجدولة** في هذا السبرنت: `scheduled` حالة انتظار؛ المنفّذ الفعلي في M4.

#### B3. الـAPI (`/api/v1/brands/{brand_id}/content/...`، `CurrentUserFresh` للكتابة، `CurrentUser` للقراءة، `_authorise` بنمط social_connect)
| الطريقة | المسار | الدور | الاستجابة |
|---|---|---|---|
| `POST` | `/items` | OWNER/MANAGER | 201 `ContentItemPublic` (مسودة + مراجعة 1، `provenance={"kind":"human"}`) |
| `GET` | `/items?status=&limit=&cursor=` | الكل | قائمة مرقّمة (keyset على `(created_at, id)`؛ `limit ≤ 100`) |
| `GET` | `/items/{id}` | الكل | `ContentItemPublic` + `revisions` (آخر 20) + `approval_events` |
| `PUT` | `/items/{id}/body` | OWNER/MANAGER | 200 — مراجعة جديدة (B2) |
| `POST` | `/items/{id}/submit` · `/withdraw` · `/approve` · `/reject` · `/schedule` · `/unschedule` · `/archive` | حسب 2-0-ب | 200 `ContentItemPublic`؛ انتقال غير مسموح ⇒ **409 `invalid_transition`** بجسم `{from, action}` |
| `POST` | `/items/{id}/generate` | OWNER/MANAGER | 200 — مراجعة جديدة مولَّدة (Part B-2) |
- التزامن: كل انتقال يُنفَّذ بـ`UPDATE … WHERE id=… AND status=<expected>` ويفحص `rowcount == 1` وإلا 409 (لا قفل تشاؤمي). **`B3-I1`** على PG: `asyncio.gather(approve, reject)` على العنصر نفسه ⇒ بالضبط واحد 200 وواحد 409.
- **`B3-U1..U6`**: الأدوار والرؤية لكل صف في 2-0-ب (بما فيها حذف `generation_log` لـVIEWER)؛ **`B3-U7`**: عزل العلامات (عنصر علامة أخرى ⇒ 404 `content_not_found`).

---

### Part B-2 — بوابة الذكاء الاصطناعي `src/modules/ai_gateway/` (D-056 — الواجهة الآن، LiteLLM لاحقاً)

#### B4. الواجهة
```python
class AIGateway(Protocol):
    async def complete(self, req: CompletionRequest) -> CompletionResult: ...
# CompletionRequest: purpose: Literal["content_draft"], system: str, user: str, max_tokens: int (≤ 2000), temperature: float (0..1), brand_id, tenant_id
# CompletionResult: text: str, provider: str, model: str, prompt_tokens: int|None, completion_tokens: int|None, latency_ms: int, finish_reason: str|None
```
- `FakeProvider` (حتمي: يعيد قالباً من الطلب؛ يُستخدم في الاختبارات وفي التشغيل حين `AI_PROVIDER=fake`).
- `HttpOpenAICompatibleProvider`: `POST {AI_PROVIDER_BASE_URL}/chat/completions` (متوافق مع LiteLLM Proxy وOpenAI وOllama) عبر `httpx.AsyncClient` المشترك؛ Bearer من `AI_PROVIDER_API_KEY`؛ مهلة 30 ث؛ **لا إعادة محاولة على 4xx**؛ إعادة واحدة على 5xx/timeout؛ 429 ⇒ `AIRateLimitedError` (⇒ 429 + `Retry-After`)؛ أي خطأ آخر ⇒ `AIUpstreamError` ⇒ 502 ثابت `{"error":"ai_upstream_unavailable"}`.
- الإعدادات: `AI_PROVIDER` (`fake|openai_compatible`، افتراضي `fake`)، `AI_PROVIDER_BASE_URL`، `AI_PROVIDER_API_KEY` (`SecretStr`)، `AI_DEFAULT_MODEL`. غياب المفتاح مع `openai_compatible` ⇒ 503 عند الاستدعاء (نمط `get_encryption_service`).
- **`B4-U1`** اختبار عقد يلتقط الطلب: Bearer موجود، المفتاح غير موجود في الرابط ولا في السجل (نفس تجربة 04.1)؛ **`B4-U2`** 429/5xx/timeout؛ **`B4-U3`** `FakeProvider` حتمي.

#### B5. التوليد — `POST /items/{id}/generate` بجسم `{brief: str (10..2000), tone: Literal["professional","friendly","promotional"] = "professional", language: Literal["ar","en"] = "ar"}`
1. الحالة يجب أن تكون `draft` أو `rejected` (وإلا 409 `invalid_transition`).
2. `system` ثابت في `prompts.py` (عربي أولاً؛ يذكر القناة وحدّ 2200 حرفاً لـIG / 5000 لـFB؛ يمنع ادعاءات أرقام غير معطاة في الـbrief — D-055).
3. الناتج ⇒ مراجعة جديدة بـ`provenance={"kind":"ai_generated","provider","model","prompt_hash": sha256(system+user)[:16],"generated_at"}` والعنصر يعود/يبقى `draft`.
4. سجل استخدام `ai_usage_log` **ليس جدولاً جديداً** في هذا السبرنت — يُكتب كـ`logger.info` منظَّم (`brand_id, tenant_id, provider, model, prompt_tokens, completion_tokens, latency_ms`) + عدّادات `counters["ai_completions"]`/`["ai_failures"]`. (جدول التكلفة في M7.)
5. حدّ معدل: `RateLimiter` القائم — 20 توليداً/ساعة لكل علامة ⇒ 429. **`B5-U1..U4`**: المسار السعيد (Fake)، الحالة الخاطئة، الحدّ، `provenance` الصحيح؛ **`B5-I1`** على PG.

### B6. ضوابط مشتركة
- لا نص مستخدم/مولَّد في سجلات INFO؛ `SecretRedactingFilter` يبقى؛ `sk-[A-Za-z0-9]{8,}` يُضاف إلى التنقية.
- كل استثناء جديد له `error` ثابت (snake_case) موثَّق في docstring المسار.
- وثّق في docstring كل مسار: الأدوار، الانتقالات، أكواد الخطأ.

---

## 3. معايير القبول

- [ ] CI خضراء (الفحوص الخمسة) على الـPR.
- [ ] 0 فشل؛ تغطية ≥ 95% (لا تراجع عن 99%)؛ `ruff check` + `ruff format --check` نظيفان.
- [ ] `grep -n "if debug and debug.scopes" src` ⇒ فارغ؛ `grep -rn "reauthorize_with_additional_scopes" src` ⇒ فارغ.
- [ ] `alembic/versions/` = 0001–**0005**؛ `0005` تمرّ صعوداً ونزولاً على PG (**`B1-M1`**).
- [ ] الاختبارات بالأسماء: `A1-U1..U4`, `A1-I1`, `A2-U1`, `A3-U1`, `B1-M1`, `B2-U1..U4`, `B2-I1`, `B3-U1..U7`, `B3-I1`, `B4-U1..U3`, `B5-U1..U4`, `B5-I1` — موجودة وناجحة على PG حقيقية.
- [ ] `B2-U1` يغطي 42 خلية مسماة (parametrize).
- [ ] لا تبعية جديدة في `pyproject.toml`؛ ثلاثة كوميتات فوق `main`؛ لا ملف محظور مُسّ.
- [ ] §6 مملوء بالسطر؛ `WORK_STATE.md` §1 ⇒ `SPRINT_06_DELIVERED_AWAITING_REVIEW`.

---

## 4. أوامر

```bash
git checkout work/implementation && git fetch origin && git merge --ff-only origin/main
pip install -e ".[dev]"
export TEST_DATABASE_URL=postgresql+asyncpg://aigos@localhost:5433/ai_growth_os_test
export AUTH_JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
ruff check src tests alembic scripts && ruff format --check src tests alembic scripts
pytest --cov=src --cov-fail-under=95
grep -rn "if debug and debug.scopes\|reauthorize_with_additional_scopes" src
ls alembic/versions/
```

---

## 5. التزامات المنفذ

1. **Part A** كوميت: `fix(meta): SPRINT-06 Part A — empty≠unknown scopes + scopes_verified (A-1), resync 409 on reauth_required (A-1), unified required_action=reconnect (A-2), post attachments (A-3)` ⇒ ادفع ⇒ افتح الـPR.
2. **Part B-1** كوميت: `feat(content): SPRINT-06 Part B-1 — migration 0005, content_items/revisions/approval_events, explicit lifecycle (42-cell matrix), owner-only approval (D-055), optimistic transitions` ⇒ ادفع.
3. **Part B-2** كوميت: `feat(ai_gateway): SPRINT-06 Part B-2 — AIGateway protocol, Fake + OpenAI-compatible providers (D-056), /generate with provenance, rate limit` ⇒ ادفع.
4. **ادفع بعد كل ملف** (`wip:` ثم سحق إلى الكوميتات الثلاثة قبل التسليم) — درس انحراف 7 في SPRINT-05.
5. املأ §6 بالسطر؛ `WORK_STATE.md` (§1 ⇒ `SPRINT_06_DELIVERED_AWAITING_REVIEW`؛ سطر جديد أعلى §6). عند التسليم اكتب حرفياً: **«PR #N جاهز لمراجعة المخطط»**. **لا تدمج** (D-063).
6. أي بند متعذر ⇒ وثّق الانحراف بالسطر والسبب؛ **لا** تُضعف جدول الرؤية (2-0-ب) ولا قاعدة «الموافقة للمالك فقط» تحت أي ذريعة — هذه قرارات مالك.

---

## 6. تقرير المنفذ (يملؤه المنفذ)

- **حالة التنفيذ:** `[ ] مكتمل` / `[ ] جزئي — السبب:`
- **Commit SHA Part A:** — **B-1:** — **B-2:** — **docs:**
- **PR:** #— — **CI:**
- **الاختبارات:** وحدة — / تكامل — / الكل — — **التغطية:** —%
- **A-1:** أسطر `granted_scopes`/`scopes_verified`/`ReauthRequiredError` في `resync`: —
- **B1:** `0005` upgrade/downgrade على PG: — ؛ CHECKs: —
- **B2:** مصفوفة 42 خلية — ملف/سطر: —
- **B3-I1:** نتيجة سباق approve/reject على PG: —
- **B4-U1:** التقاط الطلب (Bearer/لا مفتاح في الرابط/السجل): —
- **انحرافات (بالسطر والسبب):**
- **أسئلة:**

---

## 7. ما ينتظر المالك

1. 🔑 **تدوير توكن GitHub — الطلب السادس.** التوكن ظهر في المحادثة مجدداً؛ يُعدّ مكشوفاً.
2. 🛡️ حماية `main` بالفحوص الخمسة + منع الدفع المباشر.
3. **قرار مطلوب قبل M4 (لا الآن):** مزوّد الذكاء الاصطناعي الفعلي الأول خلف البوابة (مفتاح OpenAI-compatible أو تشغيل LiteLLM Proxy محلياً) — السبرنت الحالي يعمل بـ`AI_PROVIDER=fake`.
4. **قرار مطلوب قبل M6:** حساب WhatsApp Business (Cloud API) ورقم اختبار.
