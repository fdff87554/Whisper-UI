# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.6.0] - 2026-06-01

### Fixed

- Batch task groups no longer auto-collapse (or refuse to collapse) while the
  job list polls. Expand/collapse state moved out of the DOM into an Alpine
  store keyed by batch, so a 3s poll, a status-filter switch, or page
  navigation no longer fights the user's toggle, and a finishing batch no
  longer snaps shut a group the user opened.
- Progress bar no longer reserves an unfillable ~25% "diarize" band for jobs
  that run with speaker diarization disabled.
- A duplicate `finalize_success` callback for an already-completed job is now a
  no-op (symmetric with the existing already-failed guard).
- Re-enqueuing a job fully resets its Redis progress hash, so a previous
  attempt's error/result fields can no longer linger after a retry.

### Added

- AMD GPU (ROCm) support via a new `rocm` compose profile and
  `docker/Dockerfile.worker.rocm`. Transcription runs on the whisper.cpp HIP
  backend — CTranslate2 (whisperx / faster-whisper) has no ROCm backend —
  selected by a new `TRANSCRIBE_BACKEND=whispercpp` setting, while alignment
  and speaker diarization run on PyTorch-ROCm. A `rocm` device label maps to
  torch's `cuda` namespace, and the worker disables the MIOpen backend on
  gfx1151 (native HIP kernels) to avoid a `miopenStatusUnknownError` in
  pyannote. Tested on a Radeon 8060S (Strix Halo, gfx1151), ROCm 7.2.
- `ALLOW_REGISTRATION` setting (default `true`): when `false`, self-service
  `/register` is closed after the bootstrap admin exists so only an admin can
  create accounts.

### Security

- Uploads are sniffed for non-media file signatures (e.g. a PDF or HTML page
  renamed to `.mp3`); such files are skipped (not written to disk), while the
  valid files in the same batch are still queued and the skipped count is
  surfaced. ffmpeg remains the downstream gate.
- `num_speakers` is clamped to `[0, 20]` server-side on every submit path
  instead of trusting the form's `max` attribute.
- The unknown-export-format error no longer echoes the caller-supplied format
  name back in the response.
- The upload and jobs views receive only the derived availability flags they
  render, not the entire `Settings` object.

### Changed

- Internal cleanup with no behaviour change: the default Whisper model is now a
  single `DEFAULT_WHISPER_MODEL` constant, the throttled progress reporter is a
  small class instead of an 80-line closure, the shared job-list context
  builder is a public helper, and the single-process `PipelineOrchestrator`
  moved to test helpers (it had no production callers).

## [2.5.0] - 2026-05-25

### Added

- Visual-contrast regression tests (`tests/visual`, `pytest -m visual`, new
  `visual-tests` CI job). A headless Chromium renders the compiled stylesheet
  and reads computed colors to assert the card border stays perceptibly
  distinct from the card surface in both themes — guarding against the dark
  border regressing to invisible again. Requires `playwright install chromium`.

### Changed

- Dark-theme card borders are visible again. They previously used
  `border-base-300`, whose lightness sits ~3% from the `base-200` card surface
  in dark mode, so the outline all but disappeared. Borders now use a dedicated
  `--color-line` token (decoupled from `base-300`, which still backs surface
  fills like `bg-base-300` hovers/badges), tuned for a clearly perceptible
  outline in both themes (measured ≈1.7:1 light, ≈2.2:1 dark). This border, the
  page loader, and the speaker colors are defined inside the daisyUI theme
  blocks, so they track the theme exactly like the base palette — including
  `prefers-color-scheme: dark` before a `data-theme` is set (no light flash on a
  dark surface pre-hydration / with JS off) and inside a nested
  `data-theme="whisper-light"` subtree.

