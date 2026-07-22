#!/usr/bin/env python3
"""CLI script to run tenant migrations across all active hospitals.

Install deps (no root requirements.txt in this repo):
    pip install -r scripts/requirements-migrate.txt

Examples (PowerShell):
    # If Postgres runs in Docker (compose password):
    $env:MASTER_DB_URL = "postgresql://postgres:12345678@localhost:5432/hospital_master"
    $env:TENANT_DB_ENCRYPTION_KEY = "RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0="
    python scripts/migrate_all_tenants.py --dry-run
    python scripts/migrate_all_tenants.py

    # Prefer running inside Docker if host port 5432 is a different Postgres:
    docker cp scripts/migrate_existing_tenants.py hospital-master-service:/tmp/migrate_existing_tenants.py
    docker exec -w /app hospital-master-service python /tmp/migrate_existing_tenants.py
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

from sqlalchemy import create_engine, inspect, text


def _master_db_url() -> str:
    return os.getenv(
        "MASTER_DB_URL",
        os.getenv(
            "DATABASE_URL",
            "postgresql://postgres:12345678@localhost:5432/hospital_master",
        ),
    )


def _encryption_key() -> str:
    return os.getenv(
        "TENANT_DB_ENCRYPTION_KEY",
        "RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0=",
    )


def _rewrite_dsn_for_host(dsn: str) -> str:
    """Rewrite Docker service hostnames to localhost when running on the host."""
    return re.sub(
        r"@(postgres-master|postgres|hospital-postgres-master)(:\d+)?/",
        r"@localhost\2/",
        dsn,
    )


def get_active_tenants() -> list[tuple[str, str | None]]:
    from cryptography.fernet import Fernet

    cipher = Fernet(_encryption_key().encode())
    engine = create_engine(_master_db_url())
    with engine.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("tenants")}
        dsn_col = (
            "db_connection_string"
            if "db_connection_string" in cols
            else "db_dsn_encrypted"
            if "db_dsn_encrypted" in cols
            else None
        )
        if not dsn_col:
            raise RuntimeError(
                "tenants table has neither db_connection_string nor db_dsn_encrypted"
            )
        rows = conn.execute(
            text(
                f"SELECT tenant_id, {dsn_col} FROM tenants WHERE is_active = true"
            )
        ).fetchall()

    tenants: list[tuple[str, str | None]] = []
    for tenant_id, enc_dsn in rows:
        if not enc_dsn:
            tenants.append((tenant_id, None))
            continue
        try:
            dsn = cipher.decrypt(str(enc_dsn).encode()).decode()
            if os.getenv("MIGRATE_KEEP_DOCKER_HOST", "").lower() not in ("1", "true", "yes"):
                dsn = _rewrite_dsn_for_host(dsn)
                dsn = re.sub(r"@([^:/]+):5432", "@localhost:5432", dsn)
            tenants.append((tenant_id, dsn))
        except Exception as e:
            print(f"Failed to decrypt DSN for tenant {tenant_id}: {e}")
            tenants.append((tenant_id, None))
    return tenants


def run_migrations(tenant_id: str, dsn: str) -> None:
    env = os.environ.copy()
    env["TENANT_DB_URL"] = dsn
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "heads"],
        cwd="migrations/tenant",
        env=env,
        check=True,
    )
    print(f"  [OK] {tenant_id}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run tenant migrations for all active hospitals"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List tenants without running migrations",
    )
    args = parser.parse_args()

    print(f"Master DB: {_master_db_url().split('@')[-1]}")
    try:
        tenants = get_active_tenants()
    except Exception as exc:
        print(
            "\nFailed to connect to Master DB.\n"
            "Compose password is postgres/12345678. If auth fails on localhost:5432,\n"
            "another Postgres owns that port — prefer Docker:\n\n"
            "  docker exec hospital-master-service python /app/scripts/migrate_existing_tenants.py\n\n"
            "Or set MASTER_DB_URL to the correct host/password, e.g.:\n"
            '  $env:MASTER_DB_URL="postgresql://postgres:12345678@127.0.0.1:5432/hospital_master"\n'
        )
        raise SystemExit(f"{type(exc).__name__}: {exc}") from exc
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
