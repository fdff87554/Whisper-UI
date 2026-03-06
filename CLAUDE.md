# Development Guidelines

## 專案資訊

<!-- 根據專案填入 -->

- **專案名稱**: Whisper-UI
- **技術棧**: Python 3.12, Streamlit, OpenAI Whisper, Docker
- **專案結構**: 初始設定階段，尚無程式碼目錄

## 常用指令

<!-- 根據專案填入 -->

- `mise install`: 安裝開發環境
- `pytest`: 執行測試（待建立）
- `pre-commit run --all-files`: 執行 linting & formatting
- `ruff format . && ruff check --fix .`: 僅執行 Python 格式化與 lint

---

## 環境管理

<!-- 根據專案填入 -->

- **工具管理**: mise
- **配置檔**: mise.toml
- **初始化指令**: `mise install`
- **虛擬環境**: `.venv/`（由 mise 自動建立）
- **虛擬環境啟用指令**: 自動（mise `_.python.venv` 配置），手動: `source .venv/bin/activate`

### 原則

- 使用 mise 統一管理語言 runtime 與開發工具，避免依賴系統全域安裝
- mise.toml 必須 commit 至版本控制，作為環境的 single source of truth
- 專案操作前應確認 mise 環境已啟用且工具版本正確
- Python 專案的虛擬環境應位於專案根目錄下的 .venv，並已加入 .gitignore
- 安裝依賴前應確認已啟用正確的虛擬環境
- 不直接使用系統全域的語言 runtime 或工具，所有操作應透過 mise 管理的版本執行

## 開發原則

- 遵循 KISS 原則（Keep It Short and Simple）
- 遵循 DRY 原則（Don't Repeat Yourself）
- 遵循 SOLID 原則
- 遵循領域 Best Practice，採用最新穩定的實作方案
- 專案需保持高可維護性、高可讀性、高強健性
- 程式碼與結構應讓協作者能快速理解專案現況與功能

## 程式碼品質

### 架構設計

- 低耦合、高內聚（Low Coupling, High Cohesion）
- 關注點分離（Separation of Concerns）
- 單一職責，每個模組 / 函式只做一件事
- 優先使用組合而非繼承（Composition over Inheritance）
- 依賴抽象而非具體實作（Dependency Inversion）

### 程式碼風格

- 命名清晰具描述性，避免縮寫與魔術數字
- 函式保持簡潔乾淨，單一函式不超過 50 行為佳
- 適當的錯誤處理與邊界條件檢查
- 適當的文件與註解，但不過度註解
- 避免深層巢狀，提早返回（Early Return）

### 強健性

- 防禦性程式設計，驗證輸入參數
- 完善的錯誤處理與日誌記錄
- 避免硬編碼，使用設定檔或環境變數
- 考慮並處理邊界情況與異常狀況

## 資安要求

- 不在程式碼中硬編碼敏感資訊（密碼、API Key、Token），除非為測試用途的假資料
- 所有使用者輸入需進行驗證與清理（Input Validation & Sanitization）
- 遵循最小權限原則（Principle of Least Privilege）
- 避免暴露敏感的錯誤訊息與堆疊追蹤
- 依賴套件需注意已知漏洞，保持更新
- 日誌記錄不得包含敏感資訊

## 工作流程

- **IMPORTANT**: 除非是非常簡單的小修改，所有變更必須建立新 branch 進行，完成後提交 Pull Request 供團隊 Code Review
- **IMPORTANT**: 同一次任務討論中產生的所有變更，應整合在同一個 Branch 與同一個 PR 中完成，不得切分為多個 Branch 或多個 PR
- 同一個 Branch 中的 commit 應保持原子性（Atomic Commits），每個 commit 對應一個邏輯上獨立的變更，並撰寫清晰的 commit message
- 所有調整需先與使用者確認後再執行
- 不接受臨時性的調整與方案
- 完成變動後執行 formatter 確保程式碼風格一致

## 執行變更前

- 理解相關模組的現有架構與設計決策
- 確認變更不會破壞現有功能與測試
- 評估變更的影響範圍

## Code Review 規範

### Review 重點

- 程式碼是否符合專案架構與設計原則
- 邏輯正確性與邊界條件處理
- 是否有潛在的效能問題或資安風險
- 命名與可讀性是否清晰
- 測試覆蓋是否足夠
- 是否有重複程式碼可抽取

### Review 產出

- PR 描述需清楚說明變更目的與影響範圍
- 如有破壞性變更（Breaking Changes）需明確標註
- 相關文件需同步更新

## 回應規範

- 資訊不足或不確定時，標明「不確定」或「需要查證」，不補齊或編造細節
- 需要釐清需求時，主動與使用者確認
- 準備好結論後，用另一個角度自我檢查，如發現錯誤或矛盾則修正並說明原因

## 品質要求

- 不產生不必要的中間檔案
- 不產生 "Generated with" / "Co-Authored-By" / "Powered by" 等無意義資訊
- 不使用 emoji / icon（除非設計師設計）

## 工具與資源

- 進行 Coding / Review 時，建議優先透過 context7 MCP 查詢相關文件與 Best Practice
- 必要的輔助工具無法使用時，可在詢問使用者後安裝
- 安裝工具時優先透過 mise，避免直接使用系統套件管理器安裝開發工具

## Formatter & Linter

<!-- 根據專案調整 -->

### 工具清單

| 語言/類型 | Formatter   | Linter            | 配置檔                                |
| --------- | ----------- | ----------------- | ------------------------------------- |
| Python    | ruff format | ruff check        | pyproject.toml                        |
| YAML      | yamlfmt     | -                 | .yamlfmt                              |
| Markdown  | prettier    | markdownlint-cli2 | .prettierrc / .markdownlint-cli2.yaml |
| Shell     | shfmt       | shellcheck        | -                                     |
| JSON      | prettier    | -                 | .prettierrc                           |
| TOML      | taplo       | -                 | -                                     |

### pre-commit

- 專案使用 pre-commit 管理 formatter 與 linter 的 Git Hook
- 配置檔: `.pre-commit-config.yaml`
- 安裝 hook: `pre-commit install`
- 手動執行全部檢查: `pre-commit run --all-files`
- 更新 hook 版本: `pre-commit autoupdate`
- pre-commit 配置檔必須 commit 至版本控制
- 新增或變更 formatter / linter 時，應同步更新 pre-commit 配置

### 格式化原則

- 所有程式碼提交前必須通過 formatter 與 linter 檢查
- formatter 與 linter 的配置檔應 commit 至版本控制
- 不同語言 / 檔案類型應使用對應的專用工具，避免一刀切
