"""站點註冊表：讀取 config/sites.yaml 並載入對應的 adapter。

sites.yaml 格式（不含任何機密）：

    sites:
      <site_id>:
        enabled: true
        allowed_hosts: [www.example.com]
        session_ttl_seconds: 3600   # 上限 43200（12 小時）
        adapter: <module_name>       # 選填，預設等於 site_id；對應 fetcher/sites/<module_name>.py
"""

import importlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from common.errors import AppError, ErrorCode
from fetcher.sites.base import SiteAdapter

MAX_SESSION_TTL_SECONDS = 12 * 60 * 60
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")


@dataclass(frozen=True)
class SiteConfig:
    site_id: str
    enabled: bool
    allowed_hosts: frozenset[str]
    session_ttl_seconds: int
    adapter: str


def parse_site_configs(raw: Any) -> dict[str, SiteConfig]:
    if not isinstance(raw, dict) or not isinstance(raw.get("sites") or {}, dict):
        raise ValueError("sites.yaml: top-level 'sites' mapping required")
    configs: dict[str, SiteConfig] = {}
    for site_id, entry in (raw.get("sites") or {}).items():
        if not isinstance(site_id, str) or not _IDENTIFIER.match(site_id):
            raise ValueError("sites.yaml: invalid site_id")
        if not isinstance(entry, dict):
            raise ValueError(f"sites.yaml: {site_id} must be a mapping")
        hosts = entry.get("allowed_hosts") or []
        if not hosts or not all(isinstance(h, str) and _HOSTNAME.match(h.lower()) for h in hosts):
            raise ValueError(f"sites.yaml: {site_id} needs valid allowed_hosts")
        ttl = entry.get("session_ttl_seconds", 3600)
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
            raise ValueError(f"sites.yaml: {site_id} invalid session_ttl_seconds")
        adapter = entry.get("adapter", site_id)
        if not isinstance(adapter, str) or not _IDENTIFIER.match(adapter) or adapter == "base":
            raise ValueError(f"sites.yaml: {site_id} invalid adapter")
        configs[site_id] = SiteConfig(
            site_id=site_id,
            enabled=entry.get("enabled", False) is True,
            allowed_hosts=frozenset(h.lower() for h in hosts),
            session_ttl_seconds=min(ttl, MAX_SESSION_TTL_SECONDS),
            adapter=adapter,
        )
    return configs


def default_config_path() -> Path:
    """Lambda 套件中 config/ 與 fetcher/ 同層；repo 中 config/ 在 src/ 的上一層。"""
    override = os.environ.get("SITES_CONFIG_PATH")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for root in (here.parents[1], here.parents[2]):
        candidate = root / "config" / "sites.yaml"
        if candidate.is_file():
            return candidate
    return here.parents[1] / "config" / "sites.yaml"


def load_site_configs(path: Path) -> dict[str, SiteConfig]:
    with path.open(encoding="utf-8") as f:
        return parse_site_configs(yaml.safe_load(f))


def load_adapter(config: SiteConfig) -> SiteAdapter:
    module = importlib.import_module(f"fetcher.sites.{config.adapter}")
    adapter_cls = getattr(module, "Adapter", None)
    if not (isinstance(adapter_cls, type) and issubclass(adapter_cls, SiteAdapter)):
        raise ValueError(f"fetcher.sites.{config.adapter} must define Adapter(SiteAdapter)")
    return adapter_cls()


class SiteRegistry:
    def __init__(self, configs: dict[str, SiteConfig], adapters: dict[str, SiteAdapter]) -> None:
        self._configs = configs
        self._adapters = adapters

    @classmethod
    def from_config_file(cls, path: Path | None = None) -> "SiteRegistry":
        configs = load_site_configs(path or default_config_path())
        adapters = {sid: load_adapter(c) for sid, c in configs.items() if c.enabled}
        return cls(configs, adapters)

    def enabled_sites(self) -> list[tuple[SiteConfig, SiteAdapter]]:
        return [
            (c, self._adapters[sid])
            for sid, c in sorted(self._configs.items())
            if c.enabled and sid in self._adapters
        ]

    def get_enabled(self, site_id: str) -> tuple[SiteConfig, SiteAdapter]:
        config = self._configs.get(site_id)
        if config is None or not config.enabled or site_id not in self._adapters:
            raise AppError(ErrorCode.INVALID_INPUT, "unknown or disabled site")
        return config, self._adapters[site_id]
