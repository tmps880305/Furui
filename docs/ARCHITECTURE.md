# Daily News Collector — 架構與開發規格

> 本文件是給 Claude Code（及其他開發 agent）的開發依據。
> **AWS 資源的建立、IAM 設定、部署皆由人工處理**；agent 只負責程式碼、測試與建置產物。

---

## 0. 範圍與分工

### 0.1 分層概念

| 層 | 內容 | 是否經過 LLM | 第一版 |
|---|---|---|---|
| 抓取核心 | 持有帳密、登入網站、提供受限的抓取操作 | 否 | ✅ 實作 |
| 第一層：規則處理 | 可切換的 pipeline：篩選、去重、擷取、排序、格式化 | 否 | ✅ 實作 |
| 第二層：LLM 處理 | 摘要、分類、萃取等共用的 AI 處理 | 是 | ⏸ 只預留介面 |
| 第三層：個人處理 | 各自 Claude 帳號中的私有 skill，透過 MCP connector 接入 | 是 | ⏸ 只預留介面 |

**每一層結束時都可以選擇從該點匯出結果。** 第一版只支援第一層之後的匯出。

### 0.2 分工

| 項目 | 負責 |
|---|---|
| Lambda 程式碼、pipeline、site adapter、測試、建置產物 | Claude Code |
| AWS 資源建立（Secrets Manager、KMS、S3、DynamoDB、Cognito、API Gateway、SES、EventBridge、IAM） | 人工 |
| 輸入網站帳密 | 帳密擁有者（人工，透過 Console） |
| 部署、修改 IAM / policy | 人工（管理身分）；抓取核心只能由管理身分部署 |

### 0.3 使用者

第一版有兩位使用者：Zack 與 G。兩人可以分別設定各自的抓取條件與處理方式，結果預設只屬於執行者本人，是否分享給對方由執行者每次決定。

---

## 1. 目標

- 每日自動或臨時手動，登入數個新聞網站，依各使用者的條件抓取新聞並處理。
- 帳密只有抓取核心在執行期間能讀到。開發者、帳密擁有者、使用者，以及他們使用的 AI agent，在日常權限下**都無法讀取帳密**。
- 處理層（含未來的 LLM 與個人 skill）永遠接觸不到帳密、cookie、token，只會拿到新聞內容。
- 只有已註冊的使用者能觸發執行或接收結果。

---

## 2. 安全設計原則

1. **讀寫分離**：帳密擁有者只能寫入 secret，只有抓取核心能讀取。
2. **身分隔離**：邊界由 IAM role 與 Cognito 身分定義，不依賴程式自律。
3. **憑證與處理分離**：抓取核心只做抓取；所有處理都在沒有憑證權限的 Lambda 中執行。修改處理邏輯永遠不需要碰到能讀帳密的程式碼。
4. **受限的抓取介面**：抓取核心只接受結構化操作（站點白名單、關鍵字、條件），**絕不接受任意 URL**，避免已登入的 session 被拿去抓其他頁面。
5. **明文帳密只存在單次執行的記憶體中**：不寫入環境變數、檔案、log、S3，也不跨 invocation 保留。
6. **部署權限即讀取權限**：能修改抓取核心的人就等於能讀到帳密，因此只有管理身分能部署抓取核心。
7. **收件者白名單**：匯出只能送給已註冊的使用者，防止未來 LLM 或 skill 被 prompt injection 誘導外送資料。
8. **偵測補足預防**：非預期的 secret 存取、policy 變更、抓取核心部署，都會即時通知。

---

## 3. 架構總覽

```
 使用者（Zack / G）
        │ HTTPS + Cognito JWT
        ▼
 API Gateway（HTTP API, JWT authorizer）
        │
        ▼
┌──────────────────────────┐        EventBridge Scheduler（每日）
│ Orchestrator Lambda      │ ◀──────────────────────────────
│ role-orchestrator        │
│ API、訂閱、建立 run       │ ──▶ DynamoDB：users / subscriptions / runs
└──────────────────────────┘
        │ async invoke（帶 run_id）
        ▼
┌──────────────────────────┐   sync invoke（受限操作）   ┌──────────────────────────┐
│ Pipeline Lambda          │ ─────────────────────────▶ │ Fetcher Lambda（抓取核心）│
│ role-pipeline            │ ◀───────────────────────── │ role-fetcher             │
│ 第一層規則處理            │      正規化後的新聞資料      │ 登入、搜尋、取文章        │
└──────────────────────────┘                            └──────────────────────────┘
        │                                                  │            │
        │ 寫入 stage 結果                                   ▼            ▼
        ▼                                          Secrets Manager   DynamoDB：sessions
 S3：users/{user_id}/runs/{run_id}/                 （帳密，CMK）     （加密 cookie，TTL）
        │
        ▼
 匯出（SES email，收件者限已註冊使用者）

 ── 預留 ──
 第二層：LLM Lambda（role-llm，無憑證權限）讀取 layer1 結果 → 寫入 layer2
 第三層：MCP server，提供 run_pipeline / get_result / search / get_article 給各自的 Claude skill
```

