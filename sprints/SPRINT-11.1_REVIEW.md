# 📋 SPRINT-11.1-CLOSURES-AND-PROVIDER-CATALOG — تقرير مراجعة المخطط المستقلة

> **PR [#42](https://github.com/belalalibb/ai_agancy_markting/pull/42)** (`work/implementation @ 9d944ce` → `main @ 7e7542e`) · الكوميتات: Part A `67dceb4` · Part B `4c396c8` · Part C `2d26c28` · docs `9d944ce`.
> **القرار (D-063): Path 1 — قُبل ودُمج.** كل بوابات §3 من التوجيه خضراء على PG 17.11 UTF8 ببيئة أُعيد بناؤها (×4 خلال هذه المراجعة بسبب إعادة ضبط الساندبوكس). الفرضيات الرقابية الست **H1–H6 كلها ثبتت بالتجربة الحية** (§3) — **لا واحدة منها مانعة للدمج** لأن H3–H6 محصورة بالكامل خلف الافتراضيين `FEATURE_PROVIDER_CATALOG_ENABLED=false` + `PLATFORM_ADMIN_USER_IDS=[]`، وH1/H2 تمسّان مساراً لا يُطرَق إلا بتعليق باقة يدوياً عبر مشرف المنصة (ولا مشرف مُعرَّف بعد). تُغلق كلها في **SPRINT-12 Part A** قبل أي ميزة جديدة.
> **تحذير تشغيلي ملزم حتى دمج SPRINT-12 Part A:** لا تفعيل للكتالوج، لا تعريف `PLATFORM_ADMIN_USER_IDS`، لا تعليق باقات (`suspend`) على علامة لها webhook واتساب حيّ.

---

## 1. ما تحقق منه المخطط فعلياً (PostgreSQL 17.11 UTF8 + pgvector من حزم PGDG على Debian 13؛ `initdb -E UTF8 --locale=C.UTF-8`؛ `pip install -e ".[dev]"`؛ Python 3.13)

| # | الفحص | النتيجة على `9d944ce` | الحكم |
|---|---|---|---|
| 1 | `ruff check src tests alembic scripts` + `ruff format --check …` (نطاق CI) | 0 / 0 على `9d944ce` **و** على أشجار `67dceb4` / `4c396c8` / `2d26c28` (worktrees مستقلة) | ✅ |
| 2 | بوابة CI للوحدات **بلا DB** `pytest -m "not integration" --cov=src --cov-fail-under=95 --cov-precision=2` | **1,346 passed / 1 skipped · 95.05%** ⇒ `Required test coverage of 95% reached` · exit 0 (12:01؛ Python 3.13 محلياً، CI على 3.12 أخضر) | ✅ |
| 3 | بوابة CI للتكامل `pytest -m integration` على PG 17 (`ai_growth_os_test`) | **121 passed / 0 failed** · 1,346 deselected (2:34) — مطابق لتقرير المنفذ | ✅ |
| 4 | `alembic upgrade head ⇒ downgrade 0014 ⇒ upgrade head` على قاعدة نظيفة | `0015` ⇒ `0014` ⇒ `0015`؛ الجداول `ai_providers`/`ai_models`/`ai_routing` تختفي (0) وتعود (3) و`ai_cost_log` يبقى؛ **12 قيداً مسمّى** (`uq_ai_providers_slug`, `ck_ai_providers_slug`, `ck_ai_providers_kind`, `ck_ai_providers_one_credential`, `uq_ai_models_provider_name`, `ck_ai_models_capability`, `ck_ai_models_price_shape`, `ck_ai_models_prices_non_negative`, `ck_ai_models_max_audio_seconds`, `uq_ai_routing_purpose_priority`, `uq_ai_routing_purpose_model`, `ck_ai_routing_purpose`) + FKان CASCADE = 14 قيداً غير PK قبل وبعد؛ `server_encoding = UTF8` | ✅ (المنفذ أبلغ 12 = 11 + `uq_ai_routing_purpose_model` — مطابق) |
| 5 | greps الملزمة §4-3 الستة | 1 `learning-consent` في `entitlements/` = **0** · 2 `tenancy.infrastructure` خارج القائمة = **0** · 3 `src.modules.entitlements` خارج القائمة البيضاء = **0** · 4 `ai_gateway.catalog` خارج `ai_gateway/`+`main.py` = **0** · 5 `get_secret_value()` في `catalog/` بلا `credential` = **0** · 6 `git diff origin/main -- contract.py public_fetch.py pyproject.toml` = **0 سطر** | ✅ |
| 6 | `ErrorCategory` | **12** (استيراد مباشر من `contract.py`) | ✅ |
| 7 | `PROTECTED_ROUTE_COUNT` / العامة | **77** (`tests/test_auth_guard.py:80`, `tests/modules/content/test_router.py:1475`) · `PUBLIC` = **6** مسارات (+2 health) بلا تغيير | ✅ |
| 8 | حجم `src/` لكل Part (`git diff --numstat -- src`) | A **+361 −69** · B **+1,160 −24** · C **+871 −6** ⇒ B+C = **2,031 خام** (مطابق لتقرير المنفذ؛ 1,632 كوداً فعلياً) | ✅ **قرار المالك: مقبول دون تقسيم — Part C يبقى كاملاً** |
| 9 | كوميت docs `9d944ce` يمس الكود؟ | `CURRENT_DIRECTIVE.md` + `WORK_STATE.md` فقط؛ `git diff 2d26c28 9d944ce -- src tests alembic` = 0 | ✅ |
| 10 | CI على GitHub | `9d944ce`: **5/5 ×2** (lint/unit/integration/secrets/licenses). الكوميتات A/B/C بلا check-runs (دُفعت دفعة واحدة؛ CI على الرأس فقط) — عُوِّض بالبند 1 (ruff لكل شجرة) | ✅ |
| 11 | PR #42 | `mergeable=true`, `mergeable_state=clean` | ✅ |

---

## 2. تقييم تقرير المنفذ (§6 @ `9d944ce`) — الانحرافات الـ13

| # | الانحراف | حكم المخطط |
|---|---|---|
| A-1 | `check_counter` عبر منفذ يملكه المستهلك (`content/entitlements_port.py`, `whatsapp/entitlements_port.py`) ويُربط في `main.py:197–200` | ✅ **مقبول — هو التطبيق الصحيح لـD-016.** **لكن** تنفيذه الحالي سبب H1: `PlanCounterGuard._ensure` (`entitlements/dependencies.py:82–88`) يعيد رفع `EntitlementLimitError(plan_inactive)` خاماً ولا يوجد `exception_handler` لها في `main.py` ⇒ 500. يُغلق في SPRINT-12 A-1 |
| A-2 | webhook يخزّن الرسالة ويعلّم `quota_exceeded` في الحمولة بلا عمود جديد | ✅ مقبول (`test_a2_fifty_first_…` على PG يثبت 200 + تخزين). الثغرة الوحيدة هي `plan_inactive` (H1) لا الحصة |
| A-3 | `--cov-precision=2` في `ci.yml` **و** `pyproject.toml` | ✅ مقبول — يغلق F1 من مراجعة SPRINT-11 |
| B-1 | `PURPOSE_ALIASES` على `gateway.Purpose` الحيّ | ✅ مقبول (§5-5) |
| B-2 | حذف `catalog_disabled` | ✅ مقبول (سمح به §2-B-6) |
| B-3 | `NoRouteError`/`CredentialUnavailableError` ترثان `BudgetGuardError` ⇒ 503 | ✅ مقبول — ثبت حياً: probe على مزوّد `credential_ref` غير موجود ⇒ **503 `provider_credential_unavailable`** لا 500 |
| B-4 | `brand_id=None` لـ`admin_probe` فقط | ✅ مقبول (`test_c14_…null_brand_on_pg`) |
| B-5 | `get_catalog_reader` يُرجع `None` قبل أي استيراد للكتالوج عندما المفتاح معطَّل | ✅ مقبول — هو ما يجعل H3/H5/H6a **محصورة** خلف المفتاح (D-076) |
| C-1 | خطآن 422 `catalog_invalid_pricing` / `catalog_unknown_purpose` بلا `ErrorCategory` جديد | ✅ مقبول |
| C-2 | 400 من الأساسي ⇒ لا fallback + استرداد الحجز | ✅ مقبول — متسق مع SPRINT-11 B-1 |
| C-3 | الواجهة HTTP تعيد محاولة 503 مرة قبل انتقال الكتالوج | ✅ مقبول (`test_c12`) |
| C-4 | Zero-Secret يسمح بـ`max_tokens_cap`/`max_tokens` | ✅ مقبول. **لكن** `max_tokens_cap` **يُخزَّن ولا يُطبَّق** (H6a) — SPRINT-12 A-6 |
| C-5 | `probe` بـ`limits_reader=None` + سقف المنصة $25 | ✅ مقبول — معدَّل بـ`PROBE_LIMIT` لكل مشرف |

**ما طلبه المنفذ من المالك:** (1) صمّام §5-6 ⇒ **المالك قرّر قبول C كاملاً**. (2)(3) تفعيل الكتالوج و`PLATFORM_ADMIN_USER_IDS` ⇒ **مؤجَّلان إلزامياً حتى دمج SPRINT-12 Part A** (§4). (4) تدوير التوكن ⇒ ما زال مفتوحاً.

---

## 3. ما أثبته المخطط بالتجربة (مِجسّ مستقل `tests/integration/_probe_h.py` على PG 17 UTF8 — **حُذف قبل أي `git add`**)

كل سطر = نداء HTTP حقيقي عبر `TestClient(raise_server_exceptions=False)` على التطبيق الكامل بقاعدة حقيقية، مع `respx` للمزوّد الخارجي فقط.

| # | الفرضية | ما حدث فعلاً | الوسم | الخطورة |
|---|---|---|---|---|
| **H1** | `plan_inactive` يسقط خارج عقد 402 | علامة مُعلَّقة (`brand_entitlements.is_active=false`): **webhook واتساب لمحادثة جديدة ⇒ 500** «Internal Server Error» والرسالة **لا تُخزَّن** (`wa_messages` 1 ⇒ 1، `wa_conversations` 1 ⇒ 1 — rollback كامل) بينما محادثة قائمة ⇒ 200. **`approve_item` ⇒ 500 · `send_text` ⇒ 500 · `suggest_reply` ⇒ 500.** الاستثناء عند حدّ الخادم: `src.modules.ai_gateway.budget_guard.EntitlementLimitError: plan limit reached: plan_inactive`. السبب: `entitlements/dependencies.py:86` يعيد رفعها خاماً وتعليق «keeps its 429 contract» خاطئ — لا معالج لها في `main.py` (grep `add_exception_handler\|EntitlementLimitError` = 0)؛ `budget_error_to_http` (`http_errors.py:35–37`) موجود لكن المسارات الخمسة لا تمرّ به. `GET /entitlement` يعرض `is_active:false` صحيحاً. | `[VERIFIED on PG17]` | 🔴 **عالية** — Meta تعيد إرسال webhook عند 5xx حتى تعطيل الاشتراك؛ **غير مانعة**: التعليق لا يحدث إلا بفعل يدوي من مشرف المنصة ولا مشرف مُعرَّف |
| **H2** | فحص الحصة قبل التفويض في `approve_item` | غريب (STRANGER) على علامة ليست له: مع حصة متاحة ⇒ **404 `brand_not_found`** (صحيح)؛ مع `monthly_posts=0` ⇒ **402 `entitlement_quota_exceeded {limit:0, used:0}`** — ونفس 402 لعنصر وهمي `…dead`. المالك ⇒ 402 (صحيح). السبب: `content/router.py:498–502` يستدعي `quota.ensure_can_approve_post(brand_id)` **قبل** `_act(...)`. | `[VERIFIED on PG17]` | 🟡 متوسطة — **أوراكل** يكشف حالة باقة علامة أخرى لأي مستخدم مصادَق يعرف UUID العلامة؛ لا يكشف بيانات |
| **H3** | Brand Brain يتجاوز الكتالوج | كتالوج مفعَّل + مسارات `content_generation` و`brand_diagnosis` + **لا مفتاح في `.env`**: `content/generate` ⇒ **200** عبر الكتالوج (نداء واحد، صف `content_draft/h-model/ok`)، بينما **`brain/diagnose` ⇒ 503 `ai_provider_not_configured`** و`brain/content-plan` ⇒ 503؛ نداءات الكتالوج من brain = **0**. السبب: `brand_brain/router.py:223–247` يبني `get_ai_gateway(request, settings)` مباشرة ولا يمرّ بـ`get_budget_guard`. | `[VERIFIED on PG17]` | 🟡 متوسطة — D-077 غير مكتمل: مسارات `brand_diagnosis`/`competitor_gap`/`tone_profile` في `/admin/ai/routing` **بلا أثر**؛ وعند الانتقال الكامل للكتالوج يتعطل Brand Brain |
| **H4** | حارس المضيف يفحص الـIP الحرفي فقط | `POST /admin/ai/providers` بـ`base_url`: `http://ollama:11434/v1` ⇒ **201** · `http://127.0.0.1.nip.io:11434/v1` ⇒ **201** · `http://169.254.169.254.nip.io/v1` ⇒ **201** · `http://metadata.google.internal/v1` ⇒ **201** · `http://0177.0.0.1/v1` ⇒ **201** · `http://2130706433/v1` ⇒ **201** (كلاهما = 127.0.0.1 بترميز ثماني/عشري — `ipaddress.ip_address` ترفضهما فتُعامَلان كـDNS) · بينما `10.0.0.5` / `127.0.0.1` / `localhost` / `[::1]` ⇒ **422** صحيحاً. 6 صفوف خُزِّنت. السبب: `catalog/service.py:142–147` `except ValueError: return key  # DNS name` ولا فحص عند الاتصال؛ `netguard.is_global_address` **غير مستخدم** في الكتالوج (`public_fetch.py:235` يستخدم نظيره عند الـresolve). | `[VERIFIED on PG17]` | 🟠 **متوسطة–عالية** (SSRF من مشرف المنصة إلى الشبكة الداخلية/metadata عبر `/admin/ai/probe` — **يعمل والمفتاح معطَّل**) — **محصورة بشرط واحد:** `PLATFORM_ADMIN_USER_IDS=[]`. نفس عائلة B2-أ التي أُغلقت لـ`public_fetch` في SPRINT-11 |
| **H5** | `CatalogPriceTable` تُفهرس بالاسم فقط وتتجاهل `enabled` | المزوّد A **معطَّل** + `shared-model` مسعَّر **0** · المزوّد B مفعَّل + `shared-model` **غير مسعَّر** · المسار ⇒ B. `content/generate` ⇒ **200** (نداء إلى B) وصف `ai_cost_log`: `shared-model / ok / 0.000000` — **لم يُرفض كـ`ai_model_unpriced`** لأن `_by_name` (`service.py:197–203`) فضّلت المدخل المسعَّر بغضّ النظر عن `enabled`/`provider_id`. الـprobe على B ⇒ `ok:true, estimated_cost_usd:null` بينما صف `ai_cost_log` للـprobe = `0.000000` — **تضارب** بين مصدرَي التسعير. | `[VERIFIED on PG17]` | 🟡 متوسطة — يكسر D-075 «fail-closed pricing» عند تضارب أسماء النماذج بين المزوّدين (واقعي: `gpt-4o-mini` عند OpenAI وOpenRouter وAzure) |
| **H6a** | `max_tokens_cap` يُخزَّن ولا يُطبَّق | `PATCH /models/{id} {max_tokens_cap:5}` ⇒ 200؛ `content/generate` عبر الكتالوج أرسل **`max_tokens: 2000`**؛ `probe {max_tokens:64}` أرسل **64**. `max_tokens_cap` لا يظهر إلا في `admin_service.py:178,202–203` (التخزين). | `[VERIFIED on PG17]` | 🟡 متوسطة — المشرف يظن أن السقف يحمي الميزانية وهو لا يفعل |
| **H6b** | `ProviderPatch` يقبل `api_key` + `credential_ref` معاً | `PATCH {api_key, credential_ref:"ENV_REF_X"}` ⇒ **200** `has_credential:true` بينما الصف: `credential_cipher IS NULL`, `credential_ref='ENV_REF_X'` — المفتاح المرسَل **أُسقط بصمت** (`admin_service.py:144–154`)؛ الـvalidator موجود في `ProviderCreate` فقط (`schemas.py:43–46`). | `[VERIFIED on PG17]` | 🟢 منخفضة — مُضلِّل لا تسريب؛ `ck_ai_providers_one_credential` يحمي القاعدة |
| **H6c** | لا `TOKEN_ENCRYPTION_KEY` | `POST /providers` بمفتاح ⇒ **404 `catalog_not_found` «token encryption is not configured»** (رمز خاطئ دلالياً؛ المنطقي 503). لا تسريب في `GET /providers`. | `[VERIFIED on PG17]` | 🟢 منخفضة (التطبيق يرفض الإقلاع بلا مفتاح تشفير منذ SPRINT-08 A-1) |
| **H6d** | probe على `audio` + توجيه نموذج صوتي لغرض نصي | `whisper-x capability=audio` ⇒ 201؛ `probe` ⇒ **503 `ai_model_unpriced`** بلا نداء (fail-closed صحيح)؛ `PUT /routing/content_generation {models:[audio]}` ⇒ **200** — يُقبل ثم يفشل وقت التنفيذ. | `[VERIFIED on PG17]` | 🟢 منخفضة — تحقق شكلي ناقص |
| **X1** | B-3: `credential_ref` غير موجود | probe ⇒ **503 `provider_credential_unavailable`** — لا 500 | `[VERIFIED on PG17]` | ✅ |
| **X2** | عزل H3/H5/H6a خلف المفتاح وH4/H6b-d خلف `PlatformAdmin` | الافتراضيان ⇒ `/admin/ai/*` 403؛ `get_catalog_reader ⇒ None` قبل أي استيراد (`test_isolation.py`)؛ مستهلكو الـgateway يسلكون مسار SPRINT-11 حرفياً | `[VERIFIED in Code]` + مجموعة التكامل | ✅ أساس قرار الدمج |

---

## 4. القرار وما يُفتح الآن

**دمج PR #42 ⇒ `main`** (merge commit؛ المخطط هو الدامج D-063) + حذف `wip/sprint11.1-chain` و`wip/sprint11.1-squash`.

**SPRINT-12 Part A (إغلاقات ملزمة قبل أي ميزة):**

| بند | الإغلاق | المصدر |
|---|---|---|
| A-1 | **`[DECISION]` دلالة `plan_inactive`:** webhook واتساب ⇒ **يخزّن الرسالة والمحادثة ويعيد 200 لـMeta** ويعلّم المحادثة في الحمولة `plan_inactive` (لا 5xx أبداً من webhook موقَّع)؛ كل مسار **صادر** (`approve_item`, `send_text`, `send_template`, `suggest_reply`, `generate`, brain) ⇒ **402 `entitlement_plan_inactive` `{plan, active_until}`** (لا 429 — 429 محجوز للحدود الزمنية؛ العميل يميّز «ادفع» عن «انتظر»). اختبار PG لكل مسار + webhook | H1 |
| A-2 | `approve_item`: `ensure_can_approve_post` **بعد** التفويض (404/403 قبل 402) | H2 |
| A-3 | `get_brain_gateway` ⇒ `get_budget_guard` (الكتالوج) عندما لا `X-AI-Key`؛ `X-AI-Key` يبقى بلا كتالوج | H3 |
| A-4 | `validate_provider_host`: مضيف DNS يُحلّ (`getaddrinfo`) ويُفحص بـ`netguard.is_global_address` عند الإنشاء/التعديل **وعند الاتصال** (rebinding)؛ رفض `0177.0.0.1`/`2130706433` | H4 |
| A-5 | `CatalogPriceTable` بمفتاح `(provider_id, model_name)` وتجاهل `enabled=false`؛ مصدر تسعير واحد للـprobe والـcost_log | H5 |
| A-6 | تطبيق `max_tokens_cap` في `_attempt` و`probe`؛ validator لـ`ProviderPatch`؛ `_cipher` بلا تشفير ⇒ 503؛ `PUT /routing` يرفض capability لا يطابق الغرض | H6a–d |

**حجم Part A المتوقع:** ≈ 250–350 سطر `src/` — ضمن سقف G6 لـSPRINT-12.

---

## 5. ملاحظة حوكمة

- الفحص الساكن اكتشف H1 كـ«429 بدل 402»؛ المِجسّ الحي أثبت أنه **500 + فقدان الرسالة**. تبقى قاعدة المخطط: لا حكم على فرضية بلا نداء HTTP حقيقي على PG.
- CI على GitHub غطى الرأس فقط عند الدفع الدفعي؛ يُطلب من المنفذ في SPRINT-12 **دفع كل Part فور اكتماله**.
- التوكن `ghp_…` ظهر في هذه المحادثة **≥ 16 مرة** — التدوير ما زال مفتوحاً على المالك.
