from app.db.master import get_master_session
from app.db.tenant import get_tenant_session

get_master_db = get_master_session


async def get_tenant_db(tenant_id: str):
    async for session in get_tenant_session(tenant_id):
        yield session
