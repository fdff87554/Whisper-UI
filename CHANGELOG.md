# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `WORKER_MAX_IDLE_TIME` lets GPU workers (cuda/rocm, which run the non-forking
  `SimpleWorker`) self-exit after a spell with no job so the
  `restart: unless-stopped` policy respawns a fresh process, returning the
  resident GPU context and host RSS to the OS. Defaulted to `300` seconds for
  `worker-gpu` / `worker-rocm`; `0` disables, and a non-integer/negative/overlong
  value is ignored with a warning. See README "GPU worker resource lifecycle".

### Changed

- GPU/ROCm workers now release idle GPU memory and RSS by recycling the worker
  process after `WORKER_MAX_IDLE_TIME` seconds of inactivity, instead of holding
  the CUDA/HIP context for the lifetime of the container. Set
  `WORKER_MAX_IDLE_TIME=0` to keep the previous always-resident behaviour.
- Operator-set values are kept out of shell parsing so a malformed value cannot
  inject commands or extra CLI tokens: `WORKER_MAX_IDLE_TIME` is validated in the
  entrypoint; the `ollama-pull` sidecar runs `ollama pull` in exec form and the
  redis healthcheck references `REDIS_PASSWORD` as a runtime env var (rather than
  interpolating either into a `sh -c` string); and the redis server command is
  built in list form so `REDIS_MAXMEMORY` / `REDIS_PASSWORD` are literal argv
  elements that cannot re-tokenize into extra redis options.

## [2.14.0] - 2026-06-12

