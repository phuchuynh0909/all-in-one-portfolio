from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class HealthStrengthRow(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    symbol: str
    name: Optional[str] = None
    health: Optional[float] = Field(default=None, ge=0, le=100)
    financial_period: Optional[str] = None
    health_peers: int = 0
    components: dict[str, float]
    strength: Optional[float] = Field(default=None, ge=1, le=99)
    previous_strength: Optional[float] = Field(default=None, ge=1, le=99)
    turnover_vnd: Optional[float] = Field(default=None, ge=0)
    excess_returns: dict[str, Optional[float]]
    unavailable: Optional[str] = None


class HealthStrength(BaseModel):
    as_of: Optional[str] = None
    universe: int
    rows: list[HealthStrengthRow]
