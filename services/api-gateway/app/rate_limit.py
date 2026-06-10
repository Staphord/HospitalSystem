from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request


def _rate_limit_key(request: Request) -> str:
    user_sub = getattr(request.state, "user_sub", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    key = f"{tenant_id or user_sub or get_remote_address(request)}"
    return key


limiter = Limiter(key_func=_rate_limit_key)