Issue-sweep remediation (PR #121).

### Fixed

- whisper.cpp upgraded from v1.8.0 to v1.8.6, fixing a VAD buffer overflow
  (upstream ggml-org/whisper.cpp#3558) that aborted transcription with heap
  corruption on files whose VAD segment ends at the audio boundary (#119).
- whisper-cli failure messages keep the tail of stderr instead of the
  model-loading banner, so the actual crash cause reaches the UI (#120).
- DOCX export strips XML-incompatible control characters instead of failing
  the whole export when a segment carries one.

### Changed

- `uv.lock` records the current project version again (stale at 2.11.0
  since the v2.12.0 release).

## [2.13.1] - 2026-06-11

Full-codebase review remediation (PR #115). Highlights below; the PR
description carries the complete list.

### Fixed

- Live and upcoming stream URLs are rejected at submission instead of
  downloading until the job timeout kills the worker (live streams report no
  duration, which slipped past the duration cap as 0).
- Google Drive downloads are now capped like direct file uploads, and
  `MAX_UPLOAD_SIZE` actually reaches the workers under compose (it was
  frontend-only, so the cap silently ran at the 2 GB default).
- A failed pipeline now bumps the generation counter, so a still-running
  sibling stage (which SimpleWorker cannot stop mid-run) can no longer
  resurrect deleted progress/context keys as orphans.
- whisper.cpp results that omit the detected language no longer disable the
  Chinese-only postprocess conversion and LLM-correction gates on
  explicitly-Chinese jobs.
- Downloaded media paths resolve from yt-dlp's reported filepath; a retry
  after a killed attempt can no longer pick up a stale `.part` fragment.
- Job retry / re-transcribe routes no longer block the event loop while
  enqueuing; bulk actions cap the accepted id count.
- Subtitle exports escape structural characters (`&`, `<`, `-->`) so LLM
  correction output cannot break SRT/VTT parsing.
- The viewer's copy shortcut ignores Ctrl/Cmd+C; bulk export downloads get
  their intended filename back (Content-Disposition ASCII fallback).
- Size, duration, and login-lockout messages report precise values instead
  of floor-rounded ones; failed ffmpeg conversions clean up partial WAVs.
- `DEVICE=cuda` on an AMD machine now falls back to rocm instead of CPU.
- Schema migrations stop re-logging index entries as freshly applied on
  every worker task; the stray v2.10-era `idx_jobs_source_job_id` index is
  dropped; `WHISPERCPP_BINARY` is documented and passed to the rocm worker.

### Changed

- Docker images now install dependencies pinned to `uv.lock` (the shipped
  versions match what CI tested) and cache the dependency layer so source
  edits no longer trigger full rebuilds; the CPU worker pins torch 2.10.
- `compose.yml` worker configuration is deduplicated via YAML anchors
  (562 to 400 lines, behaviour verified identical for every profile).
- README documents the URL ingestion feature line and the whisper.cpp
  hallucination-guard settings; outdated troubleshooting entries refreshed.
- Dead labels, constants, icon macros and test-only helpers removed from
  the production API surface; duplicated and vacuous tests dropped.

## [2.13.0] - 2026-06-11

### Fixed

- whisper.cpp transcriptions (ROCm profile) of recordings with long silence
  or music collapsed into hallucination loops covering the entire transcript
  (observed in production: 1,673 of 1,679 segments were one repeated
  subscription-plea line, triggered by a 20-minute silent stream intro). The
  `whisper-cli` invocation now enables Silero VAD pre-segmentation
  (`WHISPERCPP_VAD`, default on; model fetched from the ggml-org/whisper-vad
  HF repo) and disables cross-window text conditioning
  (`WHISPERCPP_MAX_CONTEXT=0`), matching the protection the whisperx (CUDA)
  path already gets from its built-in VAD batching.
- File-path checks across model resolution, result loading, media download
  and source-audio copy now use `is_file()` instead of `exists()`, so a
  same-named directory can no longer masquerade as a file and fail later
  downstream; `get_source_media_path` also filters directories out of its
  glob matches.

### Added

- "自動偵測 (auto)" language option on the upload, URL and re-transcribe
  forms. Both backends detect the spoken language (whisperx via
  `language=None`, whisper.cpp via `-l auto`); the detected code flows into
  the transcript, the Traditional-Chinese conversion gate and the (zh-only)
  LLM correction stage. The default selection is unchanged (zh).
- Transcript quality gate: when one normalized segment text dominates a
  completed transcript (≥ 50% of 20+ segments — the signature of a
  hallucination loop), the job completes with a persisted quality warning
  (`jobs.quality_warning`), shows a 品質警告 badge on the job card and a
  banner in the viewer, and the LLM correction stage is skipped instead of
  spending hours "correcting" degenerate output.

## [2.12.1] - 2026-06-10

### Fixed

- Speaker diarization on AMD ROCm hosts failed with "Cannot re-initialize CUDA
  in forked subprocess". The worker entrypoint only switched to RQ's
  non-forking SimpleWorker for `DEVICE=cuda`, so the ROCm worker (`DEVICE=rocm`)
  ran the default forking worker; because ROCm's PyTorch uses the `torch.cuda`
  (HIP) API, GPU stages in a forked work-horse could not initialize the GPU
  context. The guard now covers `cuda` and `rocm`. (Pre-existing since the
  ROCm profile was added; not specific to a release.)

## [2.12.0] - 2026-06-10

### Fixed

- The jobs list poll now actually morphs the DOM instead of replacing it:
  whitespace inside the `morph:{...}` swap config broke htmx's swap-spec
  tokenization, so the idiomorph extension (introduced in v1.10.0) never ran
  on `#job-list-wrapper` and every 3s poll silently fell back to an innerHTML
  swap — nesting a fresh wrapper inside the old one and destroying open
  dropdowns, focus, scroll position, and text selection on each tick. This
  was the root cause of the jobs page feeling like it constantly reloads.
- Background polls no longer animate the global top loading bar, and a failed
  background tick no longer pops a network-error toast every 3 seconds during
  a backend hiccup. Polling wrappers opt out via `data-quiet-poll`;
  user-initiated requests (pagination, filters, actions) keep both behaviors.
- The export dropdown on job cards is driven by DaisyUI's focus mechanism
  alone. Mixing Alpine `x-show` with the `:focus-within` CSS left two sources
  of truth that could desync, producing a menu that needs two clicks to
  reopen.
- The sticky-header status chip counts refresh during polling via
  out-of-band swaps instead of going stale until a full page reload; the
  chip markup is now shared between `/jobs` and `/admin/jobs`.
- Several `x-data` / `@click` attributes that interpolated a string with
  `|tojson` rendered literal double quotes that terminated the attribute
  early, breaking the status filter chips, the dashboard greeting heading,
  the dashboard active-job ETA, and the admin reset-password dialog. They now
  use single-quoted JS literals (for quote-free values) or a data attribute
  read via `$el.dataset` (for the username).
- The `/jobs/list` polling fragment now resets an unknown `status` filter to
  empty, matching `/jobs` and `/admin/jobs/list`, so a bogus value no longer
  stays baked into the poll URL.
- URL (YouTube/Twitter/Drive) transcription jobs now get a death-penalty
  scaled to the real audio duration. They are enqueued before the media is
  downloaded, so every stage previously fell back to the default timeout and a
  long video could be killed mid-transcription; once preprocess probes the
  duration the GPU stages are resized to match what a file upload already gets.
- A failed word-level alignment no longer discards speaker labels. The
  unaligned transcription is now carried forward so speaker diarization is
  still applied at the segment level; only word-level timestamps are lost.
- Submitting a large batch of files or URLs no longer blocks the web server
  while each job is enqueued; the synchronous enqueue is offloaded so the
  jobs poll and other requests stay responsive during submission.

### Changed

- Removed the unused `list_jobs_by_source` query and its index (no view ever
  listed a job's re-transcribe siblings); the `source_job_id` column and its
  version badge are unchanged. Consolidated the duplicated jobs status-filter
  validation into a single shared helper.

## [2.11.0] - 2026-06-10

### Added

- YouTube playlist URLs are now accepted on the URL upload form and expanded
  at submit time into one transcription job per video, grouped as a batch.
  Expansion is a metadata-only yt-dlp flat extraction in the web layer (no
  media download); the per-video download pipeline is unchanged. Auto-generated
  Mixes (`RD*`/`UL*`) and login-bound lists (Watch Later, Liked videos) are
  rejected with a dedicated message, as are playlists that are private,
  deleted, empty, or larger than the batch limit — nothing is persisted on a
  failed expansion. Private/deleted entries inside an otherwise-valid playlist
  are skipped and reported in the toast. yt-dlp is now part of the `frontend`
  extra.
- Playlist batches show the playlist title in the job list batch header
  (new nullable `batch_title` column on `jobs`, migrated automatically) and
  the title is searchable in the client-side job filter.
- `youtu.be/<id>?list=` share links now resolve to their single video instead
  of being rejected as playlists, matching the existing `watch?v=<id>&list=`
  behavior.

### Changed

- `MAX_BATCH_SIZE` raised from 50 to 100 (shared by file uploads, URL
  submissions, and playlist expansion).

### Fixed

- The liveness-aware stale reaper now re-arms the pipeline state TTL each time
  it spares a queued job, so a batch backlog deeper than 7 days can no longer
  expire the generation keys and reap a still-healthy pipeline on a later
  round.

## [2.10.1] - 2026-06-09

### Fixed

- X post download no longer fails on a transient `Bad guest token` (X throttling
  its anonymous guest-token endpoint) or HTTP 429/5xx. The yt-dlp extraction now
  retries up to 3 times with a fresh client (2s/4s backoff) so a re-fetched guest
  token clears the blip; login walls, age/NSFW gating and over-length media still
  fail fast. When retries are exhausted the user gets an actionable "source
  temporarily unavailable, retry later" hint instead of the raw yt-dlp error.

## [2.10.0] - 2026-06-09

### Added

- Twitter/X post video download. Paste an `x.com` / `twitter.com` status link
  (incl. `m.` / `mobile.` subdomains) to download and transcribe the video
  attached to a post, alongside the existing YouTube and Google Drive sources.
  The URL is host-whitelisted and canonicalised to `https://x.com/i/status/{id}`
  before download, and yt-dlp's extractor is pinned to `twitter` — the same SSRF
  defence as the YouTube path.
- `TWITTER_MAX_DURATION` (default `14400`, mirrors `YOUTUBE_MAX_DURATION`) to cap
  the attached video length on the X path.
- Optional `TWITTER_COOKIES_FILE` (default unset → anonymous) to fetch
  login-walled / age-restricted posts. Enabling it needs both the env var AND
  the `cookies.txt` mounted read-only into the download worker — see
  `.env.example`. X Broadcasts / Spaces are intentionally out of scope and
  surface a clear error.

## [2.9.0] - 2026-06-09

### Added

- Dedicated `whisper:llm` queue for the optional LLM correction stage, plus a
  `worker-llm` compose service (`llm-worker` profile) and `WORKER_LLM_QUEUES`.
  This lets a dedicated `worker-llm` run the slow LLM without blocking the fast
  io/cpu finalisation — previously `llm_correction` shared `whisper:io`, so the
  io worker (which also drains `whisper:cpu`) was held up by a slow model.
  Default / single-container deployments still run everything on one worker
  (every worker drains `whisper:llm` by default); the isolation is opt-in via
  the `llm-worker` profile.
- `OLLAMA_THINK` (default `false`) to disable a thinking-capable Ollama model's
  chain-of-thought. For JSON transcript correction, thinking is markedly slower
  and (on gemma-class models) degrades the output; off is faster and cleaner.

### Migration

- **Scaled topologies with LLM correction:** because `llm_correction` moved off
  `whisper:io` to its own `whisper:llm` queue, a deployment that explicitly
  narrows `WORKER_*_QUEUES` and has `OLLAMA_BASE_URL` set must ensure some worker
  listens on `whisper:llm` — add it to a worker's queue list (e.g.
  `WORKER_IO_QUEUES="whisper:io whisper:cpu whisper:llm default"`) or run a
  dedicated `worker-llm` (`--profile llm-worker`). Otherwise an LLM-enabled job's
  final stage strands with no consumer. Default and single-container deployments
  are unaffected (every worker drains `whisper:llm` by default).

## [2.8.0] - 2026-06-08

### Added

- Minimal observability: opt-in structured JSON logs via `LOG_JSON` (worker
  stage logs carry `stage` / `job_id` / `elapsed_ms`), an unauthenticated
  Prometheus `/metrics` endpoint (queue depth, job status, RQ workers), and a
  `monitoring` compose profile (Prometheus + Redis exporter + AMD GPU
  exporter). Grafana dashboards and per-stage histograms are deliberate
  follow-ups. (#96)
- Defensive Redis memory cap via `REDIS_MAXMEMORY` (default `0` = unlimited
  preserves current behaviour). The eviction policy is hardcoded to
  `noeviction` and is not configurable — RQ generation counters, sub-job sets,
  and pipeline context must never be evicted or the DAG corrupts.
- AMD/ROCm single-GPU scaled-topology docs and `.env.example` example (narrow
  `worker-rocm` to GPU stages, run a separate `worker-io`).
- Deterministic queue-split throughput integration test. (#97)

### Fixed

- Optional LLM correction is now strictly best-effort: a failed or timed-out
  `run_llm_correction` completes the job with the un-corrected transcript
  instead of failing it, and a persist failure marks the job FAILED rather than
  leaving it stuck in PROCESSING. (#94)
- The stale-job reaper is now liveness-based (`is_pipeline_dead` via RQ sub-job
  state) instead of wall-age, so a queued-but-waiting job behind a slow single
  worker is no longer mass-reaped; `PIPELINE_STATE_TTL` raised from 24h to 7d.
  (#95)
- Documented runtime knobs now reach the containers. compose has no `env_file`,
  so `LOG_LEVEL` / `LOG_JSON` (frontend + all workers) and `ALLOW_REGISTRATION`
  / `MAX_UPLOAD_SIZE` (frontend) are passed explicitly; previously a `.env`
  value for these was silently ignored — notably an `ALLOW_REGISTRATION=false`
  registration lockdown. (#96)

## [2.7.0] - 2026-06-03

### Changed

- Default LLM correction model (`OLLAMA_MODEL`) is now `gemma4:e4b` instead of
  `gemma4:e2b`, trading a larger VRAM/disk footprint (~9.6 GB pull) for better
  correction accuracy. Set `OLLAMA_MODEL=gemma4:e2b` to keep the previous
  default.
- Upgraded the `redis` client to 8.0 (from 6.4) and `rq` to 2.9.0. redis-py 8.0
  defaults to the RESP3 protocol; the bundled `redis:7-alpine` server supports
  it and the worker/web Redis code paths were validated against a real server.
- Upgraded `ctranslate2` (the faster-whisper backend) to 4.7.2.

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