呼叫鏈是固定的：使用者 → API → Orchestrator → Pipeline → Fetcher。任何身分都不能跳過中間層直接呼叫下一層。

---

## 4. 元件規格

### 4.1 技術選型

- Runtime：Python 3.12，arm64
- HTTP client：`httpx`（需能設定 cookie jar 與 host 白名單）
- TOTP（若有網站需要）：`pyotp`
- 需執行 JS 才能登入的網站：Playwright，Fetcher 改用 container image 部署（**僅在必要時採用**）
- AWS SDK：runtime 內建的 `boto3`
- 資料驗證：`pydantic`
- 相依套件需鎖定版本與 hash（`--require-hashes`），套件數量越少越好；Fetcher 的相依套件尤其要精簡

### 4.2 跨平台開發要求（macOS / Windows）

正式執行環境是 Lambda（Linux arm64），但開發、測試與建置必須能在 macOS 和 Windows 上執行：

- 建置與工具腳本一律用 Python 撰寫，不使用 bash 或 PowerShell 專屬語法。
- 路徑一律使用 `pathlib`。
- 打包相依套件時使用 `pip install --platform manylinux2014_aarch64 --only-binary=:all:`；若需要 container image，則以 Docker 建置。
- 加入 `.gitattributes` 統一換行字元（LF）。
- 測試不得依賴特定作業系統的功能。

### 4.3 Fetcher Lambda（抓取核心）

**職責**：持有帳密、登入網站、執行受限的抓取操作、回傳正規化的資料。**不做任何處理。**

**呼叫者**：只有 `role-pipeline`（第三層實作時，再加上 MCP server 的 role）。

**操作（request 為 JSON，`op` 欄位決定操作）**：

| op | 參數 | 回傳 |
|---|---|---|
| `list_sites` | — | 可用站點與各站支援的 filter |
| `search` | `site_id`、`query`、`filters`（`date_from`、`date_to`、`section` 等，依站點而定）、`page` | 文章摘要列表（`article_id`、`title`、`url`、`published_at`、`snippet`） |
| `get_article` | `site_id`、`article_id` | 單篇文章（4.6 節格式） |

**輸入驗證（全部在 Fetcher 內再驗一次，不信任呼叫端）**：

- `site_id` 必須存在於 `config/sites.yaml` 且 `enabled`。
- `query` 只能是字串，長度上限 200，不得包含 URL。
- `filters` 只接受該站宣告支援的欄位與型別。
- `article_id` 必須符合該站 adapter 宣告的格式。
- 回傳資料中不得包含 cookie、token、header 或 request / response 原文。

**Session 快取**：

- 同一個 run 會多次呼叫 Fetcher，每次都重新登入會觸發帳號鎖定，因此登入後的 cookie 要快取。
- 快取存放在 DynamoDB `sessions` 表，以 KMS 加密（加密時帶入 encryption context `site_id`），並設定 TTL（依各站 session 實際壽命調整，上限 12 小時）。
- 每次 invocation 從 DynamoDB 讀取並解密；session 失效時才從 Secrets Manager 讀取帳密重新登入，用完立即丟棄帳密。
- **不得**把帳密或 cookie 放在模組層級或全域變數中。

**並行控制**：reserved concurrency 設為 1，避免同時多次登入同一個網站。Pipeline 遇到 throttling 時要以指數退避重試。

### 4.4 Site adapter 介面

每個網站一個 adapter，放在 `src/fetcher/sites/{site_id}.py`：