- Admin pages aligned with the v2 design language. `/admin/jobs` now reuses the
  user-facing job list (`_job_list.html`/`_job_card.html`) with a sticky filter,
  search, per-job owner badges, and **multi-select bulk delete / retry / export**
  — these reuse the existing `/jobs/bulk/*` endpoints, which already operate
  across every owner for admins (no new authorization surface). The shared
  selection store and confirm/batch-download dialogs were extracted into
  `_job_interactions.html` so the user and admin job pages share one copy.
  `/admin/users` gains role/status filter chips and moves the reset-password
  form into a shared modal. (Re-transcribe stays hidden on the admin view.)
  Bulk retry/delete confirmation now routes through the same v2 confirm modal
  as the per-row and batch actions instead of a native `window.confirm`.
- Upload result toasts ("已提交 N 個任務" …) now use a server-side session
  flash instead of redirect query params persisted to `localStorage`. The
  upload handlers stash the message in the session and redirect to a clean
  `/jobs` (no `?submitted=&failed=…`); `base.html` consumes and renders it
  once per genuine full-page load (htmx partial fetches and hx-boosted swaps
  are skipped, so a poll never pops a pending flash). The flash is now truly
  one-shot — a page reload no longer re-shows a stale toast, dropping the
  previous 60-second `localStorage` recovery hack.

### Fixed

- The jobs bulk-select store now initializes on hx-boosted navigation, not
  only on first full page load. Because `hx-boost` swaps only `#page-content`,
  arriving at `/jobs` (or `/admin/jobs`) from another page re-ran the store
  script after `alpine:init` had already fired, leaving `$store.jobSelection`
  undefined and the row checkboxes / bulk bar inert. Registration is now
  idempotent. The job-list search likewise re-applies after each htmx
  poll/filter swap instead of being cleared by the list re-render.
- Named volumes no longer keep `root:root` ownership inherited from an
  earlier root-era deployment. The Dockerfile build-time `chown` only seeds
  an empty volume on first mount, so a populated `app-data` or `model-cache`
  could stay root-owned and block the uid-1000 services: `app-data` failed
  DB/upload writes, and `model-cache` flooded every job's log with
  HuggingFace "Could not cache non-existence ... Permission denied" warnings
  for its `.no_exist` negative cache. A one-shot `volume-init` sidecar now
  re-owns both volumes to uid 1000 before the frontend and workers start. It
  is guarded — the recursive `chown` runs only when a volume is not already
  uid-1000 owned, so steady-state restarts cost one `stat` per volume rather
  than a full walk.

## [2.4.0] - 2026-05-24

### Added

- Re-transcribe a completed job's audio with different parameters
  (model, language, speaker diarization, traditional-Chinese conversion,
  LLM correction) **without re-uploading**. Each run creates a new
  transcript version and preserves the original so versions can be
  compared. The job card gains a "重新轉換" modal pre-filled from the
  source job, and re-transcribe versions are tagged with a "重新轉換版本"
  badge; a `source_job_id` column links a version chain back to its root
  job. The source audio is copied into the new job's own directory, so
  each version is independent for viewing, export, and deletion.

### Changed

- The diarization and LLM-correction opt-in flags are now clamped to what
  the deployment can actually run (`HF_TOKEN` for diarization,
  `OLLAMA_BASE_URL` for LLM correction) when a job is created from upload,
  URL, or re-transcribe. This keeps the persisted flag honest and avoids
  enqueueing a diarize sub-job that the stage would only skip. The upload
  "job inserted" log now records the clamped flags rather than the raw
  request flags.

### Fixed

- Async request handlers no longer block the event loop on slow filesystem
  or subprocess work: the ffprobe duration probe, batch ZIP creation,
  transcript result loading, and job-directory deletion are now offloaded
  to worker threads.

## [2.3.1] - 2026-05-24

### Fixed

