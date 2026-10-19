import pytest

from common.errors import AppError, ErrorCode
from fetcher.sites.base import FilterSpec
from fetcher.validation import (
    SearchRequest,
    contains_url,
    parse_request,
    validate_article_id,
    validate_filters,
)

SPECS = {
    "date_from": FilterSpec("date"),
    "limit": FilterSpec("int"),
    "section": FilterSpec("string", choices=("world", "tech")),
    "keyword": FilterSpec("string"),
}


def _invalid(fn, *args):
    with pytest.raises(AppError) as exc:
        fn(*args)
    assert exc.value.code == ErrorCode.INVALID_INPUT


def test_parse_search_defaults():
    req = parse_request({"op": "search", "site_id": "s", "query": "台積電 法說會"})
    assert isinstance(req, SearchRequest)
    assert req.page == 1 and req.filters == {}


@pytest.mark.parametrize(
    "event",
    [
        {"op": "search", "site_id": "s", "query": "x" * 201},
        {"op": "search", "site_id": "s", "query": ""},
        {"op": "search", "site_id": "s", "query": 123},
        {"op": "search", "site_id": "s", "query": "x", "page": 0},
        {"op": "search", "site_id": "s", "query": "x", "page": "2"},
        {"op": "search", "site_id": "s", "query": "line\nbreak"},
        {"op": "search", "site_id": "s", "query": "x", "url": "https://a.b"},
        {"op": "get_article", "site_id": "s"},
        {"op": "list_sites", "extra": 1},
        {"op": "unknown"},
        None,
    ],
)
def test_parse_rejects(event):
    _invalid(parse_request, event)


@pytest.mark.parametrize(
    "text",
    [
        "http://a.b",
        "ftp://x",
        "go to www.example",
        "example.com",
        "news.example.org/path",
        "foo.tw",
        "javascript://x",
    ],
)
def test_contains_url_true(text):
    assert contains_url(text)


@pytest.mark.parametrize("text", ["台積電", "AI chips 2026", "U.S. election", "3.5 percent"])
def test_contains_url_false(text):
    assert not contains_url(text)


def test_validate_filters_ok():
    clean = validate_filters({"date_from": "2026-10-01", "limit": 5, "section": "tech"}, SPECS)
    assert clean == {"date_from": "2026-10-01", "limit": 5, "section": "tech"}


@pytest.mark.parametrize(
    "filters",
    [
        {"unknown": "x"},
        {"date_from": "2026-13-01"},
        {"date_from": "2026/10/01"},
        {"limit": True},
        {"limit": "5"},
        {"limit": -1},
        {"section": "sport"},
        {"keyword": "see https://evil.example"},
        {"keyword": ""},
    ],
)
def test_validate_filters_rejects(filters):
    _invalid(validate_filters, filters, SPECS)


def test_validate_article_id():
    validate_article_id("12345", r"[0-9]{1,10}")
    _invalid(validate_article_id, "12345/../x", r"[0-9]{1,10}")
    _invalid(validate_article_id, "", r"[0-9]{1,10}")
