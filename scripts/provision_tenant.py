#!/usr/bin/env python3
"""CLI script to provision a new hospital tenant."""

import argparse
import uuid
import os

from sqlalchemy import create_engine, text


def provision_tenant(hospital_name: str, country: str, city: str, admin_email: str) -> str:
    tenant_id = f"hosp-{uuid.uuid4().hex[:8]}"
    master_db_url = os.getenv("MASTER_DB_URL", "postgresql://postgres:postgres@localhost:5432/hospital_master")
    engine = create_engine(master_db_url)

    with engine.connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tenants (
                    tenant_id, name, db_dsn_encrypted, status,
                    subscription_plan, is_active, created_at, updated_at
                ) VALUES (
                    :tenant_id, :name, '', 'active',
                    'basic', true, NOW(), NOW()
                )
                """
            ),
            {"tenant_id": tenant_id, "name": hospital_name},
        )
        conn.commit()

    print(f"Tenant provisioned successfully: {tenant_id}")
    print(f"  Hospital name: {hospital_name}")
    print(f"  Country: {country}")
    print(f"  City: {city}")
    print(f"  Admin email: {admin_email}")
    return tenant_id


def main():
    parser = argparse.ArgumentParser(description="Provision a new hospital tenant")
    parser.add_argument("--hospital-name", required=True, help="Name of the hospital")
    parser.add_argument("--country", required=True, help="Country")
    parser.add_argument("--city", required=True, help="City")
    parser.add_argument("--admin-email", required=True, help="Admin email address")
    args = parser.parse_args()

    provision_tenant(args.hospital_name, args.country, args.city, args.admin_email)


if __name__ == "__main__":
    main()
