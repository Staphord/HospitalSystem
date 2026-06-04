from app.db.master import get_master_session
from app.db.tenant import get_tenant_db

get_master_db = get_master_session


def get_tenant_session(tenant_id: str):
    yield from get_tenant_db(tenant_id)
