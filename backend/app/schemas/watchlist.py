from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

ListId = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]+$", min_length=1, max_length=72)]
Item = Annotated[str, Field(min_length=1, max_length=120)]


class Watchlist(BaseModel):
    id: ListId
    title: str = Field(min_length=1, max_length=80)
    symbols: list[Item] = Field(default_factory=list, max_length=1000)

    @field_validator("id")
    @classmethod
    def safe_id(cls, value):
        if value in {"__proto__", "constructor", "prototype"}:
            raise ValueError("Invalid watchlist ID")
        return value

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value):
        if not value.strip():
            raise ValueError("Enter a watchlist name")
        return value.strip()

    @field_validator("symbols")
    @classmethod
    def normalize_items(cls, items):
        result, seen = [], set()
        for raw in items:
            item = raw.strip()
            if not item:
                continue
            if item.startswith("###"):
                result.append(item)
            else:
                symbol = item.upper()
                if symbol not in seen:
                    result.append(symbol)
                    seen.add(symbol)
        return result


class WatchlistWrite(BaseModel):
    lists: list[Watchlist] = Field(min_length=1, max_length=100)
    active_list_id: ListId

    @model_validator(mode="after")
    def valid_selection(self):
        ids = [item.id for item in self.lists]
        if len(ids) != len(set(ids)):
            raise ValueError("Watchlist IDs must be unique")
        if self.active_list_id not in ids:
            raise ValueError("Selected watchlist does not exist")
        return self


class WatchlistLibraryOut(BaseModel):
    lists: list[Watchlist] = Field(default_factory=list)
    active_list_id: ListId | None = None
    imported_ids: list[str] = Field(default_factory=list)
