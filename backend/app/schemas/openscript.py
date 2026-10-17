from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

ScriptId = Annotated[str, Field(pattern=r"^user_[a-zA-Z0-9_-]+$", max_length=72)]
ScriptSource = Annotated[str, Field(min_length=1, max_length=100_000)]


class OpenScriptWrite(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    source: ScriptSource
    applied_source: ScriptSource | None = None
    visible: bool = False


class OpenScriptOut(OpenScriptWrite):
    model_config = ConfigDict(from_attributes=True)
    id: ScriptId


class OpenScriptVisibility(BaseModel):
    id: ScriptId
    visible: bool
