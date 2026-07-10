from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import router as v1_router
from app.config import settings
from app.core.database import get_session_local
from app.db.base import Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    SessionLocal = get_session_local()
    engine = SessionLocal.kw["bind"]
    Base.metadata.create_all(bind=engine)
    yield


tags_metadata = [
    {
        "name": "visits",
        "description": "Patient visits registration and general management.",
    },
    {
        "name": "insurance",
        "description": "Tenant insurance records management, verification, and lookups.",
    },
    {
        "name": "queues",
        "description": "Triage, doctor, laboratory, pharmacy, and billing service queue workflows.",
    },
]

app = FastAPI(
    title="Visit Service",
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
    return {"status": "healthy"}


@app.get("/")
async def root():
    return {"service": "visit-service", "status": "running"}