| 操作 | 說明 |
|---|---|
| `login` | 傳入已套用白名單的 HTTP client 與帳密，完成登入 |
| `is_session_valid` | 判斷目前 cookie 是否仍有效 |
| `search` | 依 query 與 filters 搜尋，回傳文章摘要列表 |
| `get_article` | 取得單篇文章並轉為純文字 |
| `supported_filters` | 宣告此站支援的 filter 欄位與型別 |
| `article_id_pattern` | 宣告 article_id 的格式 |

- adapter 不得自行讀取 secret，也不得自行建立 HTTP client。
- adapter 不得組合出 `allowed_hosts` 以外的 URL。

### 4.5 Pipeline Lambda（第一層：規則處理）

**職責**：依 pipeline 定義，呼叫 Fetcher 取得資料，執行規則式處理，把結果寫入 S3，並依設定匯出。

**沒有任何憑證權限**：不能讀 secret、不能解密 sessions、不能讀 sessions 表。

**Pipeline 定義**：以 YAML 放在 `config/pipelines/{pipeline_id}.yaml`，由程式碼中的步驟組合而成，並宣告使用者可傳入的參數 schema。

- 抓取階段：要呼叫哪些站、用哪些 query 與 filter，可引用使用者參數。
- 處理步驟（第一版提供）：
  - `filter_keywords`：包含或排除關鍵字
  - `filter_date`：日期範圍
  - `dedupe`：依 URL 或標題相似度去重
  - `fetch_full_text`：對篩選後的項目呼叫 `get_article` 取得全文
  - `extract_fields`：選取輸出欄位
  - `sort_limit`：排序與數量上限
  - `format`：輸出為 `md`、`json` 或 `csv`
- 新增 pipeline 只需要新增 YAML；新增步驟類型才需要修改程式碼。

**流程**：

1. 從 `runs` 表讀取 run 的設定（pipeline、參數、匯出設定）。
2. 依參數 schema 驗證使用者參數。
3. 執行抓取階段與處理步驟。
4. 結果寫入 `users/{user_id}/runs/{run_id}/layer1.{format}`，並更新 `runs` 表中的 stage 狀態。
5. 若匯出設定包含 `after_stage: layer1`，執行匯出（4.8 節）。

### 4.6 資料格式

**文章（Fetcher 回傳、各層共用）**

```
{
  "site_id": "string",
  "article_id": "string",
  "title": "string",
  "url": "string",
  "published_at": "ISO 8601 | null",
  "section": "string | null",
  "body": "string（純文字）"
}
```

**Stage 結果（各層共用的封裝，為第二、三層預留）**

```
{
  "run_id": "string",
  "owner": "user_id",
  "stage": "layer1 | layer2 | layer3",
  "pipeline_id": "string",
  "params": { },
  "generated_at": "ISO 8601",
  "items": [ /* 文章或處理後的項目 */ ],
  "stats": { "fetched": 0, "kept": 0, "failed_sites": ["site_id"] }
}
```

第二層和第三層只能讀取前一層的 stage 結果，不得呼叫 Fetcher 以外的方式取得原始資料。

### 4.7 Orchestrator Lambda（API 與排程）

**API（API Gateway HTTP API，Cognito JWT authorizer）**：

| Method | Path | 說明 |
|---|---|---|
| GET | `/pipelines` | 列出可用的 pipeline 與參數 schema |
| POST | `/runs` | 單次執行：`pipeline_id`、`params`、`exports` |
| GET | `/runs/{run_id}` | 查詢狀態；完成時回傳各 stage 結果的短效 presigned URL |
| GET | `/runs` | 列出自己的 run |
| GET | `/subscriptions` | 列出自己的訂閱 |
| PUT | `/subscriptions/{id}` | 建立或更新訂閱：`pipeline_id`、`params`、`exports`、`enabled` |
| DELETE | `/subscriptions/{id}` | 刪除訂閱 |

**授權規則**：

- `user_id` 一律從 JWT 取得，不接受 request 中自行指定。
- 使用者只能讀寫自己的 run 與訂閱。
- `exports` 中的收件者只能是 `users` 表中已註冊的 `user_id`，不接受任意 email。

**排程**：EventBridge Scheduler 每日觸發 Orchestrator，為每個 `enabled` 的訂閱建立一個 run 並啟動 Pipeline。第一版所有訂閱共用同一個每日執行時間。

### 4.8 匯出

`exports` 是一個列表，每一項指定匯出點與收件者：

