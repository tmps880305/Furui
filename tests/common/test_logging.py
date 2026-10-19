import json

from common.logging import ALLOWED_FIELDS, format_record, get_logger, sanitize_fields


def test_drops_fields_outside_whitelist():
    clean = sanitize_fields(
        {
            "site_id": "s",
            "password": "TEST_PASSWORD",
            "cookie": "sid=x",
            "headers": {"a": "b"},
            "url": "https://x",
        }
    )
    assert clean == {"site_id": "s"}


def test_drops_non_scalar_values_and_truncates():
    clean = sanitize_fields({"run_id": {"nested": 1}, "op": ["a"], "step": "x" * 1000})
    assert "run_id" not in clean and "op" not in clean
    assert len(clean["step"]) == 128


def test_keeps_all_whitelisted_scalars():
    fields = {name: 1 for name in ALLOWED_FIELDS}
    assert sanitize_fields(fields) == fields


def test_invalid_event_is_replaced():
    record = json.loads(format_record("INFO", "t", "Login with TEST_PASSWORD", {}))
    assert record["event"] == "invalid_event"


def test_logger_writes_json_line(capsys):
    get_logger("test").info("unit.event", site_id="s", secret="TEST_PASSWORD")
    out = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(out)
    assert record == {"level": "INFO", "logger": "test", "event": "unit.event", "site_id": "s"}
    assert "TEST_PASSWORD" not in out
