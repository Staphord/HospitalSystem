#!/usr/bin/env python3
"""Seed development data: 2 hospitals, super admin, staff accounts."""

import os
import uuid

from sqlalchemy import create_engine, text


MASTER_DB_URL = os.getenv("MASTER_DB_URL", "postgresql://postgres:postgres@localhost:5432/hospital_master")


def seed():
    engine = create_engine(MASTER_DB_URL)
    with engine.connect() as conn:
        # Create 2 test hospitals
        hospitals = [
            ("hosp-" + uuid.uuid4().hex[:8], "City General Hospital", "citygeneral@example.com"),
            ("hosp-" + uuid.uuid4().hex[:8], "Mercy Hospital", "mercy@example.com"),
        ]
        for tenant_id, name, email in hospitals:
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
                    ON CONFLICT (tenant_id) DO NOTHING
                    """
                ),
                {"tenant_id": tenant_id, "name": name},
            )
            print(f"Created tenant: {tenant_id} ({name})")

        # Create super admin
        conn.execute(
            text(
                """
                INSERT INTO super_admins (
                    super_admin_id, username, email, password_hash,
                    full_name, role, is_active, created_at
                ) VALUES (
                    :id, 'superadmin', 'superadmin@example.com', 'placeholder_hash',
                    'Super Admin', 'super_admin', true, NOW()
                )
                ON CONFLICT (username) DO NOTHING
                """
            ),
            {"id": str(uuid.uuid4())},
        )
        print("Created super admin: superadmin")

        conn.commit()

    print("Dev seed complete.")


if __name__ == "__main__":
    seed()
