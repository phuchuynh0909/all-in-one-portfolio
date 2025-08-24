import json
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.models.market import Sector, StockSymbol
from app.schemas.sector import SectorCreate, StockSymbolCreate, SectorSummary


def upsert_sector(db: Session, sector: SectorCreate) -> Sector:
    db_sector = (
        db.query(Sector)
        .filter(Sector.id == sector.id, Sector.level == sector.level)
        .first()
    )

    if db_sector:
        for key, value in sector.model_dump().items():
            setattr(db_sector, key, value)
    else:
        db_sector = Sector(**sector.model_dump())
        db.add(db_sector)

    db.commit()
    db.refresh(db_sector)
    return db_sector


def get_sectors(db: Session, level: int) -> List[Sector]:
    return db.query(Sector).filter(Sector.level == level).all()


def get_sector(db: Session, sector_id: int, level: int) -> Optional[Sector]:
    return (
        db.query(Sector)
        .filter(Sector.id == sector_id, Sector.level == level)
        .first()
    )


def upsert_stock_symbol(
    db: Session, symbol: StockSymbolCreate
) -> StockSymbol:
    db_symbol = (
        db.query(StockSymbol).filter(StockSymbol.symbol == symbol.symbol).first()
    )

    if db_symbol:
        for key, value in symbol.model_dump().items():
            setattr(db_symbol, key, value)
        db_symbol.updated_at = text("CURRENT_TIMESTAMP")
    else:
        db_symbol = StockSymbol(**symbol.model_dump())
        db.add(db_symbol)

    db.commit()
    db.refresh(db_symbol)
    return db_symbol


def get_stock_symbols_by_sector(
    db: Session, sector_id: int, level: int
) -> List[StockSymbol]:
    if level == 3:
        return (
            db.query(StockSymbol)
            .filter(StockSymbol.id_sector_level_3 == sector_id)
            .all()
        )
    else:
        return (
            db.query(StockSymbol)
            .filter(StockSymbol.id_sector_level_4 == sector_id)
            .all()
        )


def get_sector_summary(
    db: Session, sector_id: int, level: int
) -> Optional[SectorSummary]:
    sector = get_sector(db, sector_id, level)
    if not sector:
        return None

    stocks = get_stock_symbols_by_sector(db, sector_id, level)
    return SectorSummary(sector=sector, stocks=stocks)


def upsert_sector_from_file(db: Session, file_path: str, level: int) -> int:
    """Load sector JSON and upsert into the sector table."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        if not isinstance(payload, list):
            raise ValueError("Expected top-level JSON array")

        allowed_columns = {
            "id",
            "level",
            "type",
            "name",
            "smg",
            "dif",
            "dif_w",
            "dif_m",
            "dif_3m",
            "vonhoa_d",
            "eps_d",
            "pe_d",
            "pb_d",
            "roa_ttm",
            "roe_ttm",
            "lnst_yoy_ttm",
            "doanhthuthuan_ttm",
            "lnst_ttm",
            "ocf_ttm",
            "lnst_yoy_q",
            "novay_q",
            "tonkho_q",
            "phaithu_q",
            "tts_q",
            "vcsh_q",
        }

        upserted = 0
        for entry in payload:
            if not isinstance(entry, dict):
                continue

            sector_id = entry.get("id")
            metric_type = entry.get("type")
            data_list = entry.get("data", []) or []

            flat = {
                "id": sector_id,
                "level": level,
                "type": str(metric_type) if metric_type is not None else None,
            }

            for item in data_list:
                try:
                    k = item.get("key")
                    v = item.get("value")
                    if k in allowed_columns:
                        flat[k] = v
                    if k == "name":
                        flat["name"] = v
                except Exception:
                    continue

            filtered = {k: v for k, v in flat.items() if k in allowed_columns}
            if "id" not in filtered or filtered["id"] is None:
                continue

            sector = SectorCreate(**filtered)
            upsert_sector(db, sector)
            upserted += 1

        return upserted
    except Exception as err:
        print(f"Error upserting sector: {err}")
        return 0
