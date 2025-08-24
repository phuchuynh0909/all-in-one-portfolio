from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["health"])  # GET /api/v1/health
async def health_check() -> dict:
    return {"status": "ok"}