```
[
  { "after_stage": "layer1", "to": ["zack", "g"], "format": "md" }
]
```

- 第一版只支援 `after_stage: layer1`；`layer2`、`layer3` 需在驗證時拒絕，並回傳「尚未支援」。
- 寄送使用 SES，收件地址從 `users` 表查詢。
- 信件內容使用該 stage 結果；若過大則只附摘要列表，加上短效 presigned URL。
- 預設 `to` 只有執行者本人；要寄給對方必須明確指定。

### 4.9 DynamoDB 資料表

| 表 | Partition key | 主要欄位 | 存取者 |
|---|---|---|---|
| `users` | `user_id` | `cognito_sub`、`email`、`display_name` | Orchestrator（讀）、Pipeline（讀 email） |
| `subscriptions` | `user_id` + `subscription_id` | `pipeline_id`、`params`、`exports`、`enabled` | Orchestrator |
| `runs` | `run_id` | `owner`、`pipeline_id`、`params`、`exports`、`stages`、`status`、`created_at`、`ttl` | Orchestrator、Pipeline |
| `sessions` | `site_id` | `ciphertext`、`expires_at`（TTL） | **只有 Fetcher** |

### 4.10 預留：第二層（LLM 處理）

第一版不實作，但程式結構要能直接加入：

- 獨立的 LLM Lambda，`role-llm` 只能讀寫 S3 stage 結果與呼叫 Bedrock，**沒有任何憑證或 Fetcher 呼叫權限**。
- 由 Pipeline 定義中的 `layer2` 區塊啟用。
- 新聞內容一律視為**資料而非指令**；prompt 模板需明確區隔指令與內容。

### 4.11 預留：第三層（個人 Claude skill）

第一版不實作，未來以 remote MCP server 的形式提供：

- 工具：`run_pipeline`、`get_result`、`search`、`get_article`。
- 驗證沿用 Cognito（OAuth），各使用者在自己的 Claude 帳號加入 connector。
- skill 只存在使用者自己的帳號中，系統不需要也不會存放 skill 內容。
- 第三層的結果若要回到系統匯出，透過 `submit_result` 寫入 `layer3` stage。

---

## 5. IAM 與權限邊界（人工設定，供開發參考）

| 身分 | 允許 | 明確禁止 |
|---|---|---|
| `role-fetcher` | 讀取 secret、用 CMK 加解密、讀寫 `sessions` 表、CloudWatch Logs | 其他所有權限 |
| `role-pipeline` | invoke Fetcher、讀寫 `runs` 表、讀取 `users` 表、寫入 S3 `users/*/runs/*`、SES 寄信、CloudWatch Logs | 讀取 secret、KMS 解密、存取 `sessions` 表 |
| `role-orchestrator` | 讀寫 `users`、`subscriptions`、`runs` 表、async invoke Pipeline、產生 S3 presigned URL（`users/*` 讀取）、CloudWatch Logs | invoke Fetcher、讀取 secret、存取 `sessions` 表 |
| 使用者（Cognito） | 透過 API 操作自己的資料 | 任何直接的 AWS 存取 |
| 帳密擁有者 | 透過 Console（需 MFA）寫入 secret | 讀取 secret 值；不發 access key |
| 開發者日常身分（SSO） | 讀取 log、部署 Pipeline 與 Orchestrator | 讀取 secret、KMS 解密、存取 `sessions` 表、更新 Fetcher 程式碼或設定、PassRole `role-fetcher` |
| 管理身分（break-glass） | 管理權限，需 MFA，短效 session | — |

**額外限制**：

- `role-fetcher` 的 trust policy 只允許 `lambda.amazonaws.com`。
- Fetcher 的 resource policy 只允許 `role-pipeline` invoke。
- Pipeline 的 resource policy 只允許 `role-orchestrator` invoke。
- Secret 的 resource policy：除了 `role-fetcher` 以外，明確 Deny 所有身分讀取。
- KMS key policy：`Encrypt` / `Decrypt` 只開放給 `role-fetcher`；管理員可以管理金鑰，但不能用它解密。
- （建議）整套服務放在獨立的成員帳號，從管理帳號套用 SCP。

---

## 6. 給 Claude Code 的開發規則（必須遵守）

### MUST NOT

