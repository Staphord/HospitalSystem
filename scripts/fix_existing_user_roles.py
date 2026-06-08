"""
One-off script to retroactively assign real Keycloak roles to existing users
whose Keycloak account only has 'hospital_user'.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from app.core.database import SessionLocal, engine
from app.models.user import User
from app.services.keycloak_admin import assign_user_roles


ROLE_MAP = {
    "hospital_admin": ["hospital_admin", "hospital_user"],
    "hospital_user": ["hospital_user"],
    "nurse": ["nurse", "hospital_user"],
    "clinician": ["clinician", "hospital_user"],
    "doctor": ["doctor", "hospital_user"],
    "patient": ["patient", "hospital_user"],
}


async def fix_roles():
    db: Session = SessionLocal()
    try:
        users = db.query(User).all()
        fixed = 0
        for u in users:
            target = ROLE_MAP.get(u.role, ["hospital_user"])
            try:
                await assign_user_roles(u.keycloak_sub, target)
                print(f"Fixed {u.username} ({u.role}) -> {target}")
                fixed += 1
            except Exception as e:
                print(f"Failed to fix {u.username}: {e}")
        print(f"\nDone. Fixed {fixed}/{len(users)} users.")
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(fix_roles())
