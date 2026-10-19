"""唯一讀取 secret 的地方。

- 每次呼叫都重新向 Secrets Manager 讀取，不做任何快取。
- 回傳的 Credentials 只能存在呼叫端的區域變數中，用完立即解除參照。
- 錯誤時不帶入 secret 內容，並以 `from None` 切斷例外鏈（JSONDecodeError 等會夾帶原文）。

設定（非機密）：環境變數 CREDENTIALS_SECRET_ID，預設 news-collector/credentials。
secret 格式見 ARCHITECTURE.md 附錄 A。
"""

import json
import os

import boto3

from common.errors import AppError, ErrorCode
from fetcher.sites.base import Credentials

DEFAULT_SECRET_ID = "news-collector/credentials"  # noqa: S105 - secret 名稱，不是值


def get_credentials(site_id: str) -> Credentials:
    secret_id = os.environ.get("CREDENTIALS_SECRET_ID", DEFAULT_SECRET_ID)
    client = boto3.client("secretsmanager")
    try:
        payload = client.get_secret_value(SecretId=secret_id)["SecretString"]
    except Exception:
        raise AppError(ErrorCode.LOGIN_FAILED, "credentials unavailable") from None
    try:
        entry = json.loads(payload).get(site_id)
        if not isinstance(entry, dict):
            raise AppError(ErrorCode.LOGIN_FAILED, "credentials missing for site")
        return Credentials(
            username=str(entry["username"]),
            password=str(entry["password"]),
            totp_secret=str(entry["totp_secret"]) if entry.get("totp_secret") else None,
        )
    except AppError:
        raise
    except Exception:
        raise AppError(ErrorCode.LOGIN_FAILED, "credentials malformed") from None
    finally:
        del payload
