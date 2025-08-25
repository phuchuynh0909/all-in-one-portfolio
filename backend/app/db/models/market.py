from sqlalchemy import Column, Integer, String, DECIMAL, TIMESTAMP, text
from app.db.base import Base


class Sector(Base):
    __tablename__ = "sector"

    # Composite primary key to support multiple levels per id
    id = Column(Integer, primary_key=True)
    level = Column(Integer, primary_key=True)

    type = Column(String(10))
    name = Column(String(255))
    smg = Column(DECIMAL(20, 8))
    dif = Column(DECIMAL(20, 8))
    dif_w = Column(DECIMAL(20, 8))
    dif_m = Column(DECIMAL(20, 8))
    dif_3m = Column(DECIMAL(20, 8))
    vonhoa_d = Column(DECIMAL(20, 8))
    eps_d = Column(DECIMAL(20, 8))
    pe_d = Column(DECIMAL(20, 8))
    pb_d = Column(DECIMAL(20, 8))
    roa_ttm = Column(DECIMAL(20, 8))
    roe_ttm = Column(DECIMAL(20, 8))
    lnst_yoy_ttm = Column(DECIMAL(20, 8))
    doanhthuthuan_ttm = Column(DECIMAL(20, 8))
    lnst_ttm = Column(DECIMAL(20, 8))
    ocf_ttm = Column(DECIMAL(20, 8))
    lnst_yoy_q = Column(DECIMAL(20, 8))
    novay_q = Column(DECIMAL(20, 8))
    tonkho_q = Column(DECIMAL(20, 8))
    phaithu_q = Column(DECIMAL(20, 8))
    tts_q = Column(DECIMAL(20, 8))
    vcsh_q = Column(DECIMAL(20, 8))
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))


class StockSymbol(Base):
    __tablename__ = "stock_symbol"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    name = Column(String(255))
    id_sector_level_3 = Column(Integer, nullable=True)
    id_sector_level_4 = Column(Integer, nullable=True)
    vonhoa_d = Column(DECIMAL(20, 8), nullable=True)
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

