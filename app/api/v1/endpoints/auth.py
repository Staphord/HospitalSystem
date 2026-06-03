from fastapi import APIRouter

router = APIRouter()


@router.get("/health", include_in_schema=True)
async def health() -> dict:
    return {"status": "ok"}
