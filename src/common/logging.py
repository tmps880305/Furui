"""白名單結構化 logger（ARCHITECTURE.md 第 6 節）。

每筆 log 是一行 JSON：`level`、`logger`、`event` 加上白名單欄位。
- 白名單以外的欄位一律丟棄。
- 欄位值只接受 str / int / float / bool / None，其他型別丟棄；字串過長會截斷。
- `event` 必須是程式碼中寫死的識別字（例如 "fetch.search.done"），不符合格式會被替換。
"""

import json
import logging
import re
import sys
from typing import Any

ALLOWED_FIELDS = frozenset(
    {
        "run_id",
        "user_id",
        "site_id",
        "op",
        "step",
        "status_code",
        "item_count",
        "error_code",
        "duration_ms",
    }
)

MAX_VALUE_LENGTH = 128
_EVENT_PATTERN = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_INVALID_EVENT = "invalid_event"
_ROOT_LOGGER_NAME = "news_collector"


def sanitize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """只保留白名單欄位與純量值。"""
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in ALLOWED_FIELDS:
            continue
        if isinstance(value, str):
            clean[key] = value[:MAX_VALUE_LENGTH]
        elif value is None or isinstance(value, bool | int | float):
            clean[key] = value
    return clean


def format_record(level: str, logger_name: str, event: str, fields: dict[str, Any]) -> str:
    if not isinstance(event, str) or not _EVENT_PATTERN.match(event):
        event = _INVALID_EVENT
    record = {"level": level, "logger": logger_name, "event": event}
    record.update(sanitize_fields(fields))
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


class _StdoutHandler(logging.StreamHandler):
    """每次輸出都使用當下的 sys.stdout（測試時 stdout 會被替換）。"""

    @property
    def stream(self):  # type: ignore[override]
        return sys.stdout

    @stream.setter
    def stream(self, value) -> None:
        pass


def _base_logger() -> logging.Logger:
    base = logging.getLogger(_ROOT_LOGGER_NAME)
    if not base.handlers:
        handler = _StdoutHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return base


class StructuredLogger:
    def __init__(self, name: str) -> None:
        self._name = name
        self._logger = _base_logger().getChild(name)

    def _log(self, level: int, event: str, fields: dict[str, Any]) -> None:
        if self._logger.isEnabledFor(level):
            message = format_record(logging.getLevelName(level), self._name, event, fields)
            self._logger.log(level, message)

    def debug(self, event: str, **fields: Any) -> None:
        self._log(logging.DEBUG, event, fields)

    def info(self, event: str, **fields: Any) -> None:
        self._log(logging.INFO, event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._log(logging.WARNING, event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._log(logging.ERROR, event, fields)


def get_logger(name: str) -> StructuredLogger:
    return StructuredLogger(name)
