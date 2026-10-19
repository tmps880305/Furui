"""Fetcher Lambda 入口：list_sites / search / get_article（ARCHITECTURE.md 4.3 節）。

回傳格式：
    成功 {"ok": true, "data": {...}}
    失敗 {"ok": false, "error_code": "<ErrorCode>"}

只回傳正規化後的文章資料；不得回傳 cookie、token、header 或 request / response 原文。
不做任何處理邏輯（篩選、去重、排序等都在 Pipeline）。
"""

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from common.errors import AppError, ErrorCode
from common.logging import get_logger
from common.models import Article, ArticleSummary
from fetcher import secrets, session_store
from fetcher.http_client import create_client, export_cookies
from fetcher.registry import SiteConfig, SiteRegistry
from fetcher.sites.base import SiteAdapter
from fetcher.validation import (
    GetArticleRequest,
    SearchRequest,
    parse_request,
    validate_article_id,
    validate_filters,
)

ClientFactory = Callable[..., httpx.Client]

log = get_logger("fetcher")


def handler(event: Any, context: Any) -> dict[str, Any]:
    return dispatch(event, SiteRegistry.from_config_file())


def dispatch(
    event: Any,
    registry: SiteRegistry,
    client_factory: ClientFactory = create_client,
) -> dict[str, Any]:
    started = time.monotonic()
    op = event.get("op") if isinstance(event, dict) else None
    op_name = op if op in ("list_sites", "search", "get_article") else None
    site_id: str | None = None
    try:
        request = parse_request(event)
        if isinstance(request, SearchRequest | GetArticleRequest):
            site_id = request.site_id
        data = _execute(request, registry, client_factory)
    except AppError as e:
        _log_done(op_name, site_id, started, error_code=e.code)
        return {"ok": False, "error_code": e.code.value}
    except Exception:
        _log_done(op_name, site_id, started, error_code=ErrorCode.INTERNAL_ERROR)
        return {"ok": False, "error_code": ErrorCode.INTERNAL_ERROR.value}
    _log_done(op_name, site_id, started, item_count=data.get("item_count"))
    data.pop("item_count", None)
    return {"ok": True, "data": data}


def _log_done(
    op: str | None,
    site_id: str | None,
    started: float,
    *,
    error_code: ErrorCode | None = None,
    item_count: int | None = None,
) -> None:
    fields: dict[str, Any] = {
        "op": op,
        "site_id": site_id,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
    if error_code is not None:
        log.warning("fetcher.request.failed", error_code=error_code.value, **fields)
    else:
        log.info("fetcher.request.done", item_count=item_count, **fields)


def _execute(request: Any, registry: SiteRegistry, client_factory: ClientFactory) -> dict[str, Any]:
    if isinstance(request, SearchRequest):
        config, adapter = registry.get_enabled(request.site_id)
        filters = validate_filters(request.filters, adapter.supported_filters)
        client = _open_session(config, adapter, client_factory)
        try:
            summaries = adapter.search(client, request.query, filters, request.page)
        finally:
            client.close()
        items = [_check_summary(s, config) for s in summaries]
        return {"items": items, "item_count": len(items)}

    if isinstance(request, GetArticleRequest):
        config, adapter = registry.get_enabled(request.site_id)
        validate_article_id(request.article_id, adapter.article_id_pattern)
        client = _open_session(config, adapter, client_factory)
        try:
            article = adapter.get_article(client, request.article_id)
        finally:
            client.close()
        return {"article": _check_article(article, config, request.article_id), "item_count": 1}

    sites = [
        {
            "site_id": config.site_id,
            "filters": {
                name: {"type": spec.type, "choices": list(spec.choices) if spec.choices else None}
                for name, spec in adapter.supported_filters.items()
            },
        }
        for config, adapter in registry.enabled_sites()
    ]
    return {"sites": sites, "item_count": len(sites)}


def _open_session(
    config: SiteConfig, adapter: SiteAdapter, client_factory: ClientFactory
) -> httpx.Client:
    """回傳已登入的 client。session 有效時不讀取 secret。"""
    cookies = session_store.load(config.site_id)
    if cookies is not None:
        client = client_factory(config.allowed_hosts, cookies)
        del cookies
        try:
            if adapter.is_session_valid(client):
                return client
        except AppError:
            pass
        client.close()
        log.info("fetcher.session.expired", site_id=config.site_id)

    client = client_factory(config.allowed_hosts, None)
    try:
        credentials = secrets.get_credentials(config.site_id)
        try:
            adapter.login(client, credentials)
        finally:
            del credentials
    except AppError:
        client.close()
        raise
    except Exception:
        client.close()
        raise AppError(ErrorCode.LOGIN_FAILED) from None
    session_store.save(config.site_id, export_cookies(client), config.session_ttl_seconds)
    return client


def _check_url(url: str, config: SiteConfig) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "") not in config.allowed_hosts:
        raise AppError(ErrorCode.PARSE_ERROR, "article url outside allowlist")
    if parts.username or parts.password:
        raise AppError(ErrorCode.PARSE_ERROR, "article url has userinfo")


def _check_summary(summary: ArticleSummary, config: SiteConfig) -> dict[str, Any]:
    summary = ArticleSummary.model_validate(summary.model_dump())
    if summary.site_id != config.site_id:
        raise AppError(ErrorCode.PARSE_ERROR, "site_id mismatch")
    _check_url(summary.url, config)
    return summary.model_dump(mode="json")


def _check_article(article: Article, config: SiteConfig, article_id: str) -> dict[str, Any]:
    article = Article.model_validate(article.model_dump())
    if article.site_id != config.site_id or article.article_id != article_id:
        raise AppError(ErrorCode.PARSE_ERROR, "article identity mismatch")
    _check_url(article.url, config)
    return article.model_dump(mode="json")
