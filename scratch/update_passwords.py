import os
import re

ROOT_DIR = "/Volumes/nshondev/GILGAL/Hospital/HospitalSystem"

FILES_TO_UPDATE = [
    "infrastructure/docker-compose.yml",
    "infrastructure/recreate_visit_schema.py",
    "infrastructure/check_visit_db.py",
    "scripts/test_isolation.py",
    "scripts/migrate_existing_tenants.py",
    "scripts/test_tenant_provision.py",
    "scripts/test_e2e_isolation.py",
    "scripts/test_backward_compat.py"
]

def update_file(relative_path):
    full_path = os.path.join(ROOT_DIR, relative_path)
    if not os.path.exists(full_path):
        print(f"File not found: {full_path}")
        return
        
    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    # Replace :nasr@ with :12345678@
    updated = content.replace(":nasr@", ":12345678@")
    # Replace POSTGRES_PASSWORD: nasr with POSTGRES_PASSWORD: 12345678
    updated = updated.replace("POSTGRES_PASSWORD: nasr", "POSTGRES_PASSWORD: 12345678")
    
    if updated != content:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(updated)
        print(f"Updated: {relative_path}")
    else:
        print(f"No changes needed for: {relative_path}")

if __name__ == "__main__":
    for rel_path in FILES_TO_UPDATE:
        update_file(rel_path)
