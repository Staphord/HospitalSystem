"""Run tenant Alembic migrations on all existing tenant databases.

Usage:
    docker compose -p hospital_flow -f infrastructure/docker-compose.yml run --rm master-service python /app/scripts/migrate_existing_tenants.py
"""

from __future__ import annotations

import os
import subprocess
import sys

from sqlalchemy import create_engine, text


def _master_db_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:12345678@postgres-master:5432/hospital_master",
    )


def _tenant_db_url(tenant_id: str) -> str:
    template = os.environ.get(
        "TENANT_DB_TEMPLATE",
        "postgresql://postgres:12345678@postgres-master:5432/tenant_{tenant_id}",
    )
    return template.format(tenant_id=tenant_id)


def _get_tenant_ids() -> list[str]:
    engine = create_engine(_master_db_url(), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT tenant_id FROM tenants ORDER BY tenant_id"))
            return [row[0] for row in rows]
    finally:
        engine.dispose()


def _migrate_tenant(tenant_id: str) -> None:
    dsn = _tenant_db_url(tenant_id)
    env = {**dict(os.environ), "TENANT_DB_URL": dsn}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "migrations/tenant/alembic.ini",
            "upgrade",
            "head",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"[FAIL] {tenant_id}: {result.stderr.strip()}")
    else:
        print(f"[OK] {tenant_id}")
        if result.stdout:
            print(result.stdout.strip())


def main() -> None:
    tenant_ids = _get_tenant_ids()
    print(f"Found {len(tenant_ids)} tenant(s) to migrate")
    for tenant_id in tenant_ids:
        _migrate_tenant(tenant_id)


if __name__ == "__main__":
    main()
