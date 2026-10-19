"""共用資料結構：文章與 stage 結果（ARCHITECTURE.md 4.6 節）。

所有 model 都設定 `extra="forbid"`，避免 cookie、token、header 等額外欄位混入輸出。
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Article(_StrictModel):
    """單篇文章（Fetcher 回傳、各層共用）。body 為純文字。"""

    site_id: str
    article_id: str
    title: str
    url: str
    published_at: datetime | None = None
    section: str | None = None
    body: str


class ArticleSummary(_StrictModel):
    """search 回傳的文章摘要。多站合併時需要 site_id，因此比 4.3 節多帶此欄位。"""

    site_id: str
    article_id: str
    title: str
    url: str
    published_at: datetime | None = None
    snippet: str | None = None


class Stage(StrEnum):
    LAYER1 = "layer1"
    LAYER2 = "layer2"
    LAYER3 = "layer3"


class StageStats(_StrictModel):
    fetched: int = 0
    kept: int = 0
    failed_sites: list[str] = Field(default_factory=list)


class StageResult(_StrictModel):
    """各層共用的 stage 結果封裝。items 可以是文章，或經 extract_fields 等處理後的項目。"""

    run_id: str
    owner: str
    stage: Stage
    pipeline_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime
    items: list[dict[str, Any]] = Field(default_factory=list)
    stats: StageStats = Field(default_factory=StageStats)
