"""Site adapter 介面（ARCHITECTURE.md 4.4 節）。

- adapter 不得自行讀取 secret，也不得自行建立 HTTP client；client 與帳密都由 handler 傳入。
- adapter 不得組合出 allowed_hosts 以外的 URL（http_client 也會在傳輸層擋下）。
- adapter 應為無狀態：不得把帳密或 cookie 存在 instance 或 class 屬性上。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

import httpx

from common.models import Article, ArticleSummary

FilterType = Literal["date", "string", "int"]


@dataclass(frozen=True)
class FilterSpec:
    type: FilterType
    choices: tuple[str, ...] | None = None


@dataclass(frozen=True, repr=False)
class Credentials:
    username: str
    password: str
    totp_secret: str | None = None

    def __repr__(self) -> str:
        return "Credentials(<redacted>)"

    __str__ = __repr__


class SiteAdapter(ABC):
    site_id: ClassVar[str]
    supported_filters: ClassVar[dict[str, FilterSpec]] = {}
    article_id_pattern: ClassVar[str]
    """article_id 必須完整符合（fullmatch）的正規表示式。"""

    @abstractmethod
    def login(self, client: httpx.Client, credentials: Credentials) -> None:
        """完成登入；失敗時 raise AppError（LOGIN_FAILED / CAPTCHA_DETECTED / MFA_REQUIRED）。"""

    @abstractmethod
    def is_session_valid(self, client: httpx.Client) -> bool: ...

    @abstractmethod
    def search(
        self,
        client: httpx.Client,
        query: str,
        filters: dict[str, Any],
        page: int,
    ) -> list[ArticleSummary]: ...

    @abstractmethod
    def get_article(self, client: httpx.Client, article_id: str) -> Article: ...
