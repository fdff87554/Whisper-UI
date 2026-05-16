# Planner / Executor 慣例

本檔為 `dev-guidelines` 的 Planner / Executor 雙模型分工慣例完整規範。CLAUDE.md 對應章節為摘要。

## 適用範圍

**適用（重要任務）**：

- ≥ 3 個檔案
- 跨模組重構
- 預估 ≥ 100 行變更
- 修改 `CLAUDE.md` / `.claude/rules/` 主章節

**不適用（微調，由 Claude 直接執行不切換 manager 模式）**：

- typo 修正
- 版本號 bump
- 單一檔案 < 20 行修補

## 三層機制

採用層級由低到高，視團隊實際痛點漸進啟用：

| 層  | 機制                      | 觸發                     | 強制力                                                                |
| --- | ------------------------- | ------------------------ | --------------------------------------------------------------------- |
| 1   | 普通 `claude` session     | session 預設             | settings.json permissions + 使用者模型設定                            |
| 2   | Manager-Executor 雙 agent | `claude --agent manager` | tools allowlist + `Agent(manager)` deny；專案編輯靠 prompt convention |
| 3   | Codex external executor   | manager 偵測後詢問使用者 | skill 包裝 + worktree 隔離                                            |

## 第 1 層：普通 `claude` session（預設）

普通啟動方式：

- `claude`
- 載入 `CLAUDE.md`、`.claude/rules/`、skills / agents metadata 與 project settings
- 不自動進入 manager；模型由使用者、CLI flag、環境變數、managed settings 或帳號預設決定
- 不在模板 project settings pin Opus 或 1M context

需要 Opus / 1M context 的團隊，可透過 user / local settings、`--model`、`ANTHROPIC_MODEL` 或 `ANTHROPIC_DEFAULT_OPUS_MODEL` 自行設定。來源：<https://code.claude.com/docs/en/model-config>。

**搭配 reviewer pass**：重要變更執行完成後，呼叫對應 reviewer agent 做最後一道審查：

- 文件變更（CLAUDE.md / `.claude/rules/`）→ 呼叫 `claude-md-reviewer`（model: opus）
- 敘述品質檢查 → 呼叫 `writing-quality-checker`（model: sonnet，機械檢查）

## 第 2 層：Manager-Executor（per-session 啟動）

當 main session 需強制分工（不允許直接編輯）時，啟用 manager 模式。普通 `claude` session 不會自動切換成 manager。

### 啟動方式

```bash
claude --agent manager
```

**不**設 `.claude/settings.json` `"agent": "manager"` per-project 預設。理由：

- 預設化會綁定下游模板使用者
- 微調任務也被強制走 manager 模式，傷害彈性
- per-session 啟動把「要不要分工」決策權留給人類

已進入普通 session 後，無法用 `/manager` 等 slash command 等價切換成 main-session agent。若需要完整 manager 行為，請重啟並使用 `claude --agent manager`。

Manager Mode 與 plan mode 不互斥。前者決定 main session 的 agent prompt / tools / model；後者決定當前 permission / workflow mode。

### Agent 行為

- **`manager`**（`.claude/agents/manager.md`，model: opus）
  - `tools` 含 `Edit` / `Write` 但 prompt 約束**僅供 plan mode 寫 plan file**；plan mode 外專案編輯透過 dispatch executor 完成
  - 明列 `WebSearch, WebFetch, Read, Grep, Glob, Bash, Skill(codex-delegate), AskUserQuestion, ExitPlanMode, Agent(executor-sonnet, claude-md-reviewer, writing-quality-checker, Explore, Plan, general-purpose)` → 涵蓋 survey、review 與 plan mode 收尾
  - `Agent(...)` 帶括號為 fail-closed 顯式 allowlist（官方語意，[來源](https://code.claude.com/docs/en/sub-agents#restrict-which-subagents-can-be-spawned)）；內建 `Explore` / `Plan` / `general-purpose` 必須明列才能使用，否則 manager 無法 dispatch codebase 探索
  - **規則**：任何 main-session agent（透過 `claude --agent <name>` 啟動者）若在 frontmatter `tools` 欄位使用 `Agent(...)` 帶括號 allowlist 形式，**必須**包含 `Explore`、`Plan`、`general-purpose` 三個內建 subagent。例外情境（如安全性考量限縮 `general-purpose`、或刻意禁用 plan-mode 自動 spawn 的 `Plan`）必須在該 agent 檔的「工具範圍」段落明文說明排除理由。PR review 時依此規則檢查 `.claude/agents/*.md` 變更；本規則取代原 `check-agent-allowlist` 機械 hook（已於 PR #30 移除，理由：pre-commit 預設不顯示 warn-only stderr，hook 實際保護幾乎為零；維護負擔 > 防護價值）
  - `settings.json` `permissions.deny` 加 `Agent(manager)` 阻擋 auto-delegation；plan mode 可正常進入並由 `ExitPlanMode` 結束

- **`executor-sonnet`**（`.claude/agents/executor-sonnet.md`，model: sonnet、effort: high）
  - 完整 Edit / Write / Bash 工具
  - 接收 manager 委派的任務描述，完成編輯後回報 diff

### Reviewer pass

executor 完成後 manager 用 `git diff` 與相關 lint / test 驗證；必要時要求 executor 修正。

## 第 3 層：Codex external executor（選擇性）

Codex 不是 Claude Code subagent，而是外部 CLI executor。若本機已裝 codex CLI 且設 `OPENAI_API_KEY`，manager 可在合適任務中詢問使用者是否改用 Codex。

### 觸發語意：偵測 + 詢問，不代替使用者決定

Manager / Claude 可判斷任務是否有機會使用 Codex，但**不能直接替使用者選 Codex**。dispatch 前流程：

1. 任務適合以 diff review 驗收時，偵測 codex 可用性：`command -v codex >/dev/null 2>&1`
2. 若 codex 不存在 → 直接 dispatch `executor-sonnet`
3. 若 codex 存在 → 使用 AskUserQuestion 詢問使用者選擇 Sonnet 或 Codex
4. 使用者選擇後 dispatch 對應 executor

`codex-delegate` 設 `disable-model-invocation: false`，讓 manager 可在使用者選擇 Codex 後呼叫 skill。這是信任模型，不是安全邊界。

### 執行流程

呼叫 `/codex-delegate` skill（`.claude/skills/codex-delegate/SKILL.md`）：

- 在獨立 git worktree 中執行 codex
- 完成後取回 `git diff` 給 manager / Claude review
- 接受則 `git apply` 套回主 worktree，拒絕則回報使用者

### 下游使用者注意

`.claude/skills/codex-delegate/` 為可選 skill。若本機無 codex CLI 或不打算使用，整個刪除該目錄不影響其他功能。

## 不適用情境（補充）

以下情境**不**啟用 manager 模式、**不**使用 Codex：

- typo / 版本號 bump / 單一檔案 < 20 行修補
- 純讀取類任務（survey、解釋程式碼）
- bootstrap 本慣例自身的工作（manager 尚未存在前）

## 設計取捨來源

- 官方 subagent 文件：<https://code.claude.com/docs/en/sub-agents>
- 官方 model-config 文件：<https://code.claude.com/docs/en/model-config>
- 官方 skill 文件：<https://code.claude.com/docs/en/skills>
- 官方 permission-modes 文件：<https://code.claude.com/docs/en/permission-modes>

完整可行性評估、設計取捨論證、替代方案否決理由見對應 PR description 與 plan 檔。
