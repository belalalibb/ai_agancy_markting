# 🤖 استراتيجية مزودي الذكاء الاصطناعي (AI Provider Strategy)
**تاريخ الصياغة:** 28 أكتوبر 2026  
**المرجعية:** القرار المعماري D-053 ومواصفات بوابة LiteLLM Proxy

---

## 🪜 1. سلم مزودي الذكاء الاصطناعي (The Provider Ladder)

تنفيذاً للقرار D-053، يتم تصنيف مزودي النماذج اللغوية والرؤية في سلم تدريجي يحمي هوامش أرباح المنصة ويضمن استمرارية الخدمة:

| الدرجة (Tier) | نوع المزود | النماذج النموذجية | متى يُستخدم في المنظومة | التكلفة على المنصة |
|---|---|---|---|---|
| **Tier 0** | محلي / مفتوح المصدر (Local / OSS) | Ollama, vLLM, DeepSeek-R1 (Distill), Llama-3.3 | المهام الخلفية الحساسة، التلخيص السريع، والتصنيف الأولي للبيانات. | تكلفة الخادم فقط ($0 إضافية). |
| **Tier 1** | بوابات ومزودون مجانيون (Free APIs) | Groq Free Tier, Pollinations, Cohere Free | العروض التجريبية المجانية، النماذج الأولية، والردود السريعة جداً. | $0 (ضمن حصص الاستخدام المجانية). |
| **Tier 2** | حصص مجانية تجارية (Free-Tier Commercial) | Google Gemini 2.5 Flash / Pro, Perplexity (Free limits) | صياغة المحتوى الأساسي للعملاء المشتركين في الباقات الابتدائية. | منخفضة جداً أو مجانية. |
| **Tier 3** | نماذج تجارية منخفضة التكلفة | DeepSeek-V3 API, Claude 3.5 Haiku, GPT-4o-mini | العمليات الإنتاجية اليومية للمشتركين (كتابة المنشورات، الهاشتاجات، الردود الذكية). | ~$0.0002 إلى $0.001 لكل طلب. |
| **Tier 4** | نماذج متميزة / مفتاح العميل (Premium / BYOK) | Claude 3.5 Sonnet, GPT-4o, Midjourney, Kling, Veo 2 | الفيديوهات فائقة الجودة، التحليلات الاستراتيجية الكبرى، واستدعاءات الصوت اللحظي. | **يدفعها العميل مباشرة بمفتاحه (BYOK)** أو رصيد مدفوع بهامش ربح. |

---

## 🔄 2. آليات التوجيه والتحوط (Routing & Fallback Logic)

- **الكاش الدلالي (Semantic Caching):** تخزين نتائج الاستعلامات المتشابهة في Redis؛ طلبات المحتوى المتكررة (مثل: "اكتب منشور تهنئة بالعيد لمطعم") تُسترجع من الكاش في أقل من 50ms وتوفر 100% من تكلفة النموذج.
- **التبديل التلقائي عند التعطل (Automatic Circuit Breaker & Fallback):**
  إذا فشل المزود الأساسي (مثلاً Groq أو DeepSeek بسبب Rate Limit أو Timeout خلال 2 ثانية)، يُحول الطلب تلقائياً وبشكل غير محسوس للمستخدم إلى المزود الاحتياطي (Gemini Flash ثم OpenAI mini).

---

## 🧱 3. عقد المزوّد الواحد ومسارا المفاتيح — الرأي المعماري الصريح للمخطط (2026-10-30، D-067/D-068)

> **السؤال:** كيف نحقق «عقد مزوّد موحَّد + تعلّم داخلي + منع التجاوز + فصل مفاتيح المنصة عن مفاتيح المشتركين» بأنظف وأبسط طريقة داخل منظومتنا، مستفيدين من `general_ai_core` دون استيراد تعقيده؟

