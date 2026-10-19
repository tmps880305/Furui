"""Fetcher 輸入驗證，不信任呼叫端（ARCHITECTURE.md 4.3 節）。

驗證分兩段：
1. `parse_request`：結構驗證（op、欄位型別、長度），不認識的欄位一律拒絕。
2. `validate_filters` / `validate_article_id`：依站點宣告的 filter 與 article_id 格式驗證。

錯誤一律 raise AppError(INVALID_INPUT)，訊息不帶入使用者輸入。
"""

import re
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    TypeAdapter,
    ValidationError,
)

from common.errors import AppError, ErrorCode
from fetcher.sites.base import FilterSpec

MAX_QUERY_LENGTH = 200
MAX_FILTER_STRING_LENGTH = 100
MAX_ARTICLE_ID_LENGTH = 200
MAX_PAGE = 50

_URL_PATTERNS = (
    re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE),
    re.compile(r"\bwww\.", re.IGNORECASE),
    re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+/", re.IGNORECASE),
    re.compile(
        r"\b[a-z0-9-]+(\.[a-z0-9-]+)*\.(com|net|org|edu|gov|io|co|tw|hk|cn|jp|uk|info|biz|me|app|dev)\b",
        re.IGNORECASE,
    ),
)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListSitesRequest(_Request):
    op: Literal["list_sites"]


class SearchRequest(_Request):
    op: Literal["search"]
    site_id: StrictStr = Field(max_length=64)
    query: StrictStr = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    filters: dict[StrictStr, Any] = Field(default_factory=dict)
    page: StrictInt = Field(default=1, ge=1, le=MAX_PAGE)


class GetArticleRequest(_Request):
    op: Literal["get_article"]
    site_id: StrictStr = Field(max_length=64)
    article_id: StrictStr = Field(min_length=1, max_length=MAX_ARTICLE_ID_LENGTH)


FetcherRequest = Annotated[
    ListSitesRequest | SearchRequest | GetArticleRequest, Field(discriminator="op")
]
_request_adapter: TypeAdapter[FetcherRequest] = TypeAdapter(FetcherRequest)


def contains_url(text: str) -> bool:
    return any(p.search(text) for p in _URL_PATTERNS)


def _check_free_text(text: str) -> None:
    if _CONTROL_CHARS.search(text):
        raise AppError(ErrorCode.INVALID_INPUT, "control characters")
    if contains_url(text):
        raise AppError(ErrorCode.INVALID_INPUT, "contains url")


def parse_request(event: Any) -> ListSitesRequest | SearchRequest | GetArticleRequest:
    try:
        request = _request_adapter.validate_python(event)
    except ValidationError:
        raise AppError(ErrorCode.INVALID_INPUT, "malformed request") from None
    if isinstance(request, SearchRequest):
        _check_free_text(request.query)
    return request


def validate_filters(filters: dict[str, Any], specs: dict[str, FilterSpec]) -> dict[str, Any]:
    """只接受站點宣告的 filter，回傳正規化後的值。"""
    clean: dict[str, Any] = {}
    for name, value in filters.items():
        spec = specs.get(name)
        if spec is None:
            raise AppError(ErrorCode.INVALID_INPUT, "undeclared filter")
        clean[name] = _validate_filter_value(value, spec)
    return clean


def _validate_filter_value(value: Any, spec: FilterSpec) -> Any:
    if spec.type == "date":
        if not isinstance(value, str) or not _DATE_PATTERN.match(value):
            raise AppError(ErrorCode.INVALID_INPUT, "invalid date filter")
        try:
            date.fromisoformat(value)
        except ValueError:
            raise AppError(ErrorCode.INVALID_INPUT, "invalid date filter") from None
        result: Any = value
    elif spec.type == "int":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10_000:
            raise AppError(ErrorCode.INVALID_INPUT, "invalid int filter")
        result = value
    else:
        if not isinstance(value, str) or not 0 < len(value) <= MAX_FILTER_STRING_LENGTH:
            raise AppError(ErrorCode.INVALID_INPUT, "invalid string filter")
        _check_free_text(value)
        result = value
    if spec.choices is not None and str(result) not in spec.choices:
        raise AppError(ErrorCode.INVALID_INPUT, "filter value not allowed")
    return result


def validate_article_id(article_id: str, pattern: str) -> None:
    if not re.fullmatch(pattern, article_id):
        raise AppError(ErrorCode.INVALID_INPUT, "invalid article_id")
