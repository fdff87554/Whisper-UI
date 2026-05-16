# 程式碼品質完整規範

本文件為 CLAUDE.md 中「程式碼品質」區段的完整版，提供詳細的架構設計原則與函式設計指引。

## 架構設計

- 低耦合、高內聚（Low Coupling, High Cohesion）：模組只暴露必要的介面，內部實作細節不外洩
- 關注點分離（Separation of Concerns）：UI、業務邏輯、資料存取分屬不同模組
- 單一職責（Single Responsibility）：每個模組 / 類別只有一個變更的理由
- 優先使用組合（Composition）組合行為；繼承僅用於真正的 is-a 語義關係
- 依賴抽象而非具體實作（Dependency Inversion）：模組間透過 Interface / Protocol 溝通，建構時由外部注入依賴

## 函式設計

- 每個函式只負責一件事：如果需要用「和」來描述函式的功能，應拆分
- 函式長度以 50 行為參考上限；超過時優先檢視是否違反單一職責，但不為了湊行數而強行拆分
- 函式內的操作維持同一抽象層級：高層函式呼叫子步驟，不穿插底層實作細節
- 參數數量 0-2 個為佳；3 個以上時封裝為 Options Object 或 Config Struct
- 函式不修改傳入參數、不改變全域狀態；需要 Side Effect 時在函式名稱中明確表達意圖（如 `save`、`update`、`delete`）
- 查詢型函式不改變狀態（Command Query Separation）；例外情況（如 cache、lazy init）需在註解中說明
- CQS 於不同層級的應用：
  - Service-layer command 優先不回傳查詢結果（呼叫端需額外 query 取得最新狀態）
  - HTTP API 依 REST 慣例回傳 resource representation（POST 201 + 建立物件、狀態變更 action 回傳更新後資源）
  - 層級間溝通以外層（通常為 HTTP API）慣例為準
- 當 switch/if-else 分支超過 3 個且各分支行為結構相似時，考慮使用 Strategy Map 或 Polymorphism 替代；簡單的 2-3 分支不需重構
- 使用 Early Return 處理前置條件與錯誤路徑，將主要邏輯保持在最低巢狀層級
- Literal 數值與字串提取為具名常數，名稱需表達業務含義（如 `MAX_RETRY_COUNT = 3`）
- Error Handling 邏輯與業務邏輯分離：錯誤處理集中在函式入口或透過 middleware / decorator 統一處理
- 公開 API 的輸入參數進行型別與範圍驗證；內部函式依賴型別系統保障，不重複驗證
- 避免 boolean flag 參數改變函式行為，應拆分為獨立函式或使用 Enum / 具名參數

## 錯誤處理

- 使用語言慣例的錯誤處理機制（Python: Exception hierarchy, Go: error return, Rust: Result<T, E>）
- 定義專案專屬的 Error 類型層級，不直接拋出通用 Exception / Error
- Error message 應包含足夠 context 供 debug（什麼操作、什麼輸入、為什麼失敗），但不洩漏系統內部資訊給使用者
- 區分可恢復錯誤（retry、fallback）與不可恢復錯誤（fail fast），不對所有錯誤採取相同策略
- API 回應的錯誤格式應統一且結構化（包含 error code、message、detail），不返回自由格式的字串
- 不吞掉錯誤（catch 後不處理不 log），不使用空的 catch/except block
- 錯誤處理應在適當的層級進行，不在每一層都 catch-and-rethrow

## 日誌記錄

- 使用結構化 logging 框架（不使用 print / console.log 作為正式日誌）
- Log level 使用原則：
  - `DEBUG`: 開發時的詳細診斷資訊，production 預設不啟用
  - `INFO`: 正常但重要的業務事件（使用者登入、訂單建立、任務完成）
  - `WARNING`: 異常但可自動恢復的狀況（retry 成功、接近 rate limit）
  - `ERROR`: 需要人工關注的錯誤（請求失敗、資料不一致）
- Log 內容應包含足夠 context（who, what, when），便於事後追蹤
- 不在迴圈中產生大量重複 log，高頻操作應使用 sampling 或 aggregate

