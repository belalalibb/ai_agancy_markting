# 🚀 خطة النشر والاستضافة (Release & Deployment Plan)
**تاريخ التوثيق:** 28 أكتوبر 2026

---

## 🏗️ 1. بيئة الاستضافة المعتمدة للنسخة الأولى

- **خادم افتراضي خاص (VPS):** خادم بمواصفات 8 vCPU / 16 GB RAM (مثل Hetzner أو DigitalOcean) بنظام Ubuntu 24.04 LTS بتكلفة شهرية منخفضة (~30-50$).
- **إدارة الحاويات (Docker & Docker Compose):**
  - حاوية `web`: تطبيق Next.js 14 Frontend.
  - حاوية `api`: تطبيق FastAPI Backend.
  - حاوية `db`: قاعدة بيانات PostgreSQL 16 مع امتداد `pgvector`.
  - حاوية `redis`: خادم Redis 7 للـ Caching والمهام.
  - حاوية `temporal`: خادم Temporal Server للأتمتة المتينة.
  - حاوية `proxy`: خادم Nginx أو Caddy لإدارة شهادات SSL (HTTPS) والتوجيه العكسي.

---

## 🔄 2. خطة النشر المستمر (CI/CD Pipeline)
1. **التحقق التلقائي عند كل Pull Request على GitHub:**
   - فحص جودة الكود والـ Linting (Ruff / Black).
   - فحص منع الأسرار (Secret Scanning).
   - تشغيل اختبارات pytest والتأكد من اجتيازها بالكامل.
2. **النشر على خادم الإنتاج:**
   - بناء حاويات Docker ونشرها تلقائياً عبر GitHub Actions عند الدمج في فرع `main`.
   - تطبيق هجرات قاعدة البيانات (Database Migrations) تلقائياً عبر Alembic.
