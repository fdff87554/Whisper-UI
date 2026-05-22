# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Centralised stdlib `logging` setup in `whisper_ui.core.logging_setup`.
  Reads `LOG_LEVEL` (DEBUG / INFO / WARNING / ERROR / CRITICAL; default
  INFO) and applies one dictConfig across the frontend and worker.
  Pins `rq` / `rq.worker` / `rq.scheduler` / `uvicorn.access` to
  WARNING so the every-13-minute RQ heartbeat and the soon-to-be-
  replaced uvicorn access log do not crowd out signal.
- `RequestIdMiddleware` (registered as the outermost middleware on the
  frontend) reads `X-Request-ID` from the inbound request (8-64 hex
  chars; otherwise generates a fresh 8-char id), publishes it via
  contextvars, and echoes it back on the response. Every log line
  during the request renders `[req=<id> user=<name>]` so an operator
  can grep one id and read the whole request trace; `AuthMiddleware`
  overlays the resolved username into the `user=` tag.
- Structured access log on the `whisper_ui.web.access` logger
  carrying method, path, status, duration_ms, and ip on every
  request (including exceptions, which render `status=500` sentinel).
  Replaces uvicorn's stock access log via `--no-access-log` on the
  container CMD.
- Audit logging across the previously silent paths: rate-limit
  decisions (debug per check, warning on threshold), session lifecycle
  events in AuthMiddleware (session_version mismatch / deactivated user /
  unknown uid), upload pipeline events (per-file insert + batch
  summary), job delete / retry success paths, stage transitions
  (start / finish with elapsed_ms / timeout), pipeline failures
  (exception class alongside the localised user-facing message),
  filestore deletes (info on success, warning on OSError) and
  schema migrations (info per applied ALTER, debug for already-applied
  skips).
- `recover_stale_jobs` now logs a WARNING containing the recovered job
  ids (capped at 20 + count overflow) so an operator can correlate the
  recovery event with the affected DB rows from a single line.

### Changed

- Worker container now starts via `python -m whisper_ui.worker` (a
  thin wrapper that calls `setup_logging()` then delegates to
  `rq.cli.main`) instead of `python -m rq.cli`. This applies the
  project-wide dictConfig inside the RQ worker process rather than
  losing it across the shell `exec`.
- `pipeline.audio_probe.get_audio_duration_seconds` gains a `job_id`
  kwarg used purely for log correlation. The absolute path is no
  longer logged because user-supplied filenames can themselves be PII
  and the upload layout reveals internal storage paths. Three failure
  modes that previously shared one generic message are now distinct:
  `subprocess.TimeoutExpired`, `FileNotFoundError` (binary missing),
  and other `OSError` (logs the exception class).
- All `compose.yml` services pin `logging.driver=json-file` with
  `max-size: 20m` and `max-file: 5` (100 MB per container) so the
  log volume from the new structured access log cannot fill
  `/var/lib/docker` on long-running deployments.

## [2.1.0] - 2026-05-21

### Added

- Multi-user session-cookie authentication. There is no built-in default
  account; the first visitor to a fresh instance is bounced to a one-shot
  `/register?bootstrap=1` flow that creates the system's initial admin.
  Subsequent visits go through `/login` or self-service `/register`.
  Passwords are hashed with argon2id (`argon2-cffi`); sessions are signed
  with `itsdangerous` via Starlette's `SessionMiddleware`. Bumping a
  user's `session_version` (admin reset, deactivation) invalidates every
  existing session for that account on the next request.
- Per-user job isolation. `jobs.owner_id` (added by an additive
  migration) is set on every new upload, and every job-touching route
  filters by `owner_id = current_user.id`. Cross-user access returns 404
  rather than 403 so job existence is not leaked.
- Admin views: `/admin/users` (create / activate / deactivate / promote
  / demote / reset password) and `/admin/jobs` (every owner's jobs,
  including pre-auth `owner_id IS NULL` legacy rows). The repo layer
  enforces "system must have at least one active admin" — deactivating
  or demoting the last active admin is rejected.
