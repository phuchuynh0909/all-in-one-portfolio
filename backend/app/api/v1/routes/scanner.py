from __future__ import annotations

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.schemas.scanner import ScannerColumnsResponse, ScannerRequest, ScannerResponse
from app.services import scanner_service

router = APIRouter(prefix="/scanner", tags=["scanner"])


@router.get("/columns", response_model=ScannerColumnsResponse)
async def list_columns() -> ScannerColumnsResponse:
    return ScannerColumnsResponse(columns=scanner_service.list_columns())


@router.post("/scan", response_model=ScannerResponse)
async def scan(req: ScannerRequest) -> ScannerResponse:
    try:
        return scanner_service.scan(req)
    except Exception as e:
        logger.error(f"Error scanning: {e}")
        raise HTTPException(status_code=500, detail="Scan failed")
