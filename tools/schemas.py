# Shared response and chunk models for metric APIs.
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field

class TextChunk(BaseModel):
    """带原文偏移量的文本分段。"""

    chunk_id: str
    index: int = Field(ge=0)
    text: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)

class StandardResponse(BaseModel):
    """FastAPI 统一响应包装。"""

    success: bool
    data: Any = None
    error: dict[str, Any] | None = None