- Redis-backed login rate limit with two independent counters per
  attempt: `MAX_LOGIN_ATTEMPTS` per username (default 5) and the higher
  `MAX_LOGIN_ATTEMPTS_PER_IP` (default 20) per source IP, both in a
  `LOGIN_LOCKOUT_SECONDS` window (default 900s).
- CSRF protection on every mutating verb, comparing `Origin` (or
  `Referer`) against `request.url.netloc`. With `TRUST_PROXY_HEADERS=true`,
  the per-IP counter reads `X-Forwarded-For` (left-most entry) and the
  CSRF check also accepts `X-Forwarded-Host`, both gated so a hostile
  client cannot spoof these headers when no proxy is in front.
- Six new env vars wired through `compose.yml` and documented in README
  and `.env.example`: `SESSION_SECRET`, `SESSION_HTTPS_ONLY`,
  `MAX_LOGIN_ATTEMPTS`, `MAX_LOGIN_ATTEMPTS_PER_IP`,
  `LOGIN_LOCKOUT_SECONDS`, `TRUST_PROXY_HEADERS`.

### Changed

- The login flow now verifies the password before checking
  `is_active`, so a deactivated account only reveals its state to
  someone who already knows the correct password — anyone probing
  wrong passwords sees the same generic "invalid" message regardless
  of whether the account exists or is active.

### Migration notes

- Existing deployments: the migration is additive (`ALTER TABLE jobs
ADD COLUMN owner_id INTEGER` + `CREATE TABLE users IF NOT EXISTS`),
  so it runs cleanly against a populated database. Pre-2.1.0 jobs keep
  `owner_id IS NULL` and are visible only on the admin `/admin/jobs`
  view; per-user routes filter on `owner_id = ?` and never match NULL.
- Set `SESSION_SECRET=$(openssl rand -hex 32)` in `.env` before
  starting — leaving it empty falls back to a per-process random
  secret (with a startup WARNING) that invalidates every session on
  each restart.
- On first visit after upgrade, any browser is redirected to
  `/register?bootstrap=1`; the first account created becomes the
  initial admin. After that, registration follows the standard
  self-service flow.

## [2.0.0] - 2026-05-17

### BREAKING

- Removed the legacy `whisper_ui.worker.tasks.process_transcription`
  entry point. The DAG dispatcher in
  `whisper_ui.worker.pipeline_dispatcher.enqueue_pipeline` has been
  the only path the web routes use for several releases; the legacy
  entry survived only to keep already-queued sub-jobs running across
  upgrades. Drain the RQ queue (or `FLUSHDB` it) before upgrading.
  See README "Upgrading from v1.x to v2.0" for the procedure.
- Removed the four hand-tuned `STAGE_WEIGHTS_*` dicts and the
  orchestrator's re-exports. Callers needing pipeline-shape-aware
  bands now invoke
  `whisper_ui.pipeline.progress_bands.build_stage_weights(
has_download=..., has_llm=...)`. Exact percentages drift a couple
  of points from the legacy hand-tuned values.

### Added

- `SecurityHeadersMiddleware` sets `X-Content-Type-Options`,
  `X-Frame-Options`, and `Referrer-Policy` on every response for
  defense in depth, even though the deployment model is
  internal-network only.
- Global FastAPI exception handler returns a generic `Internal Server
Error` JSON body while logging the full traceback, so an
  unanticipated 500 no longer echoes file paths or partial SQL to the
  client.
- Opt-in upload retention loop: setting `UPLOAD_RETENTION_DAYS > 0`
  has the web app hourly reclaim the upload directory of COMPLETED
  jobs older than the threshold while keeping the DB row and
  `result.json` for viewer access. FAILED jobs are preserved so the
  retry button keeps working. The variable is wired through
  `compose.yml`'s frontend service and documented in the README
  "Optional upload retention" section.
