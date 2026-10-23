"""Public search result contracts shared by semantic, keyword and hybrid routes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MatchedChunk(BaseModel):
    chunk_id: str
    column: str


class SearchHit(BaseModel):
    table_id: str
    row_id: str
    row_revision: int
    path: str
    score: float
    score_type: str
    snippet: str
    approximate: bool
    indexed_revision: int | None = None
    matched_chunks: list[MatchedChunk] | None = None


class SearchMeta(BaseModel):
    model_config = ConfigDict(extra="allow")
    request_id: str
    partial: bool = False
    approximate: bool | None = None
    actual_mode: Literal["fast", "quality"] | None = None
    index_backend: str | None = None
    model_revision: str | None = None
    missing_embeddings: int | None = None
    candidate_limit: int | None = None
    candidate_count: int | None = None
    quality_model_id: str | None = None
    quality_model_revision: str | None = None
    quality_dimensions: int | None = None
    quality_preprocessing_revision: str | None = None
    cache_hits: int | None = None
    encoded_chunks: int | None = None
    quality_fusion: str | None = None


class SearchResponse(BaseModel):
    data: list[SearchHit]
    meta: SearchMeta


class SearchErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict = Field(default_factory=dict)


class SearchErrorResponse(BaseModel):
    error: SearchErrorDetail


SEARCH_ERRORS = {
    status: {"model": SearchErrorResponse, "description": description}
    for status, description in (
        (403, "Scope or resource permission denied."),
        (422, "Invalid search request or quality encoding input."),
        (429, "Rate or quality concurrency limit exceeded."),
        (503, "Search dependency unavailable or incompatible."),
    )
}
