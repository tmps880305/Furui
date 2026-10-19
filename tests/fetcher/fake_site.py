"""測試用的假網站：adapter 與對應的 httpx MockTransport。不連線任何真實網站。"""

import json
from typing import Any

import httpx

from common.errors import AppError, ErrorCode
from common.models import Article, ArticleSummary
from fetcher.registry import SiteConfig
from fetcher.sites.base import Credentials, FilterSpec, SiteAdapter

HOST = "news.example.test"
TEST_USER = "TEST_USER"
TEST_PASSWORD = "TEST_PASSWORD"
TEST_SESSION = "TEST_SESSION_COOKIE"

CONFIG = SiteConfig(
    site_id="fake_site",
    enabled=True,
    allowed_hosts=frozenset({HOST}),
    session_ttl_seconds=3600,
    adapter="fake_site",
)


class FakeAdapter(SiteAdapter):
    site_id = "fake_site"
    supported_filters = {
        "date_from": FilterSpec("date"),
        "section": FilterSpec("string", choices=("world", "tech")),
    }
    article_id_pattern = r"[0-9]{1,10}"

    def __init__(self, *, url_host: str = HOST) -> None:
        self.url_host = url_host

    def login(self, client: httpx.Client, credentials: Credentials) -> None:
        r = client.post(
            f"https://{HOST}/login",
            data={"user": credentials.username, "pass": credentials.password},
        )
        if r.status_code != 200:
            raise AppError(ErrorCode.LOGIN_FAILED)

    def is_session_valid(self, client: httpx.Client) -> bool:
        return client.get(f"https://{HOST}/me").status_code == 200

    def search(
        self, client: httpx.Client, query: str, filters: dict[str, Any], page: int
    ) -> list[ArticleSummary]:
        r = client.get(f"https://{HOST}/search", params={"q": query, "page": page})
        if r.status_code != 200:
            raise AppError(ErrorCode.SESSION_EXPIRED)
        return [
            ArticleSummary(
                site_id=self.site_id,
                article_id=item["id"],
                title=item["title"],
                url=f"https://{self.url_host}/a/{item['id']}",
                published_at=item["published_at"],
                snippet=item["snippet"],
            )
            for item in r.json()["results"]
        ]

    def get_article(self, client: httpx.Client, article_id: str) -> Article:
        r = client.get(f"https://{HOST}/a/{article_id}")
        if r.status_code != 200:
            raise AppError(ErrorCode.PARSE_ERROR)
        data = r.json()
        return Article(
            site_id=self.site_id,
            article_id=article_id,
            title=data["title"],
            url=f"https://{self.url_host}/a/{article_id}",
            published_at=data["published_at"],
            section=data["section"],
            body=data["body"],
        )


class FakeServer:
    """模擬網站行為，並記錄收到的請求路徑。"""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.login_ok = True

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        authed = f"sid={TEST_SESSION}" in request.headers.get("cookie", "")
        path = request.url.path
        if path == "/login":
            if not self.login_ok:
                return httpx.Response(401)
            return httpx.Response(
                200,
                headers={"set-cookie": f"sid={TEST_SESSION}; Path=/; Domain={HOST}; Secure"},
                text="ok",
            )
        if not authed:
            return httpx.Response(401)
        if path == "/me":
            return httpx.Response(200, text="me")
        if path == "/search":
            results = [
                {
                    "id": "101",
                    "title": "First",
                    "published_at": "2026-10-18T08:00:00+08:00",
                    "snippet": "first snippet",
                },
                {"id": "102", "title": "Second", "published_at": None, "snippet": None},
            ]
            return httpx.Response(200, text=json.dumps({"results": results}))
        if path.startswith("/a/"):
            body = {
                "title": "Article",
                "published_at": "2026-10-18T08:00:00+08:00",
                "section": "world",
                "body": "plain text body",
            }
            return httpx.Response(200, text=json.dumps(body))
        return httpx.Response(404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)
