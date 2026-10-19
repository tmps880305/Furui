"""唯一加解密 session 的地方（DynamoDB sessions 表 + KMS）。

- cookie 以 KMS Encrypt 直接加密，encryption context 帶 site_id。
  KMS 直接加密的明文上限為 4096 bytes，超過時視為無法快取（每次重新登入）。
- TTL 上限 12 小時；DynamoDB TTL 刪除有延遲，因此讀取時也檢查 expires_at。
- 解密失敗或資料格式錯誤一律視為沒有 session，由 handler 重新登入。

設定（非機密）：環境變數 SESSIONS_TABLE（預設 sessions）、SESSION_KMS_KEY_ID（必填）。
"""

import json
import os
import time
from typing import Any

import boto3

from common.logging import get_logger

MAX_TTL_SECONDS = 12 * 60 * 60
MAX_PLAINTEXT_BYTES = 4096

log = get_logger("fetcher.session_store")


def _table_name() -> str:
    return os.environ.get("SESSIONS_TABLE", "sessions")


def load(site_id: str) -> list[dict[str, Any]] | None:
    dynamodb = boto3.client("dynamodb")
    try:
        item = dynamodb.get_item(
            TableName=_table_name(), Key={"site_id": {"S": site_id}}, ConsistentRead=True
        ).get("Item")
    except Exception:
        log.warning("session.load_failed", site_id=site_id)
        return None
    if not item:
        return None
    try:
        if int(item["expires_at"]["N"]) <= int(time.time()):
            return None
        plaintext = boto3.client("kms").decrypt(
            CiphertextBlob=item["ciphertext"]["B"],
            EncryptionContext={"site_id": site_id},
        )["Plaintext"]
        cookies = json.loads(plaintext)
        del plaintext
    except Exception:
        log.warning("session.decrypt_failed", site_id=site_id)
        return None
    return cookies if isinstance(cookies, list) else None


def save(site_id: str, cookies: list[dict[str, Any]], ttl_seconds: int) -> None:
    plaintext = json.dumps(cookies, separators=(",", ":")).encode("utf-8")
    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        del plaintext
        log.warning("session.too_large", site_id=site_id)
        return
    try:
        ciphertext = boto3.client("kms").encrypt(
            KeyId=os.environ["SESSION_KMS_KEY_ID"],
            Plaintext=plaintext,
            EncryptionContext={"site_id": site_id},
        )["CiphertextBlob"]
    except Exception:
        log.warning("session.encrypt_failed", site_id=site_id)
        return
    finally:
        del plaintext
    expires_at = int(time.time()) + min(ttl_seconds, MAX_TTL_SECONDS)
    try:
        boto3.client("dynamodb").put_item(
            TableName=_table_name(),
            Item={
                "site_id": {"S": site_id},
                "ciphertext": {"B": ciphertext},
                "expires_at": {"N": str(expires_at)},
            },
        )
    except Exception:
        log.warning("session.save_failed", site_id=site_id)


def delete(site_id: str) -> None:
    try:
        boto3.client("dynamodb").delete_item(
            TableName=_table_name(), Key={"site_id": {"S": site_id}}
        )
    except Exception:
        log.warning("session.delete_failed", site_id=site_id)