## 效能意識

- 注意 N+1 查詢問題：需要關聯資料時使用 JOIN 或 batch loading，不在迴圈中逐筆查詢
- 大量資料處理使用分頁（pagination）或串流（streaming），不一次載入全部至記憶體
- 注意演算法複雜度：避免在大資料量下使用巢狀迴圈（O(n^2)）或重複遍歷，善用 Set / Map
- 昂貴的運算結果考慮 caching，但只在有明確效能問題時加入，不預先優化
- 資料庫查詢應只選取需要的欄位，不使用 SELECT \*

## 開發順序

- 先寫出能正確運作的實作，確保功能與測試通過後，再逐步重構至符合品質標準
- 重構時保持外部行為不變，每次 commit 只改善一個面向（命名、結構、效能等）
- 碰到的程式碼如有小幅改善空間，順手改善（Boy Scout Rule），但不在同一 commit 中混合重構與功能變更
  - 順手改善限於：命名、type annotation、刪除明顯死代碼
  - 不順手做：結構重組、介面變更、邏輯修正（應另開 issue 或 PR）
- 不為未來可能的需求預先設計（YAGNI），只實作當前確定需要的功能
- 避免 premature optimization：先確保正確性與可讀性，再依據量測結果進行效能優化
  - 優化前先用 profiling 或基準測試量測，確認瓶頸再調整
  - 不為「可能的效能問題」預先加快取、複雜結構或非標準資料型態

## 依賴管理

- 新增第三方依賴前，評估是否必要：功能簡單可自行實作時，避免引入額外依賴
- 選擇依賴時考量：維護活躍度、社群規模、License 相容性、套件大小與間接依賴數量
- 依賴版本應明確 pin（lock file 必須 commit），避免使用過於寬鬆的版本範圍
- 不同時引入功能重疊的套件（例如不同時使用 lodash 與 ramda）

## 文件與註解

- Public API（函式、類別、模組）必須撰寫 docstring，說明用途、參數與回傳值
- Private / internal 實作不強制 docstring，但邏輯複雜處應加註解說明「為什麼」（why），而非「做什麼」（what）
- 註解應與程式碼同步更新，過時的註解比沒有註解更有害
- README 應保持更新，至少包含：專案說明、快速開始、開發環境設定
- 重大架構決策應記錄原因與替代方案的取捨（可在 PR description 或專用文件中記錄）

## Review Checklist

本節整合自原 `code-review.md`（v3.2.0 合併）。提供 self-review 與 PR 描述的具體檢查項，作為 CLAUDE.md「工作流程 > Code Review」的完整版。

### Self-Review Checklist

- 確認所有測試通過，新增功能有對應測試
- 確認 formatter 與 linter 無錯誤
- 重新閱讀自己的 diff，移除 debug 程式碼、`console.log`、`TODO` 等暫時性內容
- 確認沒有不小心 commit 的檔案（如 `.env`、IDE 設定、`node_modules`）
- 確認 commit history 乾淨，每個 commit 對應一個邏輯變更

### Review 重點

- 程式碼是否符合專案架構與 CLAUDE.md 的品質標準
- 邏輯正確性：特別注意邊界條件（空值、空集合、極端值）、併發情境、錯誤路徑
- 效能風險：N+1 查詢、不必要的迴圈、過大的記憶體配置、阻塞操作
- 資安風險：參照 CLAUDE.md「資安要求」章節逐項檢查
- 是否有重複邏輯可提取為共用元件
- 命名是否準確反映意圖，閱讀程式碼時不需要回頭查看定義才能理解
- 測試覆蓋是否足夠：正常路徑、邊界條件、錯誤路徑是否都有測試

### PR 描述規範

- 說明變更的目的（Why）與方法概述（How），不只描述改了什麼（What）
- Breaking Changes 以 `BREAKING:` 前綴標註，說明影響範圍與遷移方式
- 相關文件（README、API 文件、CHANGELOG）需同步更新
- 如有 UI 變更，附上截圖或錄影
