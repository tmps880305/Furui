"""以 error_code 表示的錯誤類型（ARCHITECTURE.md 第 6 節）。

例外訊息只能是 error_code 與固定的簡短說明，不得帶入 request / response、
帳密、cookie 或使用者輸入的內容。
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    LOGIN_FAILED = "LOGIN_FAILED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    CAPTCHA_DETECTED = "CAPTCHA_DETECTED"
    MFA_REQUIRED = "MFA_REQUIRED"
    PARSE_ERROR = "PARSE_ERROR"
    INVALID_INPUT = "INVALID_INPUT"
    HOST_NOT_ALLOWED = "HOST_NOT_ALLOWED"
    STAGE_NOT_SUPPORTED = "STAGE_NOT_SUPPORTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    """帶 error_code 的應用程式錯誤。

    `detail` 只能放程式碼中寫死的固定字串（例如 "query contains url"），
    不得由外部資料組成。
    """

    def __init__(self, code: ErrorCode, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code.value if detail is None else f"{code.value}: {detail}")
