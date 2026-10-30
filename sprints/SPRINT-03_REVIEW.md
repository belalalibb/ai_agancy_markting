# 📋 SPRINT-03 — تقرير مراجعة المخطط

| البند | القيمة |
|---|---|
| **معرف المهمة** | `SPRINT-03-CI-GATES-AND-TENANCY` |
| **المُراجِع** | AI Master Planner |
| **تاريخ المراجعة** | 2026-10-29 |
| **الفرع المُراجَع** | `work/implementation` @ `2b1e1fe` (Part A `a985d55`، Part B `b712df0`، توثيق `2b1e1fe`) فوق `main` @ `aae3600` |
| **Pull Request** | [#7](https://github.com/belalalibb/ai_agancy_markting/pull/7) |
| **الحكم النهائي** | 🟢 **مقبول — يُدمج.** لا ثغرات حاجبة. ملاحظة أمنية واحدة معروفة ومقصودة (N1) تصبح محور SPRINT-04. |

> البيئة أُعيد ضبطها بالكامل قبل المراجعة (PostgreSQL 17.11 + pgvector أُعيد تثبيتها من الصفر)، والمراجعة على worktree معزول للفرع. **كل رقم في §1 نفّذه المخطط بنفسه**؛ تقرير المنفذ (§6) قُورن به لاحقاً.

---

## 1. ما تحقق منه المخطط فعلياً

| الفحص | نتيجة المخطط | ادعاء المنفذ | تطابق |
|---|---|---|---|
| **CI على `2b1e1fe`** (GitHub Checks API مباشرة) | 5 فحوص منفصلة `lint` / `unit` / `integration` / `secrets` / `licenses` — **كلها `success`** على `push` و`pull_request` | ✓ | ✅ |
| `ruff check` / `ruff format --check` على `src tests alembic scripts` | نظيفان (63 ملفاً) | ✓ | ✅ |
| `pytest -m "not integration" --cov-fail-under=95` | **298 passed**, 1 skipped، تغطية **99.68%** | 298 / 99.68% | ✅ |
| `pytest -m integration` على **PostgreSQL 17.11** حقيقية | **17 passed** | 17 | ✅ |
| الحزمة كاملة | **315 passed، 0 فشل** | 315 | ✅ |
| `grep "Depends(get_db_session)" src/main.py` / `grep "tenant_id: str" src` | فارغان | ✓ | ✅ |
| `alembic.ini` | `path_separator = os` (سطر 8) | ✓ | ✅ |
| **OpenAPI حيّاً** (13 مساراً) | لا مسار `/social/meta/connections` خارج `/brands/{brand_id}/`؛ `/api/v1/social/meta/callback` موجود؛ الحقول التي تحوي `token`: **`has_page_token` فقط** | ✓ | ✅ |
| **هجرة 0003 — تجربة مستقلة** بحالات حدية (§2) | ناجحة صعوداً ونزولاً وصعوداً | I-B1 ناجح | ✅ |
| بوابة التراخيص في venv نظيف (`pip install .`) | `exit 0`، **34 حزمة**، 0 مرفوضة | exit 0 / 37 | ✅ (الفرق = حزم pip/setuptools بين البيئتين؛ لا أثر) |
| الاختبارات الإلزامية بالاسم: `test_identity_and_secrets_never_logged`, `test_openapi_has_no_token_named_fields_except_has_page_token`, `test_health_ready_has_no_session_dependency`, `test_alembic_0003_migrates_legacy_rows_and_roundtrips`, `test_disconnect_leaves_no_provider_name_anywhere`, `test_revoke_grant_purges_agency_connections_via_api` | **كلها موجودة وناجحة** | ✓ | ✅ |
| فحص الأسرار (`ghp_`, `EAA`, `AKIA`) | نظيف | ✓ | ✅ |
| الملفات المحظورة | `git diff --name-only origin/main..HEAD` لا يمسّ أياً منها | ✓ | ✅ |
| `scripts/verify_hub.py` | ALL CHECKS PASSED | — | ✅ |
| الكوميتات فوق `main` | 3 بالضبط (A، B، docs) كما يسمح §6 | ✓ | ✅ |

---

## 2. تجربة الهجرة المستقلة (أقسى من I-B1)

أدرجتُ على `0002` بيانات قديمة مصمَّمة لكسر backfill الـslug ثم شغّلت `upgrade head`:

| الحالة القديمة | `tenant_id` | النتيجة بعد 0003 |
|---|---|---|
| تصادم slug بعد التطبيع | `Acme Co` و`acme-co` ⇒ كلاهما `acme-co` | `acme-co-0d85f417` و`acme-co-2580bfdc` — **لا تصادم، الاسم الأصلي محفوظ في `name`** |
| أقصر من 3 أحرف | `ab` | `ab-187ef443` |
| أحرف عربية بالكامل | `عميل` | `a1d0c2ac` (slug من md5؛ `name` = `عميل`) |
| نفس حساب Meta `u1` في مستأجرين | صفّان | صفّان في علامتين مختلفتين (`uq_social_connections_brand_identity` يسمح) |
| صفحة بتوكن / صفحة NULL | — | `is_selected = true / false` — مطابق للمواصفة B3-5 |

ثم `downgrade 0002`: `tenant_id` عاد بقيمة slug للصفوف الأربعة، جداول Tenancy اختفت، `uq_social_connections_identity` أُعيد. ثم `upgrade head` نجح مجدداً ⇒ `0003 (head)`.

**ملاحظة تصميمية صحيحة من المنفذ (ملاحظة المالك 3):** في `downgrade()` يُنفَّذ `DELETE` للصفوف المكررة على `(tenant_id, provider, provider_user_id)` قبل إعادة القيد الفريد القديم (يُبقي الأحدث). هذا **حذف بيانات مقصود وموثَّق في docstring** ولا بديل له: المخطط القديم لا يستطيع تمثيل حساب Meta واحد مرتبطاً بعلامتين لنفس المالك. مقبول.

---

## 3. المراجعة البندية

### Part A
| # | المطلوب | ما وُجد | الحكم |
|---|---|---|---|
| A1 | 5 وظائف منفصلة، `pgvector/pgvector:pg16`، Python 3.12، لا `continue-on-error` | `.github/workflows/ci.yml`: الوظائف الخمس بأسمائها؛ خدمة PG16 مع health-check؛ `permissions: contents: read`؛ `concurrency` | ✅ |
| A2/A3 | `scripts/check_licenses.py` + اختبارات | السكربت ذاتي (بلا تبعيات)؛ **11 اختباراً** في `tests/scripts/` (المطلوب 5)؛ allowlist فارغة | ✅ |
| A6 | `/health/ready` عبر `engine.connect()` | `main.py:86-87`: `async with database.get_engine().connect() as conn: await conn.execute(text("SELECT 1"))`؛ لا `Depends` | ✅ |
| A7 | `path_separator = os` | موجود | ✅ |
| gitleaks | `GITHUB_TOKEN` + `permissions` (ملاحظة المالك 1) | `secrets` job: `contents: read, pull-requests: read`، `fetch-depth: 0`، `env.GITHUB_TOKEN` | ✅ |

### Part B
| # | المطلوب | ما وُجد | الحكم |
|---|---|---|---|
| B1 | وحدة `tenancy` مستقلة؛ اتجاه استيراد واحد | `src/modules/tenancy/{domain,infrastructure,service,schemas,router,exceptions}`؛ `tenancy` لا يستورد `social_connect` (حذف اتصالات الوكالة بـ SQL خام — انحراف 5 **صحيح معمارياً**) | ✅ |
| B2 | الجداول الثلاثة + القيود | `0003_tenancy.py` (309 سطراً): RESTRICT/CASCADE كما حُدد، فهرس جزئي `uq_brand_access_grants_active`، CHECK `ck_social_connection_pages_selected_has_token`، `uq_social_connections_brand_identity` | ✅ |
| B3 | backfill آمن | SQL خام عبر `_legacy_tenant_map` (TEMP TABLE)، `gen_random_uuid()`، منطق slug بـ window function — **تحقق §2** | ✅ |
| B4 | `tenant_id` UUID، مصدر واحد | `tenancy/dependencies.py` `ActorTenantId`؛ grep `tenant_id: str` فارغ | ✅ |
| B5 | `BrandAccessResolver`؛ None ⇒ 404، دور ناقص ⇒ 403 | `TenancyService.require_role`؛ `BrandNotFoundError ⇒ 404`, `InsufficientRoleError ⇒ 403` في `tenancy/router.py:180-185` | ✅ |
| B6 | اختيار الصفحات؛ ≥2 ⇒ بلا توكنات؛ ثابت «لا توكن لصفحة غير مختارة» | `service._apply_selection_policy` (صفحة واحدة ⇒ مختارة؛ وإلا المختارة سابقاً فقط — **إعادة الربط تحافظ على الاختيار وتُحدّث التوكن**، ملاحظة المالك 2)؛ `select_pages` يرمي `UnknownPageIdsError` قبل أي كتابة؛ الثابت مفروض في الدومين (`violates_selection_invariant`) **وفي DB** (CHECK) | ✅ |
| B7 | حارس `provider_user_name` | `test_identity_and_secrets_never_logged` (caplog DEBUG عبر التدفق الكامل) + `test_disconnect_leaves_no_provider_name_anywhere` (PG) | ✅ |
| B8 | سحب المنحة يمسح اتصالات الوكالة في هذه العلامة فقط | `TenancyService.revoke_grant` ⇒ `delete_connections_of_grantee(brand_id, grantee)` في نفس المعاملة؛ `test_revoke_grant_purges_agency_connections_via_api` | ✅ |
| B9 | التفويض في callback **قبل** أي نداء Graph | `router.py:206-208`: `verify_state` ⇒ `_authorise(..., CONNECTION_WRITE_ROLES)` ⇒ ثم `complete_connection`؛ الاختبار يثبت `me_route.call_count == 0` و`token_route.call_count == 0` | ✅ |
| B11 | حارس Zero-Secret موسّع | يغطي `/openapi.json` كاملاً (تحقق حيّاً) | ✅ |

### الانحرافات الثمانية في تقرير المنفذ
كلها **مقبولة**: (1) إعادة تنسيق `verify_hub.py` بلا تغيير منطقي — لازمة لبوابة `ruff format`؛ (2) `(?-i:EAA)` يُصلح تذبذباً حقيقياً؛ (3) كوميتات WIP سُحقت والنتيجة النهائية مطابقة لـ §6؛ (4) الإبقاء على `connected_at` الأول قرار صحيح؛ (5) SQL خام لاحترام اتجاه الاستيراد — **الخيار الصحيح**؛ (6) إضافات الواجهة (`replace_pages`, `find_by_identity`, `verify_state`) لازمة لـ B6/B9؛ (7) اختبارات Stub للمستودعات — انظر §4-س1؛ (8) عالم Tenancy ثابت في conftest — ممارسة جيدة.

---

## 4. ردود على أسئلة المنفذ

| # | السؤال | الرد |
|---|---|---|
| س1 | اختبارات الـStub للمستودعات: تُدمج أم يُرفع الاعتماد على التكامل؟ | **تُدمج.** تختبر تركيب العبارات والتحويل صف⇄دومين بسرعة، والتكامل يختبر السلوك الفعلي — طبقتان متكاملتان. القاعدة من الآن: أي منطق مستودع جديد يحصل على كليهما. |
| س2 | تفويض `MANAGER ⇒ VIEWER` | **مرفوض حتى وحدة المصادقة** (التنفيذ الحالي `GRANT_ROLES = {OWNER}` صحيح). يُدوَّن في `DEFERRED_DECISIONS` عند تحديث Hub. |
| س3 | تدوير التوكن | ✅ المخطط يوافق ويرفع الطلب للمالك — §7-2. |

---

## 5. ملاحظات غير حاجبة (مرحَّلة إلى SPRINT-04)

| # | الملاحظة | المعالجة |
|---|---|---|
| N1 | `tenant_id` كمعامل استعلام/جسم هو **تحديد هوية ذاتي** (أي عميل يدّعي أي UUID). كان مقبولاً صراحةً في التوجيه (B4 «مؤقت حتى وحدة المصادقة»)، لكنه الآن **أكبر فجوة أمنية في المنتج** ويجب أن يكون أول ما يُغلق. | **SPRINT-04 محور أساسي:** وحدة مصادقة تستبدل `ActorTenantId` بهوية مُتحقَّق منها من مكان واحد. |
| N2 | `provider_user_name` يُعاد في `ConnectedAccountPublic` — مقصود (بيانات عرض) ومتوافق مع قرار المالك. | لا إجراء. |
| N3 | شجرة التشغيل تحوي `certifi` بـ MPL-2.0 (مقبولة في القائمة البيضاء بوعي). | توثيق في Hub `SECURITY_BASELINE` عند التحديث. |

---

## 6. تقرير المنفذ المؤرشف (§7 من `CURRENT_DIRECTIVE.md` @ `2b1e1fe`) — ملخص موثَّق

- **الحالة:** مكتمل. **Part A** `a985d55` · **Part B** `b712df0` · docs `2b1e1fe`. **PR #7**؛ CI خضراء: push `runs/40979056739`، pull_request `runs/40979061696`.
- **الاختبارات:** 298 وحدة / 17 تكامل (PG 17.11) / 315 إجمالاً، 0 فشل؛ تغطية 99.68%؛ ruff نظيف؛ بوابة التراخيص exit 0 / 37 حزمة.
- **I-B1:** ناجح بصفّين قديمين (`'Tenant A!'`, `'tenant-b'`) + صفحتين، صعود/نزول/صعود، مع حذف التكرار عند النزول (ملاحظة المالك 3).
- **ملاحظات المالك الثلاث:** مطبَّقة (gitleaks token/permissions؛ إعادة الربط تحافظ على الاختيار وتُحدّث التوكنات؛ DELETE للتكرارات قبل القيد القديم).
- **اختبارات حُذفت/أُعيدت تسميتها:** `test_alembic_0002_upgrade_downgrade_roundtrip_with_null_rows` حُذف (تغطيته مضمونة في `test_alembic_upgrade_head_and_downgrade_base` وI-B1)؛ إعادة تسمية ثلاثة اختبارات مستودع لنطاق العلامة؛ `test_router.py` أُعيد كتابته لنطاق العلامة مع الإبقاء على أسماء اختبارات SPRINT-02 المحفوظة.
- **انحرافات:** 8 (مُقيَّمة في §3). **أسئلة:** 3 (مُجابة في §4).
- **ملاحظة تشغيلية:** إعادة تهيئة الساندبوكس ×7 خلال السبرنت؛ الدفع الفوري حفظ كل شيء.

---

## 7. القرار وما يلي

1. ✅ **SPRINT-03 مقبول.** يُدمج PR #7 في `main` (المخطط يدمج بتفويض المالك القائم منذ 02.1، ويُبلغ).
2. 🔑 **طلب للمالك:** تدوير توكن GitHub المستخدم في الجلسات (ظهر في الدردشة عدة مرات) — يُستبدل بتوكن جديد يُرسَل مع «تابع» القادمة.
3. 🛡️ **طلب للمالك:** الآن وقد صارت الفحوص الخمسة ظاهرة — تفعيل حماية `main` (Require PR + الفحوص الخمسة Required + up-to-date).
4. ➡️ **SPRINT-04** يُصدَر في `CURRENT_DIRECTIVE.md` بالتزامن (PR #8): المحور الأول **المصادقة** (إغلاق N1).
