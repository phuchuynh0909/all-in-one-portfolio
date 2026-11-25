import json
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.models.market import Sector, StockSymbol
from app.services.sector_lists import SECTOR_LEVEL_5, level5_constituents
from app.schemas.sector import SectorBase, StockSymbolCreate, SectorSummary


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
    if level == SECTOR_LEVEL_5:
        # Level 5 membership is multi-tag and lives in the sector map, not in a
        # stock_symbol column; look the rows up by symbol instead.
        symbols = level5_constituents().get(int(sector_id), [])
        if not symbols:
            return []
        return db.query(StockSymbol).filter(StockSymbol.symbol.in_(symbols)).all()

    if level == 1:
        return (
            db.query(StockSymbol)
            .filter(StockSymbol.id_sector_level_1 == sector_id)
            .all()
        )
    elif level == 2:
        return (
            db.query(StockSymbol)
            .filter(StockSymbol.id_sector_level_2 == sector_id)
            .all()
        )
    elif level == 3:
        return (
            db.query(StockSymbol)
            .filter(StockSymbol.id_sector_level_3 == sector_id)
            .all()
        )
    else:  # level == 4
        return (
            db.query(StockSymbol)
            .filter(StockSymbol.id_sector_level_4 == sector_id)
            .all()
        )


def get_all_stock_symbols(db: Session, limit: int = 1000) -> List[StockSymbol]:
    """Get all available stock symbols for portfolio selection"""
    return (
        db.query(StockSymbol)
        .limit(limit)
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
        for index, entry in enumerate(payload, start=1):
            if not isinstance(entry, dict):
                raise ValueError(f"Sector entry {index} must be an object")

            sector_id = entry.get("id")
            metric_type = entry.get("type")
            data_list = entry.get("data", [])
            if not isinstance(data_list, list):
                raise ValueError(f"Sector entry {index} data must be an array")

            flat = {
                "id": sector_id,
                "level": level,
                "type": str(metric_type) if metric_type is not None else None,
            }

            for item in data_list:
                if not isinstance(item, dict) or not isinstance(item.get("key"), str) or "value" not in item:
                    raise ValueError(f"Sector entry {index} contains an invalid metric")
                k = item["key"]
                if k in allowed_columns and k not in {"id", "level"}:
                    flat[k] = item["value"]

            filtered = {k: v for k, v in flat.items() if k in allowed_columns}
            if "id" not in filtered or filtered["id"] is None:
                raise ValueError(f"Sector entry {index} requires an id")

            values = SectorBase(**filtered).model_dump(exclude_unset=True)
            sector = db.get(Sector, (values["id"], level))
            if sector is None:
                db.add(Sector(**values))
            else:
                for key, value in values.items():
                    setattr(sector, key, value)
            db.flush()
            upserted += 1

        db.commit()
        return upserted
    except Exception:
        db.rollback()
        raise
