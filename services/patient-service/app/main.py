from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import router as v1_router
from app.config import settings
from app.db.base import Base
from app.core.database import get_session_local


@asynccontextmanager
async def lifespan(app: FastAPI):
    SessionLocal = get_session_local()
    engine = SessionLocal.kw["bind"]
    Base.metadata.create_all(bind=engine)
    yield


tags_metadata = [
    {
        "name": "patients",
        "description": "Patient demographic records, registrations, updates, and searches",
    }
]

app = FastAPI(
    title="Patient Service",
    version="1.0.0",
    docs_url="/docs",
    lifespan=lifespan,
    openapi_tags=tags_metadata,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins.split(",") if settings.allowed_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(v1_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "patient-service"}
