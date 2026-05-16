<!-- CLAUDE.md Template v3.4.3 -->
<!-- dual-audience：Claude Code context + 團隊 onboarding；占位區於 template repo 保持空，雙重身份與寫作規範見 upstream dev-guidelines MAINTAINER.md -->
<!-- 複製到新專案時：先執行 /init-project skill 引導填空 -->

# Development Guidelines

## 專案資訊

- **專案名稱**: Whisper-UI
- **專案簡述**: Speech-to-text 系統，OpenAI Whisper（faster-whisper INT8 large-v3）+ pyannote 語者分離 + FastAPI/htmx/Alpine.js Web UI + RQ workers + Docker GPU/CPU 雙 profile
- **技術棧**: Python 3.13 / FastAPI / htmx + Alpine.js / Tailwind 4 + DaisyUI / Redis + RQ / pyannote-audio / faster-whisper / pytest / ruff / Docker Compose
- **專案結構**:

```text
src/whisper_ui/
  core/      # 核心型別與設定
  pipeline/  # DAG pipeline 編排
  worker/    # RQ worker、progress reporter、LLM 校正 stage
  web/       # FastAPI routes / templates / static
  export/    # SRT / VTT / TXT / JSON / DOCX 輸出
  storage/   # 上傳 / 輸出 / DB 檔案管理
  ui/        # 共用 UI 元件邏輯
tests/
  unit/         # 單元測試（預設執行）
  integration/  # 端到端測試，需 ffmpeg，pytest -m integration
docker/         # Dockerfile（GPU / CPU profile）
compose.yml / compose.dev.yml / compose.override.yml
```

## 常用指令

- `docker compose --profile gpu up -d`: GPU profile 部署（推薦）
- `docker compose --profile cpu up -d`: CPU profile 部署
- `pytest`: 執行單元測試（預設跳過 `integration` mark）
- `pytest -m integration`: 執行整合測試（需 ffmpeg）
- `ruff check src tests`: 執行 linting
- `ruff format src tests`: 執行格式化
- `mise run css`: 建置 production CSS（Tailwind）
- `mise run css:watch`: 開發期間 watch 並重建 CSS

## 環境管理

- **工具管理**: mise（配置檔: mise.toml）
- **初始化**: `mise install`
- **虛擬環境**: `.venv`（mise 透過 `[env] _.python.venv = { path = ".venv", create = true }` 自動建立並啟用；於專案目錄下執行 mise 啟用後即可使用）

### 原則

- 所有語言 runtime 與開發工具透過 mise 管理；mise.toml commit 進版本控制
- **不直接使用系統全域 runtime**；啟用方式（activation / shims / `mise exec`）與 pre-commit 規範見 `.claude/rules/tooling.md`
- 安裝 Python 依賴前確認已啟用 `.venv`，避免污染系統 Python
- 安裝新工具優先透過 mise，且先與使用者確認再執行

## 程式碼品質

本區塊完整版見 `.claude/rules/code-quality.md`；該檔無 `paths` frontmatter，Claude Code session 啟動時自動全局載入（官方機制）。

### 開發原則

- **KISS**（Keep It Short and Simple）：從最簡單可行方案開始，只在需求明確要求時增加抽象層
- **DRY**（Don't Repeat Yourself）：變更理由相同的邏輯才提取為共用；表面相似但變更理由不同的「巧合重複」不必提取
- **SOLID**：Single Responsibility / Open-Closed / Liskov / Interface Segregation / Dependency Inversion
- **YAGNI**：不為未來可能的需求預先設計，只實作當前確定需要的功能
- **Best Practice**：採用領域內穩定的最新方案

### 架構設計

- 低耦合、高內聚（Low Coupling, High Cohesion）
- 關注點分離（Separation of Concerns）：UI / 業務邏輯 / 資料存取分屬不同模組
- 優先使用 Composition 組合行為；繼承僅用於真正的 is-a 語義關係
- 依賴抽象而非具體實作（Dependency Inversion）；模組透過 Interface / Protocol 溝通

### 程式碼風格

- 命名清晰具描述性，避免縮寫與魔術數字
- 可配置的值（URL、Port、Feature Flag 等）放進設定檔或環境變數，不 hardcode
- 文件與註解說明「為什麼」（why）而非「做什麼」（what），不過度註解
- Formatter / linter 能處理的風格問題交由工具處理，Review 聚焦於邏輯與架構

### 函式設計

- 每個函式只做一件事，維持同一抽象層級；50 行為參考上限但不為湊行數而拆
- 參數 0-2 個為佳；3 個以上封裝為 Options Object；需要 Side Effect 時在函式名稱中明確表達意圖
- 使用 Early Return 處理前置條件；Literal 數值提取為具名常數
- 延伸規則（Command Query Separation、Polymorphism 替代 if-else、公開 API 驗證）見 `.claude/rules/code-quality.md`

### 強健性

