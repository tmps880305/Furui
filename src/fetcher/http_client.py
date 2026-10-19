"""帶 host 白名單的 HTTP client 工廠。

白名單在 transport 層檢查，因此每一次請求（包含 redirect 的每一跳）都會被檢查。
只允許 https，且 host 必須完全符合 allowed_hosts。
"""

import logging
from collections.abc import Iterable
from typing import Any

import httpx

from common.errors import AppError, ErrorCode

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; DailyNewsCollector/0.1)"

# httpx / httpcore 在 INFO / DEBUG 會 log 請求 URL 與 header，一律壓到 WARNING。
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)


class AllowlistTransport(httpx.BaseTransport):
    def __init__(self, allowed_hosts: frozenset[str], inner: httpx.BaseTransport) -> None:
        self._allowed_hosts = allowed_hosts
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.scheme != "https" or url.host not in self._allowed_hosts or url.userinfo:
            raise AppError(ErrorCode.HOST_NOT_ALLOWED)
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


def create_client(
    allowed_hosts: Iterable[str],
    cookies: list[dict[str, Any]] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """建立只能連線到 allowed_hosts 的 client。

    `transport` 只供測試注入 MockTransport；正式環境使用預設的 HTTPTransport。
    """
    hosts = frozenset(h.lower() for h in allowed_hosts)
    if not hosts:
        raise AppError(ErrorCode.HOST_NOT_ALLOWED, "empty allowlist")
    inner = transport if transport is not None else httpx.HTTPTransport(retries=1)
    client = httpx.Client(
        transport=AllowlistTransport(hosts, inner),
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
        max_redirects=5,
        trust_env=False,
        headers={"User-Agent": DEFAULT_USER_AGENT},
    )
    for cookie in cookies or []:
        domain = str(cookie.get("domain", "")).lstrip(".").lower()
        if domain and domain not in hosts and not any(h.endswith("." + domain) for h in hosts):
            continue
        client.cookies.set(
            str(cookie["name"]),
            str(cookie["value"]),
            domain=str(cookie.get("domain", "")),
            path=str(cookie.get("path", "/")),
        )
    return client


def export_cookies(client: httpx.Client) -> list[dict[str, Any]]:
    """取出 cookie 供 session_store 加密保存。回傳值只能交給 session_store。"""
    return [
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
        for c in client.cookies.jar
    ]
