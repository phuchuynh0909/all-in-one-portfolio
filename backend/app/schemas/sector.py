from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel


class SectorBase(BaseModel):
    id: int
    level: int
    type: Optional[str] = None
    name: Optional[str] = None
    smg: Optional[Decimal] = None
    dif: Optional[Decimal] = None
    dif_w: Optional[Decimal] = None
    dif_m: Optional[Decimal] = None
    dif_3m: Optional[Decimal] = None
    vonhoa_d: Optional[Decimal] = None
    eps_d: Optional[Decimal] = None
    pe_d: Optional[Decimal] = None
    pb_d: Optional[Decimal] = None
    roa_ttm: Optional[Decimal] = None
    roe_ttm: Optional[Decimal] = None
    lnst_yoy_ttm: Optional[Decimal] = None
    doanhthuthuan_ttm: Optional[Decimal] = None
    lnst_ttm: Optional[Decimal] = None
    ocf_ttm: Optional[Decimal] = None
    lnst_yoy_q: Optional[Decimal] = None
    novay_q: Optional[Decimal] = None
    tonkho_q: Optional[Decimal] = None
    phaithu_q: Optional[Decimal] = None
    tts_q: Optional[Decimal] = None
    vcsh_q: Optional[Decimal] = None


class SectorCreate(SectorBase):
    pass


class Sector(SectorBase):
    created_at: datetime

    class Config:
        from_attributes = True


class StockSymbolBase(BaseModel):
    symbol: str
    name: Optional[str] = None
    id_sector_level_3: Optional[int] = None
    id_sector_level_4: Optional[int] = None
    vonhoa_d: Optional[Decimal] = None


class StockSymbolCreate(StockSymbolBase):
    pass


class StockSymbol(StockSymbolBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SectorSummary(BaseModel):
    sector: Sector
    stocks: List[StockSymbol]