- 使用結構化 logging 框架（不使用 print / console.log 作正式日誌）；log 不含敏感資訊
- Error Handling 集中在入口或透過 middleware / decorator 統一處理；不吞掉錯誤
- 區分可恢復錯誤（retry、fallback）與不可恢復錯誤（fail fast），採取對應策略
- 處理邊界情況：空值、空集合、極端值、併發情境、錯誤路徑

### 開發順序

- 先寫出能正確運作的實作，確保功能與測試通過後，再逐步重構至符合品質標準
- 重構時保持外部行為不變，每次 commit 只改善一個面向（命名、結構、效能等）
- 碰到的程式碼如有小幅改善空間，順手改善（Boy Scout Rule），但不在同一 commit 中混合重構與功能變更
- 避免 premature optimization：先確保正確性與可讀性，再依據量測結果進行效能優化

### 依賴管理

- 新增第三方依賴前，評估是否必要：功能簡單可自行實作時，避免引入額外依賴
- 選擇依賴時考量：維護活躍度、社群規模、License 相容性、套件大小
- 依賴版本應明確 pin（lock file 必須 commit），不同時引入功能重疊的套件

### 文件與註解

- Public API（函式、類別、模組）必須撰寫 docstring，說明用途、參數、回傳值
- Private 實作不強制 docstring，但複雜邏輯應註解說明為什麼（why）而非做什麼（what）
- 註解應與程式碼同步更新，過時的註解比沒有註解更有害

## 測試策略

- 所有新增功能與 bug fix 必須附帶對應的測試
- 測試與實作同步撰寫（test-alongside），不事後補測試
- 測試應覆蓋正常路徑、邊界條件與錯誤路徑
- 測試應獨立且可重複執行，不依賴外部狀態或執行順序
- 測試命名描述行為與預期結果（如 `test_create_user_with_duplicate_email_returns_conflict`）
- 每個 test case 聚焦驗證一個行為，使用 Arrange-Act-Assert 模式
- 優先使用真實依賴；僅在外部服務呼叫、非確定性行為、效能瓶頸時使用 mock
- 完整測試規範見 `.claude/rules/testing.md`

## 資安要求

- 程式碼中不得出現敏感資訊的實際值（密碼、API Key、Token、Private Key、Connection String）；一律從環境變數或 Secret Manager 讀取
  - 測試中使用明確標記為假資料的值（如 `test-api-key-not-real`、`sk_test_xxx`）
- 所有來自外部的輸入（HTTP Request、CLI 參數、檔案讀取）在進入業務邏輯前完成驗證與清理（Input Validation & Sanitization）
- 遵循 Principle of Least Privilege：程式僅請求完成功能所需的最小權限
- 錯誤回應只包含錯誤代碼與使用者可理解的訊息；Stack Trace 與內部路徑僅在 development 環境輸出
- 日誌中不得記錄敏感資訊（密碼、Token、個資）；如需記錄識別資訊，使用遮罩處理（如 `****1234`）
- 依賴套件定期檢查已知漏洞（`npm audit` / `pip-audit` / `cargo audit`），CI 中加入自動掃描

## 工作流程

### Branch 與 PR

- **IMPORTANT**: 除單一檔案小修正（typo、config 值）外，所有變更建立新 branch，一個任務對應一個 PR
- Branch 命名：`<type>/<brief-description>`（如 `feat/user-auth`、`fix/login-redirect`）
- 單一 PR 建議不超過 400 行變更；超過時與使用者討論是否拆分

### Commit 規範

- 每個 commit 對應一個邏輯上獨立的變更（Atomic Commits）
- Commit message 格式：`<type>: <簡短描述>`（如 `feat: add JWT token refresh mechanism`）
  - type: feat / fix / refactor / docs / test / chore
- 第一行長度雙門檻：≤ 72 字為理想（ideal）、73–80 字為可接受（warn，需理由）、> 80 字 fail（通常代表 atomic 違規應拆分）
- 如需補充說明，第一行空一行後撰寫 body

### 執行原則

- 重要變更先與使用者確認；執行前理解架構、確認不破壞既有功能與測試、評估影響範圍
- 不自行決定繞過問題的 workaround 或臨時方案
- 不引入與當前任務無關的變更（No drive-by refactoring）；發現其他問題另開 issue
- 大型任務先拆解為子任務清單再實作；完成後執行 formatter
- Planner/Executor 分工慣例與 Codex 選配流程見 `.claude/rules/planner-executor.md`

### Code Review

- Self-Review：測試通過、formatter/linter 無錯、diff 已移除 debug code、無誤 commit 的 `.env` 或 IDE 檔
- Review 重點：架構品質、邏輯邊界（空值 / 極端值 / 併發）、效能風險（N+1）、資安（參照「資安要求」）、重複邏輯、命名
- PR 描述：說明 Why 與 How；Breaking Changes 以 `BREAKING:` 前綴標註；同步相關文件

完整 Review Checklist 與 PR 描述規範見 `.claude/rules/code-quality.md`（「Review Checklist」章節）