- Viewer transcript text no longer disappears. v2.3.0 rendered each
  segment's text client-side via
  `x-html="window.whisperHighlight({{ seg.text|tojson }}, search)"`;
  `tojson` emits a double-quoted JSON string, which closed the
  double-quoted attribute early and left the expression malformed, so
  the text never rendered while timestamps and speaker labels (rendered
  server-side) stayed visible. Text now renders server-side as element
  content — visible even if Alpine/JS fails — and `x-html` only
  re-renders it (with `<mark>` search highlighting) when Alpine is alive.

### Changed

- Viewer skips the per-segment `data-raw` copy and `x-html` highlight
  markup for search-disabled large transcripts (> `VIEWER_SEARCH_SEGMENT_LIMIT`
  segments), where search — and therefore highlighting — is already off,
  avoiding redundant client-side work and a duplicate copy of each segment's
  text.

## [2.3.0] - 2026-05-24

### Added

- v2 UI redesign across Login, Dashboard, Upload, Jobs, and Viewer
  driven by the Claude Design handoff bundle and the evaluation
  report in the PR description. Visual language (OKLCH palette,
  Noto Sans TC, 8 px radii, status colors) is preserved; only
  structure and interaction change.
  - Dashboard: time-aware greeting, hero in-flight tracker with
    stage pill row and ETA, quick-action grid (檔案 / 資料夾 /
    YouTube), 7-day completed sparkline, first-run onboarding.
  - Upload: scene presets (會議 / Podcast / 訪談 / 演講課程) that
    one-tap configure the form, basic / advanced split with InfoTip
    tooltips and an "已啟用 N 項" advanced summary, sticky submit
    footer.
  - Jobs: sticky filter header with per-status chip counts, search
    box now covers source URL, bulk select via checkbox + floating
    action bar with retry / delete / export, processing badge
    pulses for non-color cue.
  - Viewer: per-segment 複製此段 buttons are keyboard-accessible
    (WCAG 2.1.1, 1.4.13); speaker labels gain a glyph (●▲■◆★✦◉♦)
    as a non-color cue (WCAG 1.4.1); `/`, ↑/↓, c, Esc keyboard
    shortcuts with discoverable hint panel; search matches
    highlighted via `<mark>`.
  - Login: show/hide password toggle (icon_eye / icon_eye_off
    Lucide macros added). Register bootstrap mode now renders a
    warning banner so first-run admin creation is unmissable.
  - Sidebar: bundled waveform logo mark (assets/logo-mark.svg)
    sits next to the existing "Whisper UI" wordmark. The mark is
    invented for the bundle, not an official brand asset; the
    text wordmark remains canonical.
- `POST /jobs/bulk/{action}` endpoint (action ∈ retry / delete /
  export). Per-job ownership enforced; partial failures surfaced
  via HX-Trigger-After-Settle `bulkPartial` / `bulkComplete`.
- `JobDatabase.count_completed_by_day(days, owner_id)` for the
  dashboard sparkline.
- Upload result toast now persists across one page reload via
  localStorage (`whisper-ui-upload-flash`, 60-second TTL).

### Changed

- `templates/_dashboard_active.html` now renders stage indicator
  derived from the existing progress message. Hash field set
  written by `RedisProgressReporter` is unchanged.
- `JOBS_SEARCH_PLACEHOLDER` updated to mention 網址 since the
  search now matches source URLs.
- Dependencies bumped: transformers 4.57.6 → 5.9.0 (major; the
  whisperx 3.8 Wav2Vec2 align path was runtime-validated against
  transformers 5.9.0 — import surface plus model load / forward /
  CTC decode), argon2-cffi 23.1.0 → 25.1.0 (PasswordHasher defaults
  unchanged: time_cost=3, memory_cost=65536, parallelism=4),
  fastapi[standard] → 0.136.3, numpy → 2.4.6, fakeredis[lua] →
  2.35.1.

### Fixed

- SRT / VTT export now collapses embedded newlines in cue text, so a
  line break inside a segment (which the optional LLM correction
  stage can emit) can no longer split or truncate a subtitle cue.
