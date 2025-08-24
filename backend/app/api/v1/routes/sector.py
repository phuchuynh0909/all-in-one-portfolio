from typing import List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.schemas.sector import (
    Sector,
    SectorCreate,
    StockSymbol,
    StockSymbolCreate,
    SectorSummary,
)
from app.services import sector_service

router = APIRouter(prefix="/sector", tags=["sector"])


@router.get("/list/{level}", response_model=List[Sector])
def list_sectors(level: int, db: Session = Depends(get_db)) -> List[Sector]:
    return sector_service.get_sectors(db, level)


@router.get("/{level}/{sector_id}", response_model=Sector)
def get_sector(
    level: int, sector_id: int, db: Session = Depends(get_db)
) -> Sector:
    sector = sector_service.get_sector(db, sector_id, level)
    if not sector:
        raise HTTPException(status_code=404, detail="Sector not found")
    return sector


@router.post("/symbols", response_model=StockSymbol)
def upsert_stock_symbol(
    symbol: StockSymbolCreate, db: Session = Depends(get_db)
) -> StockSymbol:
    return sector_service.upsert_stock_symbol(db, symbol)


@router.get("/symbols/{level}/{sector_id}", response_model=List[StockSymbol])
def list_sector_symbols(
    level: int, sector_id: int, db: Session = Depends(get_db)
) -> List[StockSymbol]:
    return sector_service.get_stock_symbols_by_sector(db, sector_id, level)


@router.get("/summary/{level}/{sector_id}", response_model=SectorSummary)
def get_sector_summary(
    level: int, sector_id: int, db: Session = Depends(get_db)
) -> SectorSummary:
    summary = sector_service.get_sector_summary(db, sector_id, level)
    if not summary:
        raise HTTPException(status_code=404, detail="Sector not found")
    return summary


@router.post("/import/{level}")
async def import_sector_json(
    level: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    try:
        # Save uploaded file temporarily
        content = await file.read()
        temp_path = f"/tmp/sector_level_{level}.json"
        with open(temp_path, "wb") as f:
            f.write(content)

        # Process the file
        count = sector_service.upsert_sector_from_file(db, temp_path, level)
        return {"status": "success", "sectors_imported": count}
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error importing sector data: {str(e)}",
        )
