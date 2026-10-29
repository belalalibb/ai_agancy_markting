# 📦 جرد المصادر والأصول السابقة (Source Inventory)
**تاريخ الحصر والتدقيق:** 28 أكتوبر 2026  
**النطاق الجغرافي للمسح:** المجلد الجذري `d:\projects\AI_markting` وكافة مجلداته الفرعية.

---

## 🧭 1. نظرة عامة على محتويات مساحة العمل

تم العثور على **22 مجلداً فرعياً** بالإضافة إلى 3 ملفات أرشيف مضغوطة. يمثل كل مجلد مرحلة تاريخية أو تجربة معينة لبناء أجزاء من المنظومة (منذ أواخر 2024 وحتى أكتوبر 2026).

| الرقم | اسم المجلد / الأصل | المسار الفعلي | الدور والوظيفة الأساسية | التقنيات المستخدمة | حجم الأصول والكود |
|---|---|---|---|---|---|
| 1 | `ai_agents` | `d:\projects\AI_markting\ai_agents` | نموذج أولي تجريبي شامل (All-in-one prototype) لتوليد المحتوى والحملات وقاعدة بيانات SQLite | Python, Flask, SQLite, Groq API, HuggingFace | ملف `main.py` (75 KB) + قاعدة بيانات 45 KB |
| 2 | `AI_FULL` | `d:\projects\AI_markting\AI_FULL` | نموذج SaaS سابق تم نشره تجريبياً على منصة Novita sandbox مع واجهة ويب ومصادقة | Python, Flask, SQLAlchemy, SQLite, HTML | 13 ملف كود (1,214 سطر) + `PROJECT_STATUS.md` |
| 3 | `ai_mdule-master` | `d:\projects\AI_markting\ai_mdule-master` | وحدات اتصال وبرومبتات هندسية متقدمة لمزودي الذكاء الاصطناعي (Gemini, DeepSeek) وطرق تجاوز وربط | Python, Reverse Engineering, Session Management | 56 ملف كود (7,278 سطر) + 8 ملفات Markdown |
| 4 | `AI_MY_AGENT` | `d:\projects\AI_markting\AI_MY_AGENT` | هيكل قديم لبوت تسويقي (AI Marketing Bot) مع توثيق أولي | Python, Bot Framework | 38 ملف بايثون + 4 ملفات MD |
| 5 | `AI_my_v` | `d:\projects\AI_markting\AI_my_v` | هيكل متقدم لمعمارية Microservices/FastAPI تضم مجلدات منفصلة للـ Routers والـ Services والنماذج | Python, FastAPI, React (Docs), Pydantic | 18 ملف كود (2,547 سطر) + 40 ملف Markdown |
| 6 | `containt_creator_agent` | `d:\projects\AI_markting\containt_creator_agent` | وكيل متخصص في إنشاء المحتوى التسويقي | Python | 7 ملفات بايثون |
| 7 | `conversation_agent` | `d:\projects\AI_markting\conversation_agent` | مجلد محجوز لوكيل المحادثة (فارغ حالياً، نُقل محتواه إلى Qevion) | - | 0 ملفات (فارغ) |
| 8 | `data_extractor_agent` | `d:\projects\AI_markting\data_extractor_agent` | مجلد محجوز لوكيل استخراج البيانات (فارغ حالياً) | - | 0 ملفات (فارغ) |
| 9 | `data_scraping` | `d:\projects\AI_markting\data_scraping` | سكريبتات استخراج بيانات من فيسبوك وغيت هاب وكاجل | Python, Scraping, APIs | 7 ملفات بايثون |
| 10 | `downloaded_files` | `d:\projects\AI_markting\downloaded_files` | ملفات وملفات قفل مؤقتة سابقة | Lock files | 2 ملف |
| 11 | `first_lunch` | `d:\projects\AI_markting\first_lunch` | يحتوي على أهم نموذجين حديثين للواجهات: `brandmind-ai` و `ai-marketing-platform` | Next.js 14, TypeScript, Supabase, Cloudflare Workers | 50 ملف كود (10,131 سطر) + واجهات كاملة |
| 12 | `image_generator_agent` | `d:\projects\AI_markting\image_generator_agent` | مجلد محجوز لوكيل توليد الصور (فارغ حالياً، نُقل إلى JS_AI_CORE) | - | 0 ملفات (فارغ) |
| 13 | `JS_AI_CORE` | `d:\projects\AI_markting\JS_AI_CORE` | مركز أبحاث وتطوير ضخم لمزودي الذكاء الاصطناعي ومعمارية الفيديو (ComfyUI Video SaaS) | Python, ComfyUI, FLUX, Wan2.1, Multi-Provider | 52,649 ملف (يشمل مكتبات وأدوات الفيديو) |
| 14 | `life_sembel` | `d:\projects\AI_markting\life_sembel` | مشروع متكامل يضم واجهة مستخدم ضخمة ومكتبات `my_ai` السابقة | Python, HTML, TypeScript, Cloudflare Workers | 128 ملف كود (35,473 سطر) |
| 15 | `socialidias` | `d:\projects\AI_markting\socialidias` | خزانة الوثائق والدراسات الاستراتيجية والتقارير الفنية الأكثر عمقاً (تقرير 25 أكتوبر 2026) | Markdown, PDF, Text | 18 ملف Markdown (أكثر من 500 KB توثيق) |
| 16 | `Text-to-Speech_agent` | `d:\projects\AI_markting\Text-to-Speech_agent` | مجلد محجوز لوكيل تحويل النص إلى صوت (فارغ حالياً) | - | 0 ملفات (فارغ) |
| 17 | `ult_agent_agent` | `d:\projects\AI_markting\ult_agent_agent` | المستودع الأكثر نضجاً ومعمارية: `ai-growth-os-main`، دستور العائلة F0-F4، الشريحة الأولى المعتمدة | Python 3.12+, Pydantic v2, pytest, Monorepo | 114 ملف كود (23,085 سطر) + 45 ملف Markdown |
| 18 | `vedio_generator_agent` | `d:\projects\AI_markting\vedio_generator_agent` | مجلد محجوز لوكيل الفيديو (فارغ حالياً، دُمج في E3) | - | 0 ملفات (فارغ) |
| 19 | `whatsapp_ai` | `d:\projects\AI_markting\whatsapp_ai` | الإصدار الأول لنظام أتمتة رسائل واتساب مع واجهة تحكم ويب | Node.js, Express, WhatsApp Web JS | 47 ملف كود (13,610 سطر) |
| 20 | `whatsapp_ai_v2` | `d:\projects\AI_markting\whatsapp_ai_v2` | الإصدار الثالث المتطور (OmniReply AI Pro v3) مع حماية ضد الحظر وتكامل Perplexity AI | Node.js, HTML, JS, Perplexity API | 13 ملف كود (4,809 سطر) + دليل مستخدم |
| 21 | `whats_app_v3` | `d:\projects\AI_markting\whats_app_v3` | مجلد فارغ كان مخصصاً لترقية الواتساب | - | 0 ملفات (فارغ) |
| 22 | `work_ai` | `d:\projects\AI_markting\work_ai` | مشروع `marketing-saas` يضم هيكل منظم لأدوات التحقق وإدارة الملفات والعملاء المحتملين | Python, Flask, Helpers | 77 ملف بايثون (5,392 سطر كود) |

---

## 🗄️ 2. الأرشيفات المضغوطة (Archives)

- `ai_mdule-master.zip` (598 KB): نسخة احتياطية من مكتبة مزودي الذكاء الاصطناعي.
- `webapp (1).tar.gz` (47 KB): حزمة تطبيق ويب مضغوطة.
- `work_ai.rar` (53 KB): أرشيف مضغوط لمشروع work_ai.