- YouTube download stage pins `allowed_extractors=["youtube"]` as
  defense in depth on top of the existing URL whitelist, so yt-dlp
  can never fall back to the generic extractor.

### Security

- `starlette` bumped to 1.1.0 to remediate PYSEC-2026-161.

### Deploy notes

- **Docker deployments do not require any extra step.** The CSS
  rebuild is handled by `docker/Dockerfile.frontend` Stage 1
  (`node:24-alpine` runs `npx @tailwindcss/cli --minify` and the
  output is copied into the Python runtime image); the
  development compose file ships a `css-watcher` sidecar that
  rebuilds on file change. Both paths pick up the new
  `.status-pulse` keyframe and v2 utility classes automatically.
- **Bare-metal deployments** (running `uvicorn whisper_ui.web.app`
  without Docker) need to run `mise run css` once after pulling
  to refresh `src/whisper_ui/web/static/style.css`. The artifact
  is gitignored; `mise run css:watch` keeps it live during local
  development. See CONTRIBUTING.md for the full local non-Docker
  workflow.
- No backend contract changes: form field names, status enum
  values, URL paths, htmx polling structure, and theme strings
  are all preserved. See evaluation report §6 for the full
  "do-not-touch" list.
- **Worker images now build on transformers 5.9.0.** Rebuild the
  worker image (`docker compose --profile gpu|cpu build`) when
  upgrading. The transformers 5 align path was validated at the API
  level, not via a full GPU end-to-end run, so a one-off
  Chinese-audio align smoke test after deploy is recommended.

## [2.2.0] - 2026-05-22

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

### Fixed

- Admin endpoints `activate`, `deactivate`, and `toggle-admin` now
  surface a `user_not_found` error for missing user IDs instead of
  returning 500. Previously only `toggle-admin` / `reset-password`
  (via a pre-check) showed the friendly message; the sibling endpoints
  leaked the underlying `ValueError` from `users_repo.set_active` /
  `set_admin`. The `toggle-admin` path also defensively catches the
  TOCTOU race between the pre-check and the actual update.
- Batch ZIP entries for URL-source jobs no longer carry the YouTube
  URL as the filename (which produced entries like `watch?v=abc.srt`
  that some Windows extractors reject). URL jobs now use the job id
  as the entry name; uploaded media keeps its original basename.

### Documentation

- `SECURITY.md` rewritten to match v2.1.0: documents the session-cookie
  authentication, CSRF (Origin-vs-Host), per-user + per-IP rate limit,
  owner isolation, session-version revocation, defense-in-depth
  headers, upload hardening, and YouTube URL whitelist that are all
  shipped. Replaces the previous text which claimed these controls were
  intentionally omitted. Deployment best practices section updated
  with `SESSION_SECRET`, `SESSION_HTTPS_ONLY`, `TRUST_PROXY_HEADERS`,
  and `REDIS_PASSWORD` guidance.
- `CONTRIBUTING.md` migrated from `pip install -e ".[dev]"` to
  `uv sync --extra dev`; duplicated architecture tree replaced with a
  pointer to README's `## Project Structure` (which now also carries
  the layer dependency direction block).
- README config table drops the misleading `LOG_JSON` row (env var
  was never wired up).
- Internal docstrings added to `JobDatabase` (thread-safety contract),
  `PostprocessStage._converter` (per-instance lifecycle), the three
  pipeline TTL constants (`PIPELINE_STATE_TTL_SECONDS`,
  `Settings.redis_processing_expiry`, `_DEFAULT_PROCESSING_TTL`), and
  the three generation-gating enforcement sites
  (`pipeline_callbacks.is_stale_callback`,
  `progress._LUA_TERMINAL_GENERATION_GATE`,
  `context_store._GENERATION_GATED_HSET_LUA`) so the shared-state
  contracts are discoverable from the code.

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
