# CLAUDE.md

本專案是 **Daily News Collector**：在 AWS Lambda 上每日登入數個新聞網站、依使用者條件抓取並以規則處理新聞，結果寄給已註冊使用者（第一版：Zack、G）。

完整規格見 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，開發前必讀。本文件摘錄第 6 節的強制規則；兩者衝突時以 ARCHITECTURE.md 為準。

## 分工

- Claude Code 只負責：程式碼、測試、建置產物（`build/` 產出 zip 或 image）。
- AWS 資源建立、IAM、輸入帳密、部署一律由人工處理。

## MUST NOT

- 不得要求、詢問、輸出或猜測任何真實帳密、cookie、token。
- 不得在 repo、測試、fixture、`.env` 或範例設定中放入真實或看似真實的帳密。
- 不得將帳密放入 Lambda 環境變數。
- 不得將帳密、cookie、token 存放在模組層級或全域變數，也不得使用任何 Secrets Manager 快取機制（Lambda extension、caching client 等）。warm start 會重複使用執行環境。
- 不得 log 登入或抓取相關的 request / response（header、body、cookie），也不得開啟 HTTP client 的 debug log。
- 不得在例外訊息中帶入 request 或 response 內容。
- 不得執行任何 AWS CLI 或 SDK 的部署、IAM、Secrets Manager 相關指令。
- 不得讓 Fetcher 接受 URL 參數，也不得新增對 `allowed_hosts` 以外 host 的請求。
- 不得在 Fetcher 以外的元件中引入讀取 secret、解密 session 或存取 `sessions` 表的程式碼。
- 不得把處理邏輯放進 Fetcher。
- 不得讓匯出接受任意 email 地址。

## MUST

- 讀取 secret 的程式碼**只能**放在 `src/fetcher/secrets.py`；session 加解密**只能**放在 `src/fetcher/session_store.py`。
- 帳密只能存在 handler 呼叫鏈中的區域變數，每次需要時重新讀取，使用完畢立即解除參照。
- Fetcher 對所有輸入重新驗證，不信任呼叫端。
- 所有 log 使用 `src/common/logging.py` 的結構化 logger，欄位白名單：`run_id`、`user_id`、`site_id`、`op`、`step`、`status_code`、`item_count`、`error_code`、`duration_ms`。
- 錯誤以 `error_code` 表示：`LOGIN_FAILED`、`SESSION_EXPIRED`、`CAPTCHA_DETECTED`、`MFA_REQUIRED`、`PARSE_ERROR`、`INVALID_INPUT`、`HOST_NOT_ALLOWED`、`STAGE_NOT_SUPPORTED`。
- 新增網站時，同時新增 adapter（`src/fetcher/sites/{site_id}.py`）、`config/sites.yaml` 設定，以及以 fixture 為基礎的測試。
- 新增相依套件前，需在 commit 說明中寫明理由；Fetcher 的相依套件變更需特別標註。

## 開發環境

- Python 3.12，目標 Lambda arm64；開發、測試、建置需能在 macOS 與 Windows 執行。
- 建置與工具腳本一律用 Python，不寫 bash / PowerShell 專屬語法；路徑一律用 `pathlib`。
- 打包相依套件：`pip install --platform manylinux2014_aarch64 --only-binary=:all:`。
- 相依套件鎖定版本與 hash（`requirements/*.txt`，`--require-hashes`），越少越好。
- 單元測試只用 fixture 與 mock：不連真實網站、不用真實帳密、不連 AWS。`secrets.py` 與 `session_store.py` 在測試中 mock 為明顯假的值（例如 `TEST_USER`）。

## 交付

每次交付附上變更摘要，並標註是否動到：Fetcher、`secrets.py`、`session_store.py`、logging、HTTP client、相依套件。動到 Fetcher 的變更需由管理身分審查後部署。