- 不得要求、詢問、輸出或猜測任何真實帳密、cookie、token。
- 不得在 repo、測試、fixture、`.env` 或範例設定中放入真實或看似真實的帳密。
- 不得將帳密放入 Lambda 環境變數。
- 不得將帳密、cookie、token 存放在模組層級或全域變數，也不得使用任何 Secrets Manager 快取機制（Lambda extension、caching client 等）。Lambda 的執行環境在 warm start 時會被重複使用，全域狀態會留到下一次執行。
- 不得 log 登入或抓取相關的 request / response（header、body、cookie），也不得開啟 HTTP client 的 debug log。
- 不得在例外訊息中帶入 request 或 response 內容。
- 不得執行任何 AWS CLI 或 SDK 的部署、IAM、Secrets Manager 相關指令。
- 不得讓 Fetcher 接受 URL 參數，也不得新增對 `allowed_hosts` 以外 host 的請求。
- 不得在 Fetcher 以外的元件中引入讀取 secret、解密 session 或存取 `sessions` 表的程式碼。
- 不得把處理邏輯放進 Fetcher。
- 不得讓匯出接受任意 email 地址。

### MUST

- 讀取 secret 的程式碼**只能**放在 `src/fetcher/secrets.py`；session 加解密**只能**放在 `src/fetcher/session_store.py`。
- 帳密只能存在 handler 呼叫鏈中的區域變數，每次需要時重新讀取，使用完畢立即解除參照。
- Fetcher 對所有輸入重新驗證，不信任呼叫端。
- 所有 log 使用結構化 logger，欄位採**白名單**制：只允許 `run_id`、`user_id`、`site_id`、`op`、`step`、`status_code`、`item_count`、`error_code`、`duration_ms`。
- 錯誤以 `error_code` 表示，例如 `LOGIN_FAILED`、`SESSION_EXPIRED`、`CAPTCHA_DETECTED`、`MFA_REQUIRED`、`PARSE_ERROR`、`INVALID_INPUT`、`HOST_NOT_ALLOWED`、`STAGE_NOT_SUPPORTED`。
- 新增網站時，同時新增 adapter、`sites.yaml` 設定，以及以 fixture 為基礎的測試。
- 新增相依套件前，需在 commit 說明中寫明理由；Fetcher 的相依套件變更需特別標註。

---

## 7. Repo 結構

```
.
├── ARCHITECTURE.md
├── CLAUDE.md                     # 摘錄第 6 節規則，並引用本文件
├── .gitattributes
├── config/
│   ├── sites.yaml                # 站點設定（不含機密）
│   └── pipelines/
│       └── <pipeline_id>.yaml
├── src/
│   ├── common/
│   │   ├── logging.py            # 白名單結構化 logger
│   │   ├── models.py             # 4.6 節的資料結構
│   │   └── errors.py
│   ├── fetcher/
│   │   ├── handler.py
│   │   ├── secrets.py            # 唯一讀取 secret 的地方
│   │   ├── session_store.py      # 唯一加解密 session 的地方
│   │   ├── http_client.py        # 帶 host 白名單的 client 工廠
│   │   ├── validation.py
│   │   └── sites/
│   │       ├── base.py
│   │       └── <site_id>.py
│   ├── pipeline/
│   │   ├── handler.py
│   │   ├── fetcher_client.py     # 呼叫 Fetcher 的封裝（含退避重試）
│   │   ├── loader.py             # 讀取與驗證 pipeline YAML
│   │   ├── steps/                # 第一層處理步驟
│   │   └── exporter.py           # SES 匯出
│   ├── orchestrator/
│   │   ├── handler.py            # API 與排程入口
│   │   ├── api/
│   │   └── auth.py               # 從 JWT 取得 user_id
│   ├── llm/                      # 預留：第二層
│   └── mcp/                      # 預留：第三層
├── tests/
│   ├── fixtures/                 # 已去敏的 HTML / JSON 樣本
│   ├── fetcher/
│   ├── pipeline/
│   └── orchestrator/
├── build/                        # 建置腳本（Python），產出 zip 或 image，不負責部署
└── requirements/
    ├── fetcher.txt
    ├── pipeline.txt
    └── orchestrator.txt
```

---

## 8. 本機開發與測試

