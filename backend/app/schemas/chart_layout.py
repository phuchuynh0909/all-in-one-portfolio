import json
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.openscript import ScriptId

LayoutId = Annotated[str, Field(pattern=r"^(layout[123]|custom_[a-zA-Z0-9_-]+)$", max_length=72)]
IndicatorId = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_]+$", max_length=72)]
Parameter = Annotated[float, Field(allow_inf_nan=False)]


class ChartLayoutName(BaseModel):
    name: str = Field(min_length=1, max_length=60)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("Enter a layout name")
        return value.strip()


class ChartLayoutWrite(ChartLayoutName):
    indicators: list[IndicatorId] = Field(default_factory=list, max_length=200)
    params: dict[IndicatorId, dict[str, Parameter]] = Field(default_factory=dict, max_length=200)
    scripts: list[ScriptId] = Field(default_factory=list, max_length=500)
    chart_state: dict[str, Any] | None = None

    @field_validator("chart_state")
    @classmethod
    def bounded_snapshot(cls, value):
        if value is not None and len(json.dumps(value, allow_nan=False).encode("utf-8")) > 2_000_000:
            raise ValueError("Chart state must be under 2 MB")
        return value


class ChartLayoutOut(ChartLayoutWrite):
    model_config = ConfigDict(from_attributes=True)
    id: LayoutId


class ChartLayoutsOut(BaseModel):
    layouts: list[ChartLayoutOut]
    active_layout_id: LayoutId | None = None


class ChartLayoutSelection(BaseModel):
    layout_id: LayoutId


class ChartLayoutImport(BaseModel):
    layouts: list[ChartLayoutOut] = Field(max_length=100)
    active_layout_id: LayoutId | None = None
