# SESSION RECOVERY PROTOCOL

## بروتوكول استعادة جلسة العمل واستئناف التطوير

> **الغرض:** ضمان استمرار العمل عبر جلسات متعددة أو عند انتقال المهمة بين وكلاء ذكاء اصطناعي ومطورين مختلفين، ومنع فقدان المعرفة، وتفادي إعادة كتابة كود تم إنجازه واختباره بالفعل.

---

## 1. قواعد الاستئناف الإلزامية (Resumption Checklist)

قبل كتابة أي كود أو تعديل أي ملف بعد انقطاع الجلسة أو بدء دورة جديدة، **يجب** على الوكيل اتباع الخطوات التالية بالتسلسل:

### الخطوة 1: فحص ملفات التحكم الثلاثة
1. اقرأ `WORK_STATE.md` لمعرفة المهمة النشطة الحالية، والفرع المعتمد، وآخر Commit، والخطوة التالية المحددة بدقة.
2. اقرأ `ARCHITECTURE_LOCK.md` للتأكد من القيود المعمارية والقرارات المعتمدة التي يُحظر المساس بها أو مخالفتها.
3. راجع `SESSION_RECOVERY.md` (هذا الملف) للتأكد من توافق بيئة العمل.

### الخطوة 2: فحص حالة مستودع Git
قم بتشغيل الأوامر التالية والتحقق من المخرجات:
```bash
git status
git branch --show-current
git log -n 5 --oneline
```
- تأكد أنك لست في حالة `detached HEAD` أو تعارض دمج غير محسوم (`merge conflict`).
- تأكد من فرع العمل:
  - `main`: النسخة المستقرة المعتمدة فقط (ممنوع الكوميت المباشر عليها أثناء التطوير).
  - `work/implementation`: فرع التطوير النشط لكافة التعديلات البرمجية اليومية.

### الخطوة 3: التحقق من سلامة خط الأساس الحالي (Baseline Verification)
قبل إجراء أي تعديل جديد، شغّل حزمة الاختبارات القائمة للتأكد من أن الكود الحالي في حالة سليمة بنسبة 100%:
```bash
pytest
```
- إذا رسب أي اختبار قائم، **توقف فوراً**. لا تبدأ المهمة الجديدة حتى يتم إصلاح الاختبار أو إبلاغ المالك.
- لا تبنِ ميزة جديدة فوق أساس به أخطاء غير معالجة.

### الخطوة 4: التحقق من التوجيه التنفيذي للمهمة الحالية
- راجع ملف التوجيه المعتمد للمهمة في `07_EXECUTION_AND_TESTING/TASK_BACKLOG.md` أو وثيقة التوجيه المحددة في `WORK_STATE.md`.
- تحقق من النطاق المسموح بتعديله، والملفات المحظور لمسها، ومعايير القبول (`Acceptance Criteria`).

### الخطوة 5: تقديم ملخص الاستئناف للمالك (Handshake Summary)
قدّم للمالك ملخصاً موجزاً في سطرين إلى أربعة أسطر:
1. الحالة التي تم استعادتها بنجاح (رقم الكوميت وحالة الاختبارات).
2. المهمة الدقيقة التي سيتم البدء في تنفيذها الآن.
3. معايير القبول التي سيتم اختبارها عند التسليم.

---

## 2. إجراءات الطوارئ عند الانقطاع المفاجئ (Emergency Recovery)

إذا انقطعت الجلسة أثناء كتابة كود أو تعديل ملفات:

1. **فحص التغييرات المعلقة (Uncommitted Changes):**
   ```bash
   git status
   git diff
   ```
2. **التمييز بين التغييرات الصالحة والتالفة:**
   - إذا كانت التغييرات المتبقية جزءاً مكتملاً من المهمة واختباراته ناجحة، قم بعمل Commit واضح:
     ```bash
     git commit -m "fix/feat: [Task ID] description"
     ```
   - إذا كانت التغييرات ناقصة وتسببت في كسر الاختبارات ولا يمكن إكمالها بأمان، استعد الحالة المستقرة الأخيرة:
     ```bash
     git checkout -- .
     ```
     أو استخدم `git stash` إذا كنت ترغب في فحصها لاحقاً.

3. **تحديث `WORK_STATE.md` فوراً:**
   - سجل التوقيت، وآخر Commit صالح، وسبب التراجع إن وُجد.

