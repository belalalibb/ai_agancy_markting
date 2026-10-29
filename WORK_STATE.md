# WORK STATE & EXECUTION POINTER

## سجل حالة العمل ومؤشر التنفيذ النشط

> **الغرض:** نقطة الحقيقة اللحظية لأي وكيل ذكاء اصطناعي أو مهندس يعمل على المستودع. يُسجل بدقة المهمة الحالية، والفرع النشط، وآخر فحص، والخطوة التالية.

---

## 1. ملخص الحالة العامة للمشروع (Project Snapshot)

- **اسم المشروع:** AI Marketing Platform (AI Growth OS / BrandMind AI / QEVION)
- **الحالة الإجمالية:** `READY_FOR_FOUNDATION_SPRINT`
- **المرحلة الحالية:** المرحلة 0 و 1 (Phase 0: Environment Foundation & Phase 1: Meta Graph API Integration)
- **الفرع المعتمد المستقر:** `main`
- **فرع التطوير المستمر:** `work/implementation`
- **المرجع المعرفي المعتمد:** مجلد `AI_Marketing_Platform_Hub/` (57 وثيقة تخصصية)
- **الميثاق المعماري الحاكم:** وثيقة `imaster_planner.md`

---

## 2. النواة البرمجية المكتشفة والمحققة (Verified Codebase Baseline)

بناءً على فحص مساحة العمل الأرشيفية، هذه هي الأصول المعتمدة الجاهزة لإعادة الاستخدام:

| المكون | المسار الأصلي في مساحة العمل | الحالة والجاهزية | الأدلة والاختبارات |
|---|---|---|---|
| **النواة الخلفية (FastAPI)** | `ult_agent_agent/ai-growth-os-main` | جاهز للنقل وإعادة الاستخدام | 29 اختبار pytest ناجح بنسبة 100% |
| **الواجهة الأمامية (Next.js 14)** | `first_lunch/ai-marketing-platform` | جاهز للتوصيل (RTL + عربي) | صفحات الشات واللوحة والتقارير مبنية |
| **روبوت محادثات WhatsApp** | `whatsapp_ai_v2/whatsapp_ai_v3` | جاهز للتكامل مع Meta Cloud API | محرك OmniReply التفاعلي |

---

## 3. المهمة النشطة الحالية (Active Task)

- **معرف المهمة:** `TASK-01-FOUNDATION-META-FASTAPI`
- **الهدف التنفيذي:**
  1. تأسيس هيكل تطبيق FastAPI الموحد المعتمد على نمط الـ Modular Monolith.
  2. تثبيت التبعيات الأساسية (`requirements.txt` / `pyproject.toml`).
  3. ربط وتوثيق تكامل Meta Graph API (Facebook & Instagram Business) مع معالجة تدفق OAuth 2.0 وتشفير التوكنات وتخزينها الآمن.
- **الملفات المسموح بتعديلها أو إنشائها:**
  - `src/` أو `backend/`
  - `tests/`
  - `pyproject.toml` أو `requirements.txt`
  - `WORK_STATE.md`
- **الملفات المحظور لمسها:**
  - أي ملفات داخل `AI_Marketing_Platform_Hub/` إلا بطلب توثيقي صريح من المالك.
  - أي محاولة لدمج مكتبات AGPL-3.0 تجارياً.

---

## 4. معايير القبول للمهمة الحالية (Acceptance Criteria)

- [ ] تشغيل خادم FastAPI بنجاح وظهور صفحة التوثيق التفاعلية (`/docs`).
- [ ] وجود اختبارات تغطية وحدات (Unit Tests) بنسبة تزيد عن 85% للوحدات المضافة.
- [ ] مسارات ربط Meta OAuth تعمل بصورة Mocking ومجهزة للاتصال الحقيقي.
- [ ] عدم وجود أي أسرار أو توكنات في الكود المصدري.
- [ ] اجتياز جميع اختبارات `pytest` بنجاح كامل (Zero Failures).

---

## 5. الخطوة الدقيقة التالية (Exact Next Step)

1. إعداد فرع العمل `work/implementation` والانتقال إليه.
2. إنشاء هيكل مشروع FastAPI الموحد (`src/core`, `src/modules/social_connect`).
3. إعداد اختبارات الـ Contract والـ Mock لـ Meta OAuth API وتشغيلها للتحقق من نجاحها.
