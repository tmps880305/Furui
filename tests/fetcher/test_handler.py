import json
from typing import Any

import pytest

from common.errors import AppError, ErrorCode
from fetcher import handler as fetcher_handler
from fetcher.http_client import create_client
from fetcher.registry import SiteConfig, SiteRegistry
from fetcher.sites.base import Credentials
from tests.fetcher import fake_site
from tests.fetcher.fake_site import CONFIG, TEST_PASSWORD, TEST_SESSION, TEST_USER


class FakeSessionStore:
    def __init__(self, cookies: list[dict[str, Any]] | None = None) -> None:
        self.cookies = cookies
        self.saved: list[tuple[str, list[dict[str, Any]], int]] = []

    def load(self, site_id: str) -> list[dict[str, Any]] | None:
        return self.cookies

    def save(self, site_id: str, cookies: list[dict[str, Any]], ttl_seconds: int) -> None:
        self.saved.append((site_id, cookies, ttl_seconds))
        self.cookies = cookies


class Env:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, cookies=None, adapter=None) -> None:
        self.server = fake_site.FakeServer()
        self.store = FakeSessionStore(cookies)
        self.secret_reads = 0
        self.adapter = adapter or fake_site.FakeAdapter()
        disabled = SiteConfig(
            site_id="disabled_site",
            enabled=False,
            allowed_hosts=frozenset({"other.example.test"}),
            session_ttl_seconds=60,
            adapter="disabled_site",
        )
        self.registry = SiteRegistry(
            {"fake_site": CONFIG, "disabled_site": disabled}, {"fake_site": self.adapter}
        )

        def get_credentials(site_id: str) -> Credentials:
            self.secret_reads += 1
            return Credentials(username=TEST_USER, password=TEST_PASSWORD)

        monkeypatch.setattr(fetcher_handler.secrets, "get_credentials", get_credentials)
        monkeypatch.setattr(fetcher_handler.session_store, "load", self.store.load)
        monkeypatch.setattr(fetcher_handler.session_store, "save", self.store.save)

    def call(self, event: Any) -> dict[str, Any]:
        def factory(hosts, cookies):
            return create_client(hosts, cookies, transport=self.server.transport())

        return fetcher_handler.dispatch(event, self.registry, client_factory=factory)


VALID_COOKIES = [{"name": "sid", "value": TEST_SESSION, "domain": fake_site.HOST, "path": "/"}]
SEARCH = {"op": "search", "site_id": "fake_site", "query": "election"}


def test_list_sites_only_enabled(monkeypatch):
    env = Env(monkeypatch)
    result = env.call({"op": "list_sites"})
    assert result["ok"] is True
    assert [s["site_id"] for s in result["data"]["sites"]] == ["fake_site"]
    assert result["data"]["sites"][0]["filters"]["section"]["choices"] == ["world", "tech"]


def test_search_without_session_logs_in_and_caches(monkeypatch):
    env = Env(monkeypatch)
    result = env.call(SEARCH)
    assert result["ok"] is True
    assert [i["article_id"] for i in result["data"]["items"]] == ["101", "102"]
    assert env.secret_reads == 1
    assert env.server.paths[0] == "/login"
    assert env.store.saved and env.store.saved[0][0] == "fake_site"
    assert any(c["value"] == TEST_SESSION for c in env.store.saved[0][1])


def test_valid_session_does_not_read_secret(monkeypatch):
    env = Env(monkeypatch, cookies=VALID_COOKIES)
    result = env.call(SEARCH)
    assert result["ok"] is True
    assert env.secret_reads == 0
    assert "/login" not in env.server.paths
    assert env.store.saved == []


def test_expired_session_relogs_in(monkeypatch):
    stale = [{"name": "sid", "value": "STALE", "domain": fake_site.HOST, "path": "/"}]
    env = Env(monkeypatch, cookies=stale)
    result = env.call(SEARCH)
    assert result["ok"] is True
    assert env.secret_reads == 1
    assert env.server.paths[:2] == ["/me", "/login"]