### 3-أ. ما قرأه المخطط في `general_ai_core` فعلاً (لا على الوصف)
- `gateway-service/docs/CONTRACT.md`: نموذج ثلاثي الطبقات — **Layer 1** حزمة المزوّد حرّة تماماً (أي عدد ملفات/SDK/جلسات/حسابات)، **Layer 2** واجهة إلزامية تترجم «ما حدث داخلياً» إلى **Layer 3** عقد ثابت: مغلّف طلب/استجابة، 8 عمليات، 12 فئة خطأ «لا تُمدَّد أبداً»، شكل `Usage`، ترويسات `X-Gateway-Secret`/`X-Route-Token`. قاعدة ذهبية: **طلب واحد ⇒ استجابة قياسية واحدة** مهما تعدّدت النداءات الداخلية.
- `gateway/credentials.py`: `user_key` يُحلّ على جانب المنصة ويعبر داخل المغلّف ذاكرةً فقط؛ `platform` يُحلّ داخل الواجهة باسم المزوّد ولا يأتي من الطلب أبداً؛ عدم تطابق النمط مع التعريف = `bad_request`.
- `evidence/error_classification_map.md`: التصنيف مُثبَت **بإعادة تشغيل أشكال فشل حقيقية ملتقَطة** عبر المحوّل (لا بقراءة الكود)؛ فجوة موثَّقة D-01: رفض داخل 200 لا يُصنَّف — درس نحتاجه.
- `core/learning/sanitizer.py` + `gates.py`: منقّي أنماط أسرار (Bearer/PEM/AWS/JWT/sk-/gsk_/AIza/ghp_) يعمل على أي قيمة معرفية قبل التخزين؛ بوابات أهلية (`TrainingEligibilityGate`) وترقية (`PromotionGate`) منفصلتان.
- **الحجم:** 1807 ملفاً، خادم بوابة HTTP مستقل، سجلّات توجيه وسعة وحسابات متعددة، Vault/AppRole، 20 هجرة. **هذا نظام منصة-عامة لا يناسبنا كقاعدة كود**، لكن مبادئه الأربعة (طبقات ثلاث، تصنيف مغلق، مسارا مفاتيح، منقّي قبل التعلّم) ناضجة ومُثبَتة.

### 3-ب. الرأي: «البوابة مكتبة داخل الوحدة، لا خدمة»
**القرار الأنظف لمرحلتنا هو إبقاء البوابة مكتبة Python داخل `src/modules/ai_gateway/` (كما هي الآن) ورفعها إلى العقد الكامل تدريجياً — لا خدمة HTTP مستقلة ولا LiteLLM Proxy مُثبَّتاً الآن.** الأسباب:
1. **المونوليث المعياري (F0) يعطينا الحدود نفسها بتكلفة صفر شبكة:** حدّ الوحدة + قاعدة No-Bypass الآلية (D-068) يحققان «لا طريق للمزوّد إلا عبر البوابة» دون ترويسات سرّية ولا `X-Route-Token` ولا دوران أسرار بين خدمتين. الخدمة المستقلة تُبرَّر فقط حين يستدعيها أكثر من تطبيق — وهذا ليس حالنا قبل V1.
2. **العقد ثابت، التنفيذ يتبدّل:** `AIGateway.complete(CompletionRequest) -> CompletionResult` (الموجود) هو بالفعل Layer 3 لعملية `generate_text`. نضيف إليه الآن ما يفتقده فقط: **فئات الخطأ الـ12** (بدل الثلاث الحالية `AIRateLimitedError/AIUpstreamError/not_configured`)، و`credential_mode` في الطلب/النتيجة، و`Usage` موحَّداً. حين تأتي العمليات السبع الأخرى (صورة/صوت/تضمين/…) تُضاف بأسمائها من القائمة المغلقة — **لا نبنيها الآن** (YAGNI؛ اسمها محجوز في D-067 فقط كي لا يخترع أحد اسماً آخر).
3. **LiteLLM Proxy يبقى خياراً لطبقة التنفيذ (Layer 1) في M7 لا بديلاً للعقد:** يُشغَّل كحاوية خلف واجهة `LiteLLMFacade` إن احتجنا 100 مزوّد وتتبّع تكلفة جاهزاً؛ المحركات لن تلاحظ. هذا يصحّح قراءة D-056: LiteLLM = **تنفيذ محتمل**، العقد = **ملكنا**.

