---
paths:
  - "**/test_*.py"
  - "**/*_test.py"
  - "tests/**"
  - "**/__tests__/**"
  - "**/*.test.{ts,tsx,js,jsx,mjs,cjs}"
  - "**/*.spec.{ts,tsx,js,jsx,mjs,cjs}"
  - "**/*_test.go"
  - "**/*_test.rs"
---

# 測試策略完整規範

本文件為 CLAUDE.md 中「測試策略」區段的完整版。

**載入機制**：此檔案使用 `paths:` frontmatter，**只在 Claude 讀取測試檔案時才載入**，節省非測試任務的 context 成本。團隊成員仍可直接 `cat` 本檔案閱讀（paths 不影響人類直接開檔）。

## 基本原則

- 所有新增功能與 bug fix 必須附帶對應的測試
- 測試與實作同步撰寫（test-alongside），不事後補測試
- 測試應覆蓋正常路徑、邊界條件與錯誤路徑
- 測試應獨立且可重複執行，不依賴外部狀態或執行順序

## 測試類型與比例

- **Unit Tests**: 核心邏輯、純函式、工具函式必須有 unit test
- **Integration Tests**: 模組間互動、資料庫存取、外部 API 呼叫需有 integration test
- **E2E Tests**: 關鍵使用者流程應有 end-to-end 測試覆蓋（視專案規模決定）

## 測試品質

- 測試命名應描述行為與預期結果，例如 `test_create_user_with_duplicate_email_returns_conflict`
- 每個 test case 只驗證一個行為（允許多個 assert 但聚焦同一行為）
- 使用 Arrange-Act-Assert（AAA）模式組織測試結構
- 避免測試中包含條件邏輯（if/else），每個分支應為獨立的 test case
- 測試資料應在 test case 中明確定義，不依賴共享的 mutable 狀態

## 模擬原則

- 優先使用真實依賴，只在以下情況使用 mock：
  - 外部服務呼叫（第三方 API、Email 服務等）
  - 非確定性行為（時間、隨機數、UUID 生成）
  - 效能瓶頸（大量資料的資料庫操作）
- Mock 應模擬行為而非實作細節，避免測試與實作過度耦合
- 不 mock 不屬於自己的程式碼邊界（不 mock 語言標準庫，前述非確定性工具除外）
- 時間、隨機、UUID 等非確定性來源優先以 dependency injection 方式注入（如注入 clock、id_generator）；僅在 DI 成本過高的場景退回於邊界 patch
- Mock 的回傳值應反映真實的資料結構，不使用簡化的假資料

## 測試維護

- 修改功能時，同步更新對應的測試
- 刪除功能時，同步刪除對應的測試，不留下無用的 test case
- 不允許 skip / disable 測試作為長期解決方案；如需暫時跳過，必須附帶 issue 追蹤
