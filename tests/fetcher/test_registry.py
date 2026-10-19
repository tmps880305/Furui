from pathlib import Path

import pytest

from common.errors import AppError
from fetcher.registry import MAX_SESSION_TTL_SECONDS, SiteRegistry, parse_site_configs

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_parse_valid_config():
    configs = parse_site_configs(
        {
            "sites": {
                "daily_news": {
                    "enabled": True,
                    "allowed_hosts": ["www.Example.com", "login.example.com"],
                    "session_ttl_seconds": 999_999,
                },
                "off_site": {"allowed_hosts": ["off.example.com"]},
            }
        }
    )
    daily = configs["daily_news"]
    assert daily.enabled is True
    assert daily.allowed_hosts == frozenset({"www.example.com", "login.example.com"})
    assert daily.session_ttl_seconds == MAX_SESSION_TTL_SECONDS
    assert daily.adapter == "daily_news"
    assert configs["off_site"].enabled is False


@pytest.mark.parametrize(
    "raw",
    [
        {"sites": {"Bad-Id": {"allowed_hosts": ["a.example.com"]}}},
        {"sites": {"s": {"allowed_hosts": []}}},
        {"sites": {"s": {"allowed_hosts": ["https://a.example.com"]}}},
        {"sites": {"s": {"allowed_hosts": ["a.example.com"], "adapter": "../x"}}},
        {"sites": {"s": {"allowed_hosts": ["a.example.com"], "adapter": "base"}}},
        {"sites": {"s": {"allowed_hosts": ["a.example.com"], "session_ttl_seconds": 0}}},
        {"sites": ["s"]},
        "nope",
    ],
)
def test_parse_rejects_invalid(raw):
    with pytest.raises(ValueError):
        parse_site_configs(raw)


def test_repo_sites_yaml_loads():
    registry = SiteRegistry.from_config_file(REPO_ROOT / "config" / "sites.yaml")
    assert registry.enabled_sites() == []


def test_get_enabled_unknown_site():
    with pytest.raises(AppError):
        SiteRegistry({}, {}).get_enabled("x")
