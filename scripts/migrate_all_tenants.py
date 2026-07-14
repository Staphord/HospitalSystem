#!/usr/bin/env python3
"""CLI script to run tenant migrations across all active hospitals."""

import argparse
import os
import subprocess

from sqlalchemy import create_engine, text


def get_active_tenants() -> list[tuple[str, str]]:
    from cryptography.fernet import Fernet
    master_db_url = os.getenv("MASTER_DB_URL", "postgresql://postgres:postgres@localhost:5432/hospital_master")
    encryption_key = os.getenv("TENANT_DB_ENCRYPTION_KEY", "RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0=")
    cipher = Fernet(encryption_key.encode())

    engine = create_engine(master_db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT tenant_id, db_connection_string FROM tenants WHERE is_active = true")
        ).fetchall()

    tenants = []
    import re
    for row in rows:
        tenant_id, enc_dsn = row[0], row[1]
        if enc_dsn:
            try:
                dsn = cipher.decrypt(enc_dsn.encode()).decode()
                # Replace any database host with localhost for host execution
                dsn = re.sub(r'@([^:]+):5432', '@localhost:5432', dsn)
                tenants.append((tenant_id, dsn))
            except Exception as e:
                print(f"Failed to decrypt DSN for tenant {tenant_id}: {e}")
        else:
            tenants.append((tenant_id, None))
    return tenants


def run_migrations(tenant_id: str, dsn: str) -> None:
    import sys
    env = os.environ.copy()
    env["TENANT_DB_URL"] = dsn
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd="migrations/tenant",
        env=env,
        check=True,
    )
    print(f"  [OK] {tenant_id}")


def main():
    parser = argparse.ArgumentParser(description="Run tenant migrations for all active hospitals")
    parser.add_argument("--dry-run", action="store_true", help="List tenants without running migrations")
    args = parser.parse_args()

    tenants = get_active_tenants()
    if not tenants:
        print("No active tenants found.")
        return

    print(f"Found {len(tenants)} active tenant(s):")
    for tenant_id, dsn in tenants:
        print(f"  - {tenant_id}")

    if args.dry_run:
        print("Dry-run mode: no migrations executed.")
        return

    print("Running migrations...")
    for tenant_id, dsn in tenants:
        if not dsn:
            print(f"  [SKIP] {tenant_id} — no DSN configured")
            continue
        try:
            run_migrations(tenant_id, dsn)
        except Exception as exc:
            print(f"  [ERR] {tenant_id} — {exc}")


if __name__ == "__main__":
    main()
