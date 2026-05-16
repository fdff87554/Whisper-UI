# Prerequisites for Claude Code in Whisper-UI

`CLAUDE.md` and `.claude/rules/` inherit directives from the upstream `dev-guidelines` template that assume certain Claude Code primitives are available outside of this repo. This file lists those prerequisites with setup steps and the fallback behavior when they are absent, so a fresh checkout stays useful even without the optional pieces.

## context7 MCP (referenced by `CLAUDE.md` L193)

`CLAUDE.md` L193 instructs Claude to prefer [context7](https://github.com/upstash/context7) MCP for library / API documentation lookup. The repository does **not** ship a `.mcp.json` for this; contributors should configure context7 at user level if they want the directive fully actionable.

### Setup (choose one)

- **HTTP (recommended)** — add to `~/.claude/settings.json` or run `claude mcp add`:

  ```json
  {
    "mcpServers": {
      "context7": {
        "type": "http",
        "url": "https://mcp.context7.com/mcp"
      }
    }
  }
  ```

- **Local stdio** — same shape but with `"type": "stdio"`, `"command": "npx"`, `"args": ["-y", "@upstash/context7-mcp"]`. Requires local Node.

### Fallback when not configured

Without context7, Claude falls back to its built-in `WebFetch` / `WebSearch` tools for the same lookup intent. CLAUDE.md L193 (a generic section preserved verbatim from the template) degrades gracefully; nothing in this repo breaks.

## codex CLI (optional, referenced by `.claude/skills/codex-delegate/`)

Used only inside the `claude --agent manager` workflow when the user opts in to the Codex executor path. Not required for normal sessions or for the default Sonnet executor path.

### Setup

Install codex CLI (non-Anthropic distribution) and set `OPENAI_API_KEY` in the environment that runs Claude. See codex CLI's own documentation for installation.

### Fallback when not installed

`planner-executor.md` step "若 codex 不存在 → 直接 dispatch executor-sonnet" applies: manager probes `command -v codex` and routes everything to `executor-sonnet` when codex is absent. The `codex-delegate` skill is also fenced by per-command Claude approval (commit `bcf9209` removed pre-authorization of `codex *` / `git worktree *` / `git apply *`), so even with codex installed, side-effect commands require explicit user approval at run time.
