from typing import Any, Optional, Generic, TypeVar
from pydantic import BaseModel
from fastapi.responses import JSONResponse

T = TypeVar("T")

class PaginationMeta(BaseModel):
    total: int
    page: int
    page_size: int
    pages: int

class ResponseEnvelope(BaseModel, Generic[T]):
    success: bool = True
    data: Optional[T] = None
    error: Optional[str] = None
    meta: Optional[dict[str, Any]] = None

def create_response(
    data: Any = None,
    success: bool = True,
    error: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
    status_code: int = 200
) -> JSONResponse:
    """
    Standardize all API responses with a consistent envelope.
    """
    content = ResponseEnvelope(
        success=success,
        data=data,
        error=error,
        meta=meta
    ).model_dump(exclude_none=True)
    
    return JSONResponse(content=content, status_code=status_code)
