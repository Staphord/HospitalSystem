import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.router import router as service_router
from app.core.config import settings
from app.core.limiter import limiter
from app.core.middleware import (
    AuditLogMiddleware,
    ImpersonationBannerMiddleware,
    ReadOnlyScopeMiddleware,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("service.log", mode="a"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("service")

docs_url = None if settings.environment == "prod" else "/docs"
openapi_url = None if settings.environment == "prod" else "/openapi.json"

app = FastAPI(
    title="Clinical Decision Support Service",
    description=(
        "Deterministic clinical decision support. Medication safety results come "
        "from an approved, versioned ruleset, never from a language model, and a "
        "result that is unknown, stale, ambiguous, or failed is reported as "
        "needs_review rather than as safe."
    ),
    version="1.0.0",
    docs_url=docs_url,
    openapi_url=openapi_url,
    openapi_tags=[
        {
            "name": "Medication safety",
            "description": (
                "Normalize medicines for confirmation, run deterministic checks, "
                "and record acknowledgements and overrides."
            ),
        },
        {
            "name": "Ruleset",
            "description": "Which approved ruleset version is answering today.",
        },
    ],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

allowed_origins = [origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip()]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Impersonation-Banner"],
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if settings.environment == "prod":
        response.headers["Content-Security-Policy"] = "default-src 'none'"
    return response


app.add_middleware(ReadOnlyScopeMiddleware)
app.add_middleware(ImpersonationBannerMiddleware)
app.add_middleware(AuditLogMiddleware)


@app.get("/health")
async def health():
    """Liveness plus the operational state an on-call engineer needs.

    Deliberately carries no ruleset contents, no rule identifiers, no patient
    data, and no credential: only whether the capability is switched on and
    whether an approved ruleset is currently answering.
    """
    from app.cds.rules import active_ruleset_health

    return {
        "status": "ok",
        "service": "clinical-decision-support-service",
        "cds_enabled": settings.cds_enabled,
        "medication_check_enabled": settings.cds_medication_check_enabled,
        "ruleset": active_ruleset_health(),
    }


app.include_router(service_router, prefix="/api/v1/cds")
