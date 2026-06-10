#!/usr/bin/env python3
"""CLI script to run tenant migrations for a single hospital."""

import argparse
import os
import subprocess

from sqlalchemy import create_engine, text


def get_tenant_dsn(tenant_id: str) -> str:
    master_db_url = os.getenv("MASTER_DB_URL", "postgresql://postgres:postgres@localhost:5432/hospital_master")
    engine = create_engine(master_db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT db_dsn_encrypted FROM tenants WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        ).fetchone()
    if not result:
        raise ValueError(f"Tenant {tenant_id} not found")
    # In production, decrypt the DSN here. For dev, assume plaintext or use env override.
    dsn = result[0]
    if not dsn:
        raise ValueError(f"Tenant {tenant_id} has no DB DSN configured")
    return dsn


def run_migrations(tenant_id: str, dsn: str) -> None:
    env = os.environ.copy()
    env["TENANT_DB_URL"] = dsn
    subprocess.run(
        ["alembic", "upgrade", "head"],
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
