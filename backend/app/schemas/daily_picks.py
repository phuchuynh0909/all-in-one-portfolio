"""Contracts for generated daily stock-pick snapshots."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class _DailyPicksModel(BaseModel):
    model_config = ConfigDict(protected_namespaces=())


class DailyPickComponents(_DailyPicksModel):
    """Auditable inputs behind one composite ranking score."""

    model_probability: float = Field(ge=0.0, le=1.0)
    relative_strength_rank: float = Field(ge=0.0, le=1.0)
    trend_rank: float = Field(ge=0.0, le=1.0)
    liquidity_rank: float = Field(ge=0.0, le=1.0)
    volatility_safety_rank: float = Field(ge=0.0, le=1.0)


class DailyPick(_DailyPicksModel):
    rank: int = Field(ge=1)
    symbol: str
    score: float = Field(ge=0.0, le=100.0)
    model_probability: float = Field(ge=0.0, le=1.0)
    signal_versions: list[str] = Field(default_factory=list)
    components: DailyPickComponents
    risk_flags: list[str] = Field(default_factory=list)


class DailyPickSnapshot(_DailyPicksModel):
    """One immutable result generated from one completed EOD session."""

    as_of: date
    generated_at: datetime
    model_version: str
    universe_size: int = Field(ge=0)
    eligible_size: int = Field(ge=0)
    candidate_size: int = Field(ge=0)
    picks: list[DailyPick] = Field(default_factory=list)


class DailyPickHistory(_DailyPicksModel):
    """Recent immutable snapshots, ordered oldest to newest."""

    snapshots: list[DailyPickSnapshot] = Field(default_factory=list)