- 單元測試使用 fixture 與 mock，**不連線真實網站、不使用真實帳密、不連線 AWS**。
- `secrets.py` 與 `session_store.py` 在測試中以 mock 取代，回傳明顯是假的值（例如 `TEST_USER`）。
- 必要的測試案例：
  - Fetcher 拒絕：未啟用的 `site_id`、含 URL 的 query、未宣告的 filter、格式錯誤的 `article_id`。
  - HTTP client 拒絕非白名單 host。
  - logger 會丟棄白名單以外的欄位。
  - Fetcher 回傳內容不含 cookie、token、header。
  - session 失效時會重新登入，有效時不讀取 secret。
  - Pipeline 步驟各自的單元測試，以及 pipeline YAML 的驗證。
  - 單一站點失敗時，其他站點照常處理，並記錄在 `stats.failed_sites`。
  - Orchestrator 拒絕：讀取他人的 run、在 exports 中使用未註冊的收件者、`after_stage` 為 `layer2` 或 `layer3`。
- **真實網站的整合測試**：由人工部署後執行，檢查 S3 輸出與 log。

---

## 9. 建置與交付

- `build/` 腳本分別為三個 Lambda 產出部署套件。
- Claude Code 的工作在產出建置物後結束，**部署由人工執行**。
- 每次交付需附上變更摘要，並特別標註：是否動到 Fetcher、`secrets.py`、`session_store.py`、logging、HTTP client 或相依套件。動到 Fetcher 的變更需由管理身分審查後部署。

---

## 10. 人工 AWS 設定清單

- [ ] （建議）在 Organizations 中建立獨立的成員帳號
- [ ] 建立 KMS CMK，key policy 中的 `Encrypt` / `Decrypt` 只開放給 `role-fetcher`
- [ ] 建立 secret `news-collector/credentials`（格式見附錄 A），使用上述 CMK
- [ ] 設定 secret 的 resource policy：除了 `role-fetcher` 以外，明確 Deny 讀取
- [ ] 建立帳密擁有者的身分：只能寫入 secret，Console 登入需 MFA，不發 access key
- [ ] 建立 DynamoDB 表：`users`、`subscriptions`、`runs`（啟用 TTL）、`sessions`（啟用 TTL）
- [ ] 建立 S3 bucket：封鎖所有公開存取、預設加密、生命週期規則（例如 run 結果保存 90 天）
- [ ] 建立 Cognito user pool，只建立 Zack 與 G 兩個帳號，關閉自助註冊，啟用 MFA
- [ ] 在 `users` 表中登錄兩人的 `user_id`、`cognito_sub`、`email`
- [ ] 設定 SES：驗證寄件網域或地址；sandbox 模式下也驗證兩位收件者的地址
- [ ] 建立 `role-fetcher`、`role-pipeline`、`role-orchestrator`，依第 5 節設定權限
- [ ] 建立三個 Lambda，設定 Fetcher 的 reserved concurrency 為 1，並設定各自的 resource policy
- [ ] 建立 API Gateway HTTP API，掛上 Cognito JWT authorizer，並設定 throttling
- [ ] 設定 EventBridge Scheduler：台北時區每日觸發 Orchestrator
- [ ] 設定 IAM Identity Center 的權限集合：日常與管理（break-glass）
- [ ] 啟用 CloudTrail，並設定 EventBridge rule 在以下事件發生時通知：
  - 非 `role-fetcher` 呼叫 `GetSecretValue`
  - 修改 secret 的 resource policy 或 KMS key policy
  - 更新 Fetcher 的程式碼、設定或 resource policy
- [ ] （若有獨立成員帳號）套用 SCP

---

## 11. 待決事項

- [ ] 目標網站清單，以及各網站的登入方式（form / api / browser）、MFA 類型、session 壽命
- [ ] 各網站是否會封鎖 AWS 的 IP（需先實測）
- [ ] 第一批 pipeline 的定義（Zack 與 G 各自的需求）
- [ ] 每日執行時間；未來是否需要每個訂閱各自設定時間
- [ ] 確認各網站的服務條款是否允許把內容分享給帳號持有人以外的人
- [ ] 第二層 LLM 的選擇（Bedrock 模型或外部 API）
- [ ] 第三層 MCP server 的部署方式與 OAuth 設定

---

## 附錄 A：Secret 格式

名稱：`news-collector/credentials`，內容為 JSON，以 `site_id` 為 key：

```
{
  "<site_id>": {
    "username": "...",
    "password": "...",
    "totp_secret": "...（選填）"
  }
}
```

repo 中只能出現這份**結構說明**，不得出現任何真實或看似真實的值。