def test_session_cached_across_invocations(monkeypatch):
    env = Env(monkeypatch)
    env.call(SEARCH)
    env.call({"op": "get_article", "site_id": "fake_site", "article_id": "101"})
    assert env.secret_reads == 1


def test_get_article(monkeypatch):
    env = Env(monkeypatch, cookies=VALID_COOKIES)
    result = env.call({"op": "get_article", "site_id": "fake_site", "article_id": "101"})
    assert result["ok"] is True
    article = result["data"]["article"]
    assert set(article) == {
        "site_id",
        "article_id",
        "title",
        "url",
        "published_at",
        "section",
        "body",
    }
    assert article["body"] == "plain text body"


def test_login_failure_returns_error_code_only(monkeypatch):
    env = Env(monkeypatch)
    env.server.login_ok = False
    result = env.call(SEARCH)
    assert result == {"ok": False, "error_code": "LOGIN_FAILED"}
    assert env.store.saved == []


def test_unexpected_login_exception_is_masked(monkeypatch):
    env = Env(monkeypatch)

    def boom(client, credentials):
        raise RuntimeError(f"leak {credentials.password}")

    monkeypatch.setattr(env.adapter, "login", boom)
    result = env.call(SEARCH)
    assert result == {"ok": False, "error_code": "LOGIN_FAILED"}


@pytest.mark.parametrize(
    "event",
    [
        {"op": "search", "site_id": "disabled_site", "query": "x"},
        {"op": "search", "site_id": "no_such_site", "query": "x"},
        {"op": "search", "site_id": "fake_site", "query": "see https://evil.example/x"},
        {"op": "search", "site_id": "fake_site", "query": "evil.com"},
        {"op": "search", "site_id": "fake_site", "query": "x", "filters": {"author": "y"}},
        {"op": "search", "site_id": "fake_site", "query": "x", "filters": {"section": "sport"}},
        {"op": "search", "site_id": "fake_site", "query": "x", "filters": {"date_from": "10/1"}},
        {"op": "search", "site_id": "fake_site", "query": "x", "url": "https://evil.example"},
        {"op": "get_article", "site_id": "fake_site", "article_id": "../etc"},
        {"op": "get_article", "site_id": "fake_site", "article_id": "https://evil.example/1"},
        {"op": "fetch_url", "url": "https://evil.example"},
        "not a dict",
    ],
)
def test_rejects_invalid_input_without_touching_site(monkeypatch, event):
    env = Env(monkeypatch)
    result = env.call(event)
    assert result == {"ok": False, "error_code": "INVALID_INPUT"}
    assert env.server.paths == []
    assert env.secret_reads == 0


def test_adapter_url_outside_allowlist_rejected(monkeypatch):
    env = Env(
        monkeypatch, cookies=VALID_COOKIES, adapter=fake_site.FakeAdapter(url_host="evil.test")
    )
    result = env.call(SEARCH)
    assert result == {"ok": False, "error_code": "PARSE_ERROR"}


def test_response_and_logs_contain_no_secrets(monkeypatch, capsys):
    env = Env(monkeypatch)
    outputs = [
        env.call(SEARCH),
        env.call({"op": "get_article", "site_id": "fake_site", "article_id": "101"}),
    ]
    env.server.login_ok = False
    env.store.cookies = None
    outputs.append(env.call(SEARCH))
    dumped = json.dumps(outputs) + capsys.readouterr().out
    for forbidden in (TEST_PASSWORD, TEST_USER, TEST_SESSION, "set-cookie", "cookie", "header"):
        assert forbidden.lower() not in dumped.lower()


def test_logs_are_whitelisted_json(monkeypatch, capsys):
    env = Env(monkeypatch)
    env.call(SEARCH)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    done = [line for line in lines if line["event"] == "fetcher.request.done"]
    assert done and done[0]["op"] == "search" and done[0]["item_count"] == 2


def test_app_error_message_has_no_payload():
    err = AppError(ErrorCode.INVALID_INPUT, "contains url")
    assert str(err) == "INVALID_INPUT: contains url"
