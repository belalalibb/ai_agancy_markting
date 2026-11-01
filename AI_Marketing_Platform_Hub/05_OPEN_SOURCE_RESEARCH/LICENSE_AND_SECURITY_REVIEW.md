# ⚖️ مراجعة التراخيص والأمان القانوني (License & Security Review)
**تاريخ الاعتماد:** 28 أكتوبر 2026  
**المرجعية:** القرار D-054 وتوجيهات التدقيق الفني الشامل

---

## 🚦 1. تصنيف مناطق التراخيص في المنظومة (License Zones)

| المنطقة | نوع الترخيص | المشاريع النموذجية | الحكم والسياسة المعتمدة |
|---|---|---|---|
| **المنطقة الخضراء (Green Zone)** | تراخيص متساهلة (MIT / Apache-2.0 / BSD / PostgreSQL) | Pydantic AI, Umami, LiveKit, faster-whisper, imgproxy (**Apache-2.0** — صُحِّح 2026-11-01), `harry0703/MoneyPrinterTurbo` (MIT), `krillinai/OpenCreator` (Apache-2.0) — *(Temporal وLiteLLM رخصتهما خضراء لكنهما **مرفوضان في V1** قراراً معمارياً D-056/D-057)* | **مسموح بها تماماً:** يمكن استخدامها وتضمين مكتباتها وبناء الكود التجاري المغلق فوقها دون أي قيود. |
| **المنطقة الصفراء (Yellow Zone)** | ترخيص LGPL أو تراخيص المصدر المتاح (Source-Available) | FFmpeg (LGPL runtime), Medusa core | **مسموح بها بشروط:** استخدامها كـ Runtime منفصل أو استدعاءات خارجية عبر الشبكة (Dynamic link)، مع حظر دمج كودها المصدري. |
| **المنطقة الحمراء (Red Zone)** | تراخيص فيروسية مقيدة للشبكة (AGPL-3.0 / GPL-3.0) | **Postiz (`gitroomhq/postiz-app` — AGPL-3.0 مؤكَّد 2026-11-01 ⇒ REJECT)**, Dub, Plausible, listmonk, ComfyUI core | **حظر التضمين في الكود التجاري:** يُمنع منعاً باتاً استيراد كودها في الـ Monorepo. تُستخدم فقط كخدمة شبكية مستقلة تماماً دون تعديل كودها، أو كمرجع تصميمي (Reference Only). |

---

## 🛡️ 2. مصفوفة الأمان القانوني وضوابط التصدير
- **إسناد الحقوق (Attribution):** الاحتفاظ بملفات التراخيص الأصلية للمشاريع المصرحة بتراخيص MIT/Apache في مجلد التبعيات دون تغيير.
- **حماية الأسرار (Clean IP):** عدم نسخ سطور كود مباشرة من مشاريع الـ AGPL؛ صياغة مهايئات المنصات بلغة بايثون من الصفر بالاعتماد على الوثائق الرسمية فقط.


## 🔒 ملحق 2026-11-01 — أحكام REJECT صريحة بعد قرارات المالك Q1–Q17

| المشروع | الرخصة | الحكم | السبب |
|---|---|---|---|
| `gitroomhq/postiz-app` (Postiz) | AGPL-3.0 | **REJECT** | المنطقة الحمراء (D-054): لا استيراد ولا تشغيل كخدمة معدَّلة؛ القراءة التصميمية فقط. |
| `unclecode/crawl4ai` (Crawl4AI) | Apache-2.0 | **REJECT** | ليس لسبب الرخصة بل D-074 (لا كشط) + D-068 (يجرّ `playwright` المحظور في `banned-api`)؛ الجلب العام يبقى `public_fetch.py` بسقف 512 KiB وrobots.txt وفحص `is_global` (SPRINT-11 B2-أ). |
| `BerriAI/litellm` | MIT | **REJECT (V1)** | D-056 مُراجَع (Q7): بوابة داخلية + كتالوج مزودين في قاعدة البيانات (D-077). |
| `temporalio/temporal` | MIT | **REJECT (V1)** | D-057 مُراجَع (Q8): مجدول داخلي + `reap_stale_leases` + dead-letter. |

**أمن أسرار المزودين (D-077/G4):** مفاتيح المزودين المضافة من لوحة الإدارة تُشفَّر فوراً بـ`TokenEncryptionService` (MultiFernet، D-058) أو تُشار إليها بـ`credential_ref` لمتغير بيئة؛ لا تُعاد أبداً في أي استجابة (`has_api_key: bool`)؛ `base_url` https فقط مع فحص `getaddrinfo`+`ip_address.is_global` (استثناء محلي صريح `AI_ALLOW_LOCAL_PROVIDER_HOSTS=true`).
