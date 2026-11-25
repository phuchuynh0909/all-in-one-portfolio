from typing import Annotated, Optional

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.schemas.sector import SectorRotationRequest, SectorRsTimeframe

Symbol = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9._^-]{0,31}$")]


class SymbolStrengthRequest(SectorRotationRequest):
    symbols: list[Symbol] = Field(min_length=1, max_length=2000)
    benchmark: Symbol = "VNINDEX"

    @field_validator("symbols")
    @classmethod
    def unique_symbols(cls, symbols: list[str]) -> list[str]:
        return list(dict.fromkeys(symbols))


class SymbolStrengthRow(BaseModel):
    symbol: str
    rs: list[Optional[float]] = Field(description="Mansfield RS, centered on zero; aligned to dates")
    ratio: list[Optional[float]] = Field(description="RS + 100; benchmark-centered rotation x coordinate")
    momentum: list[Optional[float]] = Field(description="Mansfield of RS-ratio + 100; rotation y coordinate")
    unavailable: Optional[str] = None


class SymbolStrength(BaseModel):
    benchmark: str
    window: int
    momentum_window: int
    timeframe: SectorRsTimeframe
    dates: list[str]
    rows: list[SymbolStrengthRow]
