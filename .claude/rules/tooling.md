---
paths:
  - "mise.toml"
  - ".mise.toml"
  - ".tool-versions"
  - ".pre-commit-config.yaml"
  - "**/pyproject.toml"
  - "**/package.json"
  - "**/package-lock.json"
  - "**/go.mod"
  - "**/Cargo.toml"
  - "**/.python-version"
  - "**/.node-version"
---

# 工具與環境詳細指引

本文件為 CLAUDE.md 中「環境管理」與「Formatter & Linter」區段的完整版。

**載入機制**：此檔使用 `paths:` frontmatter，**只在 Claude 操作環境/工具配置檔時才載入**，節省非環境任務（純業務邏輯、純文件）的 context 成本。團隊成員仍可直接 `cat` 本檔閱讀（paths 不影響人類直接開檔）。

## mise 環境管理原則

- 使用 mise 統一管理語言 runtime 與開發工具，避免依賴系統全域安裝
- mise.toml 必須 commit 至版本控制，作為環境的 single source of truth
- 專案操作前應確認 mise 環境已啟用且工具版本正確
- Python 專案的虛擬環境應位於專案根目錄下的 .venv，並已加入 .gitignore
- 安裝依賴前應確認已啟用正確的虛擬環境
- 不直接使用系統全域的語言 runtime 或工具，所有操作應透過 mise 管理的版本執行
- 安裝工具時優先透過 mise，避免直接使用系統套件管理器安裝開發工具

## pre-commit 完整操作指引

- 專案使用 pre-commit 管理 formatter 與 linter 的 Git Hook
- 配置檔: `.pre-commit-config.yaml`
- 安裝 hook: `pre-commit install`
- 手動執行全部檢查: `pre-commit run --all-files`
- 更新 hook 版本: `pre-commit autoupdate`
- pre-commit 配置檔必須 commit 至版本控制
- 新增或變更 formatter / linter 時，應同步更新 pre-commit 配置
- hook 版本應使用各工具的最新穩定版本

## 格式化原則

- 所有程式碼提交前必須通過 formatter 與 linter 檢查
- formatter 與 linter 的配置檔應 commit 至版本控制
- 不同語言 / 檔案類型應使用對應的專用工具，避免一刀切
- 程式碼風格問題交由工具處理，Review 聚焦於邏輯與架構
