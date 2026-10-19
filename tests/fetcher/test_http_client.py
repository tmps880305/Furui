import httpx
import pytest

from common.errors import AppError, ErrorCode
from fetcher.http_client import create_client, export_cookies

HOST = "news.example.test"


def _client(handler, cookies=None):
    return create_client({HOST}, cookies, transport=httpx.MockTransport(handler))


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="ok")


def test_allowlisted_host_passes():
    with _client(ok) as client:
        assert client.get(f"https://{HOST}/x").status_code == 200


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.test/x",
        f"https://sub.{HOST}/x",
        f"http://{HOST}/x",
        f"https://user:pw@{HOST}/x",
    ],
)
def test_rejects_non_allowlisted_requests(url):
    with _client(ok) as client, pytest.raises(AppError) as exc:
        client.get(url)
    assert exc.value.code == ErrorCode.HOST_NOT_ALLOWED


def test_redirect_to_other_host_is_blocked():
    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example.test/steal"})

    with _client(redirect) as client, pytest.raises(AppError) as exc:
        client.get(f"https://{HOST}/start")
    assert exc.value.code == ErrorCode.HOST_NOT_ALLOWED


def test_error_message_does_not_contain_url():
    with _client(ok) as client, pytest.raises(AppError) as exc:
        client.get("https://evil.example.test/secret-path?token=abc")
    assert "evil" not in str(exc.value) and "token" not in str(exc.value)


def test_empty_allowlist_rejected():
    with pytest.raises(AppError):
        create_client([])


def test_cookies_for_foreign_domains_are_dropped():
    cookies = [
        {"name": "a", "value": "1", "domain": HOST, "path": "/"},
        {"name": "b", "value": "2", "domain": "evil.example.test", "path": "/"},
    ]
    with _client(ok, cookies) as client:
        assert {c["name"] for c in export_cookies(client)} == {"a"}


def test_trust_env_disabled():
    with _client(ok) as client:
        assert client.trust_env is False
