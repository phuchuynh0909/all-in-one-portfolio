from fastapi import APIRouter

from app.schemas.cw import CoveredWarrantResponse
from app.services.cw_service import get_covered_warrant


router = APIRouter(prefix="/cw", tags=["cw"])


@router.get("/{symbol}", response_model=CoveredWarrantResponse)
async def get_cw_detail(symbol: str) -> CoveredWarrantResponse:
    return await get_covered_warrant(symbol)