---

## 2-ب. إعادة بناء بيئة الساندبوكس بعد إعادة الضبط (Sandbox Rebuild — مُجرَّب 14 مرة حتى SPRINT-07)

إعادة ضبط الساندبوكس تمسح PostgreSQL والاعتماديات وأشجار العمل (`git worktree`) **ولا تمسح `/home/user/webapp`**. الترتيب الذي ثبت عملياً:

```bash
# 1. المستودع: أعد ضبط الـremote بالتوكن (يُمرَّر في بداية الجلسة فقط — لا يُكتب في أي ملف)
git -C /home/user/webapp remote set-url origin https://x-access-token:<TOKEN>@github.com/belalalibb/ai_agancy_markting.git
git -C /home/user/webapp fetch origin --prune && git worktree prune

# 2. PostgreSQL 17 + pgvector (apt من pgdg) — إن غاب /usr/lib/postgresql/17
sudo apt-get install -y -qq postgresql-common && sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
sudo apt-get install -y -qq postgresql-17 postgresql-17-pgvector

# 3. عنقود محلي بلا صلاحيات نظام — ⚠️ /var/run/postgresql غير قابل للكتابة بعد إعادة الضبط ⇒ وجّه المقبس إلى /tmp
PGBIN=/usr/lib/postgresql/17/bin
[ -d /tmp/pg17 ] || $PGBIN/initdb -D /tmp/pg17 -U aigos --auth=trust
$PGBIN/pg_ctl -D /tmp/pg17 -o "-p 5433 -c listen_addresses=localhost -c unix_socket_directories=/tmp" -l /tmp/pg17.log start
$PGBIN/psql -h localhost -p 5433 -U aigos -d postgres -c "CREATE DATABASE ai_growth_os_test"
$PGBIN/psql -h localhost -p 5433 -U aigos -d ai_growth_os_test -c "CREATE EXTENSION IF NOT EXISTS vector"

# 4. الاعتماديات + المتغيرات (alembic يقرأ DATABASE_URL لا TEST_DATABASE_URL)
pip install -e ".[dev]"
export DATABASE_URL=postgresql+asyncpg://aigos@localhost:5433/ai_growth_os_test
export TEST_DATABASE_URL=$DATABASE_URL
export AUTH_JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")

# 5. تحقق
$PGBIN/pg_ctl -D /tmp/pg17 status && pytest -q -x --no-cov tests/integration -k "b1_m1 or persistence" | tail -1
```

**قواعد مستخلصة:**
- `pg_isready` قد لا يكون في `PATH`؛ استخدم `$PGBIN/pg_ctl … status`.
- سكربت إعادة البناء بـ`set -e` يفشل **صامتاً** إذا كان في أنبوب (`| tail`)؛ شغّل الخطوات منفصلة أو تحقق من `SETUP_DONE` صراحةً.
- المراجع (المخطط) يعمل في `git worktree add .rv origin/work/implementation` ليبقى `main` نظيفاً؛ الشجرة تُفقد عند إعادة الضبط وتُعاد بالأمر نفسه.
- **ادفع بعد كل كتابة ملف** — إعادة الضبط لا تُنذر.

---

## 3. قواعد صارمة للأمن والأسرار (Security & Secrets Policy)

- **ممنوع نهائياً** تضمين أي مفاتيح API، أو توكنات، أو كلمات مرور داخل الكود، أو ملفات Markdown، أو رسائل الـ Commit.
- استخدم دائماً ملفات `.env.template` كدليل للمتغيرات البيئية، وضع القيم الفعلية في ملف `.env` المحلي (المستثنى من Git).
- إذا اكتشفت مفتاحاً مكشوفاً في ملف قديم، أبلغ المالك فوراً لإلغائه وتدويره، ولا تكرر نسخه.

---

## 4. شروط الانتقال والإنهاء (Handoff Conditions)

لا يُعتبر أي وكيل قد أنهى جلسته بنجاح إلا إذا:
1. اجتازت جميع الاختبارات بنجاح (`pytest` exited with code 0).
2. تم تسجيل التغييرات في `git commit` بوصف دقيق ومحدد.
3. تم تحديث ملف `WORK_STATE.md` ليعكس رقم الكوميت والخطوة التالية.
4. تم رفع التغييرات إلى الفرع البعيد (`git push origin <branch>`).
