from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request


def _rate_limit_key(request: Request) -> str:
    user_sub = getattr(request.state, "user_sub", None)
    return user_sub or get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key)