### 3-ج. الشكل النهائي (أقل من 300 سطر في `contract.py`)
```
src/modules/ai_gateway/
├── contract.py      # Layer 3: Operation (Literal مغلق)، ErrorCategory (12)، AIError(category, retryable, retry_after_ms, provider_code مُنقّى)،
│                    #           Usage(prompt_tokens, completion_tokens)، CompletionRequest(+credential_mode)، CompletionResult(+usage, +credential_mode)
├── gateway.py       # AIGateway Protocol (موجود) + Router بسيط: قائمة facades مرتبة؛ Fallback فقط حين category.retryable أو ∈ {provider_unavailable, model_unavailable}
├── definitions.py   # Layer 1 metadata: ProviderDefinition(slug, operations, credential_mode, models) — بيانات لا كود
├── facades/         # Layer 2: fake.py (موجود)، openai_compatible.py (موجود: يُعاد تصنيف أخطائه إلى الـ12)، لاحقاً litellm.py / anthropic.py
├── keys.py          # KeyResolver: platform ⇒ Settings داخل الوحدة فقط؛ user_key ⇒ brand_provider_keys (MultiFernet) يُفكّ لحظة النداء ويُحذف (M7)
└── dependencies.py  # get_ai_gateway (موجود) — المكان الوحيد الذي يلمس Settings.AI_PROVIDER_API_KEY
```
- **No-Bypass قابل للفحص (D-068):** قاعدة `ruff` `flake8-tidy-imports banned-api` تمنع `httpx`/SDKات النماذج خارج `ai_gateway/` و`meta_client.py`، + اختبار يمسح شجرة `src/` للاستيرادات. استبدال المزوّد = ملف facade واحد + `AI_PROVIDER`.
- **الأخطاء تقود الـFallback لا الكود:** كل facade يُلزَم بإرجاع `AIError` بفئة من الـ12؛ `Router` لا يعرف المزوّد، يقرأ `retryable` فقط. اختبار **إعادة تشغيل أشكال فشل حقيقية** (نسخ `evidence/` من `general_ai_core` كمنهج) يُثبّت التصنيف لكل facade.
- **الدرس D-01 يُطبَّق من اليوم الأول:** الرفض داخل 200 (رسالة «لا رصيد» كنصّ) يُكتشف في الواجهة بقواعد بسيطة ويُصنَّف `quota_exceeded` لا نجاحاً مفوتراً.

### 3-د. مسارا المفاتيح والتسعير (بلا تعقيد الآن)
| | `platform` (مفتاح المنصة) | `user_key` / BYOK |
|---|---|---|
| من يملكه | الأدمن؛ متغيّر بيئة/سرّ حاوية | المشترك؛ يُدخله مرة ويُخزَّن MultiFernet في `brand_provider_keys(brand_id, provider_slug, encrypted_key, key_hint, verified_at)` |
| كيف يُحلّ | داخل `keys.py` باسم المزوّد؛ لا يأتي من أي طلب | يُفكّ داخل `keys.py` لحظة النداء ويُحذف في `finally` (نمط توكن الصفحة في SPRINT-05) |
| ما يُسجَّل | `provenance.credential_mode="platform"` + usage | `credential_mode="user_key"` + usage؛ **المفتاح لا يُسجَّل ولا يُعاد** (حتى `key_hint` آخر 4 أحرف فقط) |
| الحدّ/التسعير | ميزانية رموز/يوم/علامة من الباقة (SPRINT-07 A-5 يضع الآلية) | بلا ميزانية رموز منصة؛ اشتراك ثابت للميزات — **فرضية تسعير** تُقرّ في M7 |
| الفشل | `quota_exceeded` ⇒ 429 `generation_budget_exceeded` | `invalid_credential` ⇒ 402/409 `provider_key_invalid` + `required_action=update_provider_key` |

**ما لا نفعله:** لا Vault/AppRole الآن (MultiFernet + تدوير مفاتيح M9 يكفيان لحجمنا)، لا حسابات متعددة لكل مزوّد، لا تسعير ديناميكي، لا خدمة بوابة منفصلة.

