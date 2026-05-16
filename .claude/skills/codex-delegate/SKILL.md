---
name: codex-delegate
description: 委派實作給外部 codex CLI（gpt-5）。Codex 是 external CLI executor，不是 Claude Code subagent；約定上僅在 manager 詢問使用者並選擇 Codex 後呼叫。需本機已裝 codex 且設有 OPENAI_API_KEY。
disable-model-invocation: false
allowed-tools: Bash(command -v *), Bash(git diff *)
arguments: task_description
---

# codex-delegate skill

委派實作給外部 codex CLI（gpt-5）的標準包裝流程。完整慣例見 `.claude/rules/planner-executor.md`「第 3 層」。

> **外部 executor 邊界**
>
> Codex 不是 Claude Code subagent，不受 Claude Code subagent lifecycle 管理。
>
> `disable-model-invocation: false`：manager 可在使用者選擇 Codex 後呼叫此 skill。
>
> 約定：manager 必須先用 AskUserQuestion 取得使用者選擇。未確認前不要呼叫。
>
> `allowed-tools` 僅預授權**真正 read-only** 的環境檢查（`command -v *` 與 top-level `git diff *`）。具副作用的 `codex *`、`git worktree *`、`git apply *` 不預授權；transport flag 形式的 `git -C <path> <subcommand>` 也不預授權，因為 `*` glob 會涵蓋 `git -C . reset --hard` / `git -C ../foo checkout ...` 等 side-effect 子命令。所有 side-effect 命令（含 SKILL.md 流程中的 `git -C <worktree> diff`）每次執行時走標準 Claude approval 流程。這是 downstream defense-in-depth：除了 prompt-level「manager 先詢問使用者」的 convention 之外，再加一道 mechanical fallback gate。
>
> 啟用前請理解：codex CLI 進程本身不受 Claude permissions 約束；本 skill 以 worktree 隔離 + 每命令 approval + 人工 diff review 三層降低風險。

## 前置條件

**呼叫前提**：manager 已透過 AskUserQuestion 確認使用者選擇 Codex，或使用者明確手動執行 `/codex-delegate <task>`。

下游使用者若無 codex CLI 或不打算使用 gpt-5，可整個刪除 `.claude/skills/codex-delegate/` 目錄；其他功能不受影響。

## 環境檢查（dynamic context injection）

- codex 版本：!`codex --version 2>&1 || echo "codex CLI 未安裝"`
- 當前 repo root：!`git rev-parse --show-toplevel 2>/dev/null || echo "不在 git repo 中"`

## 執行步驟

### 1. 建隔離 worktree

```bash
git worktree add ../.codex-${CLAUDE_SESSION_ID} HEAD
```

理由：codex 進程內部不受 Claude permissions 約束；worktree 隔離把影響範圍限制在 `../.codex-<session>/` 內，主 worktree 不會被直接污染。

### 2. 在隔離 worktree 中呼叫 codex

具體 flag 以 `codex --help` 為準。先列 help 確認當前版本的 CLI 介面：

```bash
codex --help
cd ../.codex-${CLAUDE_SESSION_ID}
codex <appropriate-flags> "$task_description"
```

### 3. 取回 diff 供 manager / Claude review

```bash
git -C ../.codex-${CLAUDE_SESSION_ID} diff
```

### 4. Review 決策

由 manager / Claude 主對話審查 diff：

- **接受** → `git apply <(git -C ../.codex-${CLAUDE_SESSION_ID} diff)` 套回主 worktree
- **拒絕** → 說明原因，回報使用者由其決定下一步（重試、改用 executor-sonnet、放棄）

### 5. 清理

```bash
git worktree remove --force ../.codex-${CLAUDE_SESSION_ID}
```

## 任務內容

$task_description

## 失敗模式與處理

| 失敗              | 原因                               | 處理                                                    |
| ----------------- | ---------------------------------- | ------------------------------------------------------- |
| codex CLI 未安裝  | 步驟 1 報錯                        | 回報使用者安裝 codex CLI，或改用 `executor-sonnet`      |
| codex 拒絕執行    | API key 無效、額度耗盡、模型不可用 | 看 codex 錯誤訊息；回報使用者                           |
| diff 過大或破壞性 | codex 誤解任務或修改範圍超出預期   | manager 拒絕 apply；改回 executor-sonnet 或請使用者決定 |
| worktree 已存在   | 同 session 重試或前次清理失敗      | 先 `git worktree remove --force` 舊的，再重試步驟 1     |

## 不要做的事

- 不要在主 worktree 直接呼叫 codex（會繞過隔離機制）
- 不要在 codex 完成後直接 commit；先讓 manager / 使用者 review diff
- 不要把 codex 的判斷當作最終結論——manager 仍負責 review 與 reject 權限

## 來源與限制

- 官方 skill 文件：<https://code.claude.com/docs/en/skills>
- codex CLI 非 Anthropic 官方整合；本 skill 為自製包裝，CLI 版本變動可能 break skill
- gpt-5 / Sonnet 4.6 在 coding 任務上的品質對價無公開 benchmark；本 skill 不替使用者判斷哪個更適合
