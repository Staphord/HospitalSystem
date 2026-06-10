import os
import re

files_to_fix = [
    "services/report-service/app/core/config.py",
    "services/notification-service/app/core/config.py",
    "services/ward-service/app/core/config.py",
    "services/billing-service/app/core/config.py",
    "services/pharmacy-service/app/core/config.py",
    "services/radiology-service/app/core/config.py",
    "services/laboratory-service/app/core/config.py",
    "services/consultation-service/app/core/config.py",
    "services/triage-service/app/core/config.py",
    "services/reception-service/app/core/config.py",
    "services/admin-service/app/config.py",
]

for rel_path in files_to_fix:
    path = os.path.join(os.path.dirname(__file__), "..", rel_path)
    with open(path, "r") as f:
        content = f.read()

    # Check if extra = "ignore" is already present
    if 'extra = "ignore"' in content:
        print(f"SKIP: {rel_path} (already has extra = 'ignore')")
        continue

    # Add extra = "ignore" to the Config class
    new_content = re.sub(
        r'(class Config:\s*\n\s*env_file = ".env"\s*\n\s*case_sensitive = False)(\s*\n)',
        r'\1\n        extra = "ignore"\2',
        content,
    )

    if new_content == content:
        print(f"WARN: Could not patch {rel_path}")
    else:
        with open(path, "w") as f:
            f.write(new_content)
        print(f"FIXED: {rel_path}")

print("Done.")