---

## 🔁 4. حلقة التعلّم الداخلية المحكومة (D-069) — التصميم الأبسط الذي يصمد

**المبدأ:** نتعلّم من **ما فعله الإنسان بالمخرج** لا من المخرج نفسه. منظومتنا تملك أصلاً أغنى إشارة جودة في السوق: المالك يوافق فوراً / يعدّل ثم يوافق / يرفض بسبب — وكلها مسجَّلة في `approval_events` + المراجعات (SPRINT-06). لا نحتاج «حكماً نموذجياً» (LLM-as-judge) في البداية.

```
/generate ──► provenance{prompt_version, model, credential_mode, channel}          (SPRINT-07: prompt_version)
approve/edit/reject ──► approval_events + diff(revision_n, revision_n-1)
                                  │
                      (دوري، خارج الطلب — M7)
                                  ▼
          learning_signals(prompt_version, model, channel, credential_mode,
                           signal ∈ {approved_unchanged, approved_after_edit(edit_ratio), rejected(reason_class)},
                           features{len, lang, has_cta, has_hashtags}, post_perf? (M8))
                                  │
                                  ▼
          لوحة الأدمن: معدل القبول لكل prompt_version×model×channel  ──► اقتراح نسخة برومبت جديدة
                                  │
                      ترقية بموافقة الأدمن فقط (PromotionGate) ──► prompts.py@N+1
```
- **ما يدخل التعلّم:** إشارات وسمات مجرَّدة فقط، لكل العلامات (platform وBYOK سواء). **النصوص الخام** تدخل مجموعة أمثلة (few-shot/DSPy لاحقاً) فقط بموافقة العلامة `learning_consent=true` وبعد **منقّي** أسرار/PII (أنماط مفاتيح، بريد، هاتف، JWT، أرقام بطاقات) — المبدأ نفسه في `core/learning/sanitizer.py`، نكتبه بـ60 سطراً.
- **ما لا يدخل أبداً:** مفاتيح المشتركين، توكنات Meta، أسماء/معرّفات صفحات، أي قيمة من `Settings`.
- **المحركات مستقلة عن المزوّد:** `prompt_version` مرتبط بالمحرك (`content_draft@1`)، والقياس يقارن النماذج تحت النسخة نفسها ⇒ تبديل المزوّد قرار بيانات لا قرار كود.
- **الحوكمة:** لا تعديل ذاتي للبرومبت في الإنتاج؛ D-055 يمتد إلى تعلّم النظام نفسه. DSPy/أي محسِّن يُقترح ويُراجَع (بند مؤجَّل 13).

---

## 📅 5. التوقيت (ما يُبنى متى)

| المرحلة | ما يُنفَّذ من هذا التصميم | لماذا الآن لا قبل/بعد |
|---|---|---|
| **SPRINT-07 (M4)** | `contract.py` بالفئات الـ12 + `credential_mode` + `Usage`؛ إعادة تصنيف أخطاء الواجهتين الحاليتين؛ قاعدة No-Bypass في CI؛ `prompt_version` في `provenance`؛ ميزانية رموز/يوم/علامة | رخيص (≤ 1 يوم عمل)، يمنع تراكم محركات على تصنيف أخطاء ناقص، ويبدأ جمع الإشارات مبكراً بلا جدول جديد |
| **M7 (SPRINT-10)** | `brand_provider_keys` + `KeyResolver` BYOK؛ `Router` متعدد الواجهات + Fallback بالفئات؛ `learning_signals` + لوحة القبول؛ LiteLLM كواجهة اختيارية | يحتاج مزوّداً حقيقياً ثانياً وباقات؛ قبل ذلك سطح بلا مستخدم |
| **M8–M9** | أداء المنشور كإشارة؛ allowlist خروج شبكي؛ تدوير MultiFernet | يعتمد على `read_insights` وبيئة تشغيل حقيقية |
| **ما بعد V1** | DSPy optimizer على مجموعة منقّاة ≥ 200 إشارة/قناة؛ عمليات غير نصية من القائمة المغلقة | بيانات كافية + محركات فعلية تطلبها |