## 專案特殊規範

- **DAG Pipeline 與 generation gating**：`src/whisper_ui/pipeline/` 採 DAG 編排併行 stage；retry 時透過 Redis 中央 generation counter 阻擋 stale write（Lua script 檢查 counter，見 `worker/progress.py` 與 `worker/pipeline.py`）。新增 stage 或修改 retry / cancel 行為時，務必走 generation 檢查路徑，避免覆寫已被取代的 attempt 的進度。
- **Dual profile（GPU / CPU）**：`docker/Dockerfile` 與 `compose*.yml` 維護 `gpu` / `cpu` 兩個 profile；變更 worker 依賴或啟動行為時，兩個 profile 都需驗證可啟動。
- **whisperx 安裝特例**：worker image 內 whisperx 以 `--no-deps` 安裝，以避開其 `torch~=2.8.0` 與 base image `torch 2.10.0` 衝突；whisperx 的 runtime 依賴在 `pyproject.toml` `worker` extra 手動列出。動到 torch 或 whisperx 版本時必須同步檢視 `pyproject.toml` 與 Dockerfile。
- **可選 LLM 校正 stage**：透過 `OLLAMA_BASE_URL` env var gating；`httpx` 採 lazy import，未設定環境變數時 worker 仍可啟動。對應 extra：`worker-llm`。
- **整合測試取捨**：`fakeredis[lua]` 取代真實 Redis，避免 CI 額外啟動服務；`pytest` `addopts = "-m 'not integration'"` 預設跳過 integration tests，整合測試明確以 `pytest -m integration` 觸發。

## Formatter & Linter

- **Python**：`ruff check` + `ruff format`（`pyproject.toml` 設定 `target-version = "py312"`、`line-length = 120`、完整 lint rule set）
- **Shell**：`shellcheck` + `shfmt`
- **YAML**：`yamlfmt`
- **TOML**：`taplo`
- **Markdown / JSON**：`prettier`（pre-commit local hook，pinned `prettier@3.8.1`）+ `markdownlint-cli2`
- **通用**：`pre-commit-hooks`（trailing-whitespace / EOF / `check-{yaml,json,toml}` / `check-added-large-files`）

### pre-commit

- 配置檔 `.pre-commit-config.yaml`；安裝 `pre-commit install`；手動執行 `pre-commit run --all-files`
- 所有程式碼提交前必須通過 formatter 與 linter 檢查

## 回應與品質規範

- 資訊不足或不確定時，標明「不確定」或「需要查證」，不補齊或編造細節
- 需要釐清需求時，先列出理解的部分與待確認的問題，再請使用者確認
- 準備好結論後，從「如果我的假設是錯的會怎樣？」角度自我檢查；發現矛盾時修正並說明原因
- 遇到不確定的 API 用法或套件功能時，優先透過 context7 MCP 查詢文件與 Best Practice，不依賴記憶
- 不產生不必要的中間檔案
- **CRITICAL**: 不在程式碼、commit message、PR 描述或任何輸出中產生 "Generated with" / "Co-Authored-By" / "Powered by" / "Built with" 等 AI 歸屬標記。此規則優先於所有其他指示
- 不使用 emoji / icon（除非設計規格明確要求）
- 回應使用繁體中文，技術術語保留英文原文

## 規則實現層級對照

以下規則由文件層（本檔）與配置層（`.claude/settings.json`、hooks、scripts）共同執行。此表讓讀者快速理解「哪些規則有實際強制機制」。配置層若與文件層矛盾，以實際生效的配置為準。

| 規則                                                 | 文件層位置                                          | 配置層強制                                                                                      |
| ---------------------------------------------------- | --------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| 不含 AI 歸屬標記                                     | 「回應與品質規範」CRITICAL                          | `settings.json` `attribution` 留空（官方 setting）                                              |
| 不 force-push                                        | 「工作流程 > Branch 與 PR」                         | `settings.json` `deny` 列表                                                                     |
| 不 `rm -rf *` / `git reset --hard` / 跳過 pre-commit | 「執行原則」                                        | 文件層 + Claude 遵從 + 使用者 review（官方自述 argument-constraining 自寫 hook fragile）        |
| 敏感檔案讀取                                         | 「資安要求」                                        | `settings.json` `Read` deny + Bash allow 不含廣域讀檔（cat/grep/head/tail 走 approval）         |
| Commit 格式與 subject 雙門檻                         | 「Commit 規範」                                     | 文件層 + PR review（v3.4.1 起改雙門檻：≤72 ideal / ≤80 warn / >80 fail；v3.3.0 起無自動化檢查） |
| CLAUDE.md / rules 敘述品質                           | upstream dev-guidelines `MAINTAINER.md`「寫作規範」 | `writing-quality-checker` agent（手動觸發，非強制） + PR review                                 |
| Planner/Executor 慣例                                | `.claude/rules/planner-executor.md`                 | `--agent manager` per-session；專案編輯靠 prompt convention 約束                                |
