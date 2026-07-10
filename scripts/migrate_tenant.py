#!/usr/bin/env python3
"""CLI script to run tenant migrations for a single hospital."""

import argparse
import os
import subprocess

from sqlalchemy import create_engine, text


def get_tenant_dsn(tenant_id: str) -> str:
    from cryptography.fernet import Fernet
    master_db_url = os.getenv("MASTER_DB_URL", "postgresql://postgres:postgres@localhost:5432/hospital_master")
    encryption_key = os.getenv("TENANT_DB_ENCRYPTION_KEY", "RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0=")
    cipher = Fernet(encryption_key.encode())

    engine = create_engine(master_db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT db_connection_string FROM tenants WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        ).fetchone()
    if not result:
        raise ValueError(f"Tenant {tenant_id} not found")
    
    enc_dsn = result[0]
    if not enc_dsn:
        raise ValueError(f"Tenant {tenant_id} has no DB DSN configured")
    
    dsn = cipher.decrypt(enc_dsn.encode()).decode()
    import re
    return re.sub(r'@([^:]+):5432', '@localhost:5432', dsn)


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
    print(f"Migrations applied for tenant: {tenant_id}")


def main():
    parser = argparse.ArgumentParser(description="Run tenant migrations for one hospital")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID")
    args = parser.parse_args()

    dsn = get_tenant_dsn(args.tenant_id)
    run_migrations(args.tenant_id, dsn)


if __name__ == "__main__":
    main()
