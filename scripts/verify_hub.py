import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
hub_dir = os.path.join(repo_root, "AI_Marketing_Platform_Hub")

expected_folders = [
    "00_START_HERE",
    "01_PROJECT_DISCOVERY",
    "02_REQUIREMENTS_AND_VISION",
    "03_EXISTING_PROGRESS",
    "04_ARCHITECTURE_AND_DESIGN",
    "05_OPEN_SOURCE_RESEARCH",
    "06_PRODUCT_PLANNING",
    "07_EXECUTION_AND_TESTING",
    "08_SECURITY_AND_OPERATIONS",
    "09_BUSINESS_AND_GO_TO_MARKET",
    "10_DECISIONS_AND_HISTORY",
    "11_HANDOVER",
    "12_ARCHIVE_INDEX",
]

print("=== VERIFYING KNOWLEDGE HUB INTEGRITY ===")
total_files = 0
total_bytes = 0
all_good = True

for folder in expected_folders:
    p = os.path.join(hub_dir, folder)
    if not os.path.exists(p):
        print(f"FAIL: Missing folder {folder}")
        all_good = False
        continue
    files = [f for f in os.listdir(p) if f.endswith(".md")]
    print(f"OK: {folder} has {len(files)} docs")
    for f in files:
        fp = os.path.join(p, f)
        size = os.path.getsize(fp)
        total_files += 1
        total_bytes += size
        if size == 0:
            print(f"  WARN: Empty file {f}")
            all_good = False

# Verify root control files
control_files = [
    "README.md",
    "imaster_planner.md",
    "SESSION_RECOVERY.md",
    "WORK_STATE.md",
    "ARCHITECTURE_LOCK.md",
]
print("\n=== VERIFYING ROOT CONTROL FILES ===")
for cf in control_files:
    cfp = os.path.join(repo_root, cf)
    if os.path.exists(cfp):
        print(f"OK: {cf} ({os.path.getsize(cfp)} bytes)")
    else:
        print(f"FAIL: Missing control file {cf}")
        all_good = False

print(f"\nTotal Hub Documents: {total_files}")
print(f"Total Hub Size: {total_bytes:,} bytes")
if all_good:
    print("RESULT: ALL CHECKS PASSED SUCCESSFULLY!")
    sys.exit(0)
else:
    print("RESULT: VERIFICATION FAILED.")
    sys.exit(1)