- `uv.lock` is now committed and consumed by both CI jobs (`uv sync
--frozen`). Dependabot watches `uv`, GitHub Actions, and the
  Dockerfiles for upstream updates.
- CI gained a dedicated integration-tests job (installs ffmpeg) and a
  `pip-audit` step against the locked requirements. The audit scans
  the dev extra (which transitively pulls in `frontend` + `worker-llm`,
  covering fastapi / python-docx / httpx); the heavy worker ML extras
  are monitored by Dependabot instead to avoid persistent alerts from
  upstream-unpatched CVEs.

### Changed

- `worker.runtime.build_worker_runtime` accepts a `generation` kwarg
  and stamps it on the bundled reporter, replacing the two
  `_build_generation_aware_reporter` helpers that previously
  re-instantiated the reporter in stage tasks and finalize callbacks.
- Callback helpers (`mark_failed`, `cancel_remaining_subjobs`,
  `format_failure_message`, `extract_meta_generation`,
  `is_stale_callback`) moved from `worker.pipeline_dispatcher` into a
  new `worker.pipeline_callbacks` module. The `rq.Callback` entry
  points themselves stay at their existing dotted paths.
- `worker.progress` shares the terminal-write generation gate between
  `complete()` and `fail()` via an interpolated `_LUA_TERMINAL_GENERATION_GATE`
  snippet, closing the duplication that PR #39 Round 4 shipped one
  drift bug from.
- `is_llm_active(job, settings)` and `cleanup_preprocessed_audio` are
  now single helpers in `worker.runtime` instead of being duplicated
  across the dispatcher and the (removed) legacy task path.
- Generation / context TTLs collapse into one
  `core.constants.PIPELINE_STATE_TTL_SECONDS` constant; both worker
  modules import from it.
- Settings now uses `extra="ignore"` so worker-only env vars
  (`WORKER_GPU_QUEUES`, `WORKER_IO_QUEUES`) coming from the same
  `.env` no longer block the web tier from starting.
- The upload-retention sweep now offloads its SQLite query and
  per-job `shutil.rmtree` calls through `asyncio.to_thread` and caps
  each pass at 200 jobs, so a long-deferred sweep cannot stall the
  FastAPI event loop or starve incoming requests. The offloaded
  thread opens its own short-lived `JobDatabase` instead of sharing
  `app.state.db` with the event-loop thread, since CPython's
  `sqlite3` binding does not serialise concurrent calls on the same
  connection even with `check_same_thread=False`. The batch cap now
  counts only successful deletions (and the SQL orders results oldest-
  first), so a backlog larger than the limit drains across consecutive
  sweeps instead of stalling on the first batch — retention does not
  touch the DB row, so without this every sweep would re-visit the
  same already-reclaimed ids.
- Docstrings and the worker entrypoint comment that still implied the
  legacy `process_transcription` path would keep running have been
  rewritten; historical-fact phrasing about the pre-v2 orchestrator is
  preserved where it explains why a piece of code looks the way it
  does.
- README now points at `uv sync --extra dev` / `uv run` for local
  development and warns that production deployments must set
  `REDIS_PASSWORD`. The quick-start URL was corrected to match the
  compose port mapping (`localhost:8080`).
- Dependency upper bounds tightened: `faster-whisper<2`,
  `transformers<5`, `redis<7`. `taplo` versions aligned between
  `mise.toml` and `.pre-commit-config.yaml` at `0.9.3` (upstream
  pre-commit wrapper has not yet published a `0.10.x` tag).

### Removed

- `src/whisper_ui/worker/tasks.py` (legacy monolithic worker).
- `tests/integration/test_worker.py` — its assertions (timeout
  classification, missing-job handling) are now covered by unit tests
  in `test_pipeline_dispatcher.py` and `test_stage_tasks.py`.

[2.0.0]: https://github.com/fdff87554/Whisper-UI/releases/tag/v2.0.0
