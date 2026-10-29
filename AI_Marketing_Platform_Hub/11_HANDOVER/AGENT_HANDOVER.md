# 🤖 دليل تسليم المهندسين ووكلاء الذكاء الاصطناعي (Agent Handover Guide)
**المستلم المستهدف:** أي مهندس برمجيات، مهندس معمارية، أو وكيل ذكاء اصطناعي يستلم المشروع.  
**تاريخ التحديث:** 28 أكتوبر 2026

---

## 📜 1. القواعد الذهبية للعمل في هذا المشروع

1. **المرجع المعتمد الوحيد هو `AI_Marketing_Platform_Hub`:**
   - لا تبدأ بالبحث العشوائي في المجلدات القديمة. ابدأ دائماً بقراءة [MASTER_INDEX.md](file:///d:/projects/AI_markting/AI_Marketing_Platform_Hub/00_START_HERE/MASTER_INDEX.md).
2. **احترام ميثاق الخدمة الموحد F1:**
   - أي ميزة أو خدمة جديدة يجب أن تستخدم `RequestEnvelope` و `ResponseEnvelope` من مكتبة `libs/envelope`.
   - لا تُعد كتابة كود الأمان أو عزل المستأجرين؛ الأساس المشترك (Shared Foundation) مبني بالفعل.
3. **ممنوع كسر الاختبارات القائمة:**
   - قبل إجراء أي تعديل برمجياً، شغّل:
     ```powershell
     python -m pytest platform/tests
     ```
   - يجب أن تظل جميع الاختبارات (29 اختباراً) خضراء وناجحة دائماً.
4. **حظر تضمين تراخيص الـ AGPL (القرار D-054):**
   - لا تقم أبداً باستيراد أو نسخ كود من مستودعات تحمل ترخيص AGPL-3.0 (مثل Postiz أو Dub) إلى الكود المصدري للمنظومة.
5. **سياسة الأمان وانعدام الأسرار (Zero-Secret Commitment):**
   - لا تضع أبداً أي مفاتيح API أو كلمات مرور أو رموز سرية في الكود المصدري أو ملفات الماركداون. استخدم `.env` دائماً.
6. **احترام واجهة المستخدم العربية والـ RTL:**
   - الواجهة مبنية أصلياً باللغة العربية مع دعم كامل للاتجاه من اليمين لليسار؛ لا تقم بإلغاء نصوص اللغة العربية أو تحويل الواجهة للإنجليزية.

---

## 🧭 2. خريطة القراءة للمهندس الجديد
1. اقرأ [PROJECT_STATUS.md](file:///d:/projects/AI_markting/AI_Marketing_Platform_Hub/00_START_HERE/PROJECT_STATUS.md) لتعرف أين يقف المشروع اليوم بالضبط.
2. اقرأ [SYSTEM_OVERVIEW.md](file:///d:/projects/AI_markting/AI_Marketing_Platform_Hub/04_ARCHITECTURE_AND_DESIGN/SYSTEM_OVERVIEW.md) لفهم المعمارية وتوزيع المحركات.
3. اقرأ [TASK_BACKLOG.md](file:///d:/projects/AI_markting/AI_Marketing_Platform_Hub/07_EXECUTION_AND_TESTING/TASK_BACKLOG.md) لاختيار المهمة التالية والبدء فيها فوراً.
