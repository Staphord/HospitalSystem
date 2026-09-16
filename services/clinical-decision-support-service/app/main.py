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
        "Clinical Differential Support. Produces considerations for clinician "
        "review, never a diagnosis: red flags come from a deterministic, "
        "versioned rule pack rather than from a language model, and no result "
        "may prescribe, dose, refer, admit, or write to a record."
    ),
    version="1.0.0",
    docs_url=docs_url,
    openapi_url=openapi_url,
    openapi_tags=[
        {
            "name": "Clinical differential support",
            "description": (
                "Diagnosis suggestions for clinician review, with the inputs, "
                "evidence, missing data, limitations, and versions behind them."
            ),
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

    Deliberately carries no rule contents, no patient data, and no credential:
    only whether the capability is switched on and which rule pack is answering.
    """
    from app.cds.redflags import ruleset_version

    return {
        "status": "ok",
        "service": "clinical-decision-support-service",
        "cds_enabled": settings.cds_enabled,
        "differential_support_enabled": settings.cds_differential_support_enabled,
        "redflag_ruleset_version": ruleset_version(),
    }


@app.get("/metrics")
async def feature_metrics():
    """Feature-level counters for operations and clinical review.

    Not reachable through the API Gateway: the gateway routes /api/v1/cds and
    nothing else here, so this endpoint is only addressable from inside the
    deployment network by a scraper or an on-call engineer.

    Every counter is keyed by a member of a closed vocabulary. No patient, no
    tenant, no actor, and no clinical text can appear in this payload, by
    construction rather than by review.
    """
    from app.cds.metrics import snapshot

    return snapshot()


app.include_router(service_router, prefix="/api/v1/cds")
