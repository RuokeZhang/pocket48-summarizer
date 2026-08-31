# Pocket48 Room Voice Recording Plan

**Status:** Reviewed and ready for execution  
**Repository:** `RuokeZhang/pocket48-summarizer`  
**Feature flag:** Off by default until the authenticated production probe passes

**Execution note (2026-08-31):** The repository-owned Phase 0 client, redacted
one-shot probe, bounded 60-second recorder, and manual one-SMS login helper are
implemented on `roxzhang/room-voice-poc`. Offline tests pass. A real login
attempt from the developer's current network was rejected before the API by
Pocket48's CDN/WAF with an HTML HTTP 403 response while the request was
unsigned, so no SMS was sent and no credential was created. The clean-room
dynamic `pa` implementation uses a locally provisioned seed extracted only
after verifying a pinned, reviewed source file's complete SHA-256; neither the
seed nor a third-party binary is committed or executed. Phase 1 remains blocked
until the signed login and authenticated audio probe pass.

**Account-session finding (2026-08-31):** A signed SMS login succeeded and
created a valid local token without storing the phone number or code. Logging
back into the official iPhone App immediately invalidated that token with
business status `401004`, confirming a single-active-session account model.
The user selected the dedicated-monitor strategy for version one: this account
will remain logged into the monitor rather than the official App. Any auth
failure stops monitoring and requires a manual SMS login; the service never
re-authenticates or sends SMS automatically.

**Authenticated probe result (2026-08-31):** After the user explicitly logged
the account back into the dedicated monitor, a fresh signed request resolved
member `407126` to channel `7587624` and server `6227955`. A subsequent
one-shot `team/voice/operate` request succeeded with `active=false`, zero
participants, and no stream URL, which is the expected inactive-room shape.
Authentication, dynamic `pa`, account token handling, member-room resolution,
and the status endpoint are therefore confirmed. The remaining Phase 0 gate is
one observed active session that returns an allowlisted RTMP/RTMPS URL and
produces a playable 60-second recording without invalidating the monitor token.

**Bounded account scan result (2026-08-31):** The batch room map returned 506
member-to-server mappings. The batch last-message contract supplied a current
primary channel for 447 of them; the primary-channel ordering matched
`server/jump` exactly for the five user-selected rooms. A one-time,
one-request-per-second scan then checked all 447 candidates without auth
failure, rate limiting, or consecutive anomalies. No room returned voice
participants or a stream URL during that snapshot. This confirms the bounded
scanner and account stability, but it does not satisfy the active-audio gate
and must not be converted into an unbounded poller before Phase 2.

## 1. Outcome

Add authenticated monitoring for Pocket48 room voice sessions ("房间上麦" /
"房间电台") to the existing application. When a configured member starts a
voice session, the service records the RTMP/RTMPS audio, preserves a private
copy, transcribes it, generates an AI summary, and presents the result in the
existing web UI.

This remains in the current repository because it should reuse the existing
authentication, SQLite database, member catalog, FFmpeg runner, private OSS,
DashScope transcription, LLM summarization, deployment workflow, and UI. The
long-lived monitor runs as a separate process so a stalled stream cannot block
the existing replay worker or web slots.

## 2. Confirmed Constraints

- Pocket48 exposes the current room voice state through the authenticated
  `im/api/v1/team/voice/operate` endpoint with `operateCode: 2`.
- A successful response can contain an RTMP/RTMPS `streamUrl` and a
  `voiceUserList`.
- Room voice is treated as live-only. The recorder must be running while the
  member is on mic; no reliable historical replay API is assumed.
- The user currently has one Pocket48 account. Version one must use one account,
  monitor one target, and allow only one concurrent recording.
- Pocket48 authentication requires a token, device/app headers, and a `pa`
  signature whose current generation rules must be verified before automation.
- No unreviewed third-party Pocket48 program, precompiled WASM module, plugin,
  hook, or script may be installed or executed. Protocol behavior may be
  studied, but implementation must be written and reviewed in this repository
  using approved dependencies.
- Room voice recordings are authenticated content and must be private by
  default. They must never appear on the public homepage or public job APIs.

## 3. Non-goals for Version One

- Monitoring every followed member or importing the account's complete follow
  list.
- More than one concurrent room recording.
- Publishing room voice recordings publicly.
- Replaying live audio through the web UI while recording.
- Capturing Pocket48 room text messages alongside the audio.
- Video clip export for audio-only sessions.
- Reusing third-party Electron applications or their packaged binaries.
- Automatically entering or resending SMS codes, storing account passwords,
  or bypassing account security controls. A one-time local helper may request
  one SMS and prompt the user to enter its code manually.

## 4. Architecture

```text
Pocket48 authenticated REST
        |
        | poll room/info + team/voice/operate
        v
pocket48-voice-monitor (separate systemd service)
        |
        | RTMP/RTMPS
        v
FFmpeg rolling audio segments ----> private permanent OSS recording
        |                                      |
        | finalized local audio                | signed playback URL
        v                                      v
existing worker queue                 authenticated <audio> UI
        |
        v
shared audio processing pipeline
  -> temporary OSS ASR input
  -> DashScope
  -> transcript normalization
  -> AI summary
  -> cleanup temporary artifacts
```

The voice monitor only detects and records sessions. It must not call
DashScope or the LLM itself. Once a recording is finalized, it creates a
private processing job and wakes the existing durable worker.

## 5. Data Model

### 5.1 Generalize processing jobs

Add a migration that extends `jobs` with:

- `source_type TEXT NOT NULL DEFAULT 'replay'`, constrained to `replay` or
  `room_voice`.
- `visibility TEXT NOT NULL DEFAULT 'public'`, constrained to `public` or
  `private`.

All existing rows remain public replay jobs. Every room voice job is created as
`source_type='room_voice'` and `visibility='private'`, with `job_access`
assigned to the configuring user. `require_readable_job`, homepage queries,
download routes, playback-track routes, and related APIs must enforce
visibility server-side. Hiding cards with JavaScript is not an access control.

The current schema also requires `jobs.live_id` to be non-null and unique and
`jobs.source_url` to be non-null. A room voice processing job therefore uses
repository-generated internal identifiers:

- `live_id = 'room-voice:' || room_voice_session.id`
- `source_url = 'https://pocketapi.48.cn/im/api/v1/team/voice/operate'`

Add a dedicated `create_room_voice_job()` repository method. It must not call
the replay-oriented `create_or_get_job()`, apply replay submission quotas, or
deduplicate by a Pocket48 replay ID. Its idempotency key is the unique
`room_voice_sessions.job_id` relationship.

The existing access rule has an important unsafe shortcut: completed jobs are
currently considered public. Change the rule everywhere to:

```text
visibility == public AND status == completed
OR the requesting user has job_access
OR the requesting user is an administrator
```

Apply that predicate to `require_readable_job`, `_visible_jobs_condition`,
homepage/member-filter queries, status, transcript, playback-track, SRT, raw
ASR, summary, media redirect, and every new room voice endpoint. A completed
private job must remain private.

The existing `transcript_segments`, summary fields, translation queue, and job
events remain attached to the job and are reused for both source types.

### 5.2 Monitoring targets

Create `room_voice_targets`:

- `id` UUID primary key.
- `owner_user_id` foreign key to `users`.
- `member_id` and display `member_name`.
- `channel_id` and optional cached `server_id`.
- `enabled` boolean.
- `created_at`, `updated_at`, and `last_checked_at`.
- `last_status`, `last_error_code`, and `last_error_at`.

Version one permits at most one enabled target. Enforce this in repository
logic under `BEGIN IMMEDIATE`; do not rely only on the UI.

### 5.3 Captured sessions

Create `room_voice_sessions`:

- `id` UUID primary key.
- `target_id` and `owner_user_id`.
- Nullable `job_id`, unique once a processing job is created.
- `status`: `detected`, `recording`, `finalizing`, `queued`,
  `processing`, `completed`, `failed`, or `deleting`.
- `detected_at`, `recording_started_at`, `recording_ended_at`, `duration_ms`.
- `stream_fingerprint`, a SHA-256 digest used for deduplication. Never persist
  the signed/raw `streamUrl`.
- `segment_directory`, `next_segment_index`, and `last_media_progress_at` for
  crash recovery.
- `final_audio_oss_object_key` for the permanent private recording.
- `recording_sha256`, codec/sample-rate metadata, and byte count.
- `stop_reason`, `error_code`, `error_message`, `retryable`.
- `created_at` and `updated_at`.

For version one, store a bounded, redacted `participants_json` snapshot on the
session rather than building participant event sourcing. Update it only from a
valid current response. Unknown or malformed participant entries are ignored
with a bounded warning and must not fail audio recording. Do not claim that a
participant name corresponds to a particular ASR speaker.

Use a transactionally enforced single-active-session guard instead of a
multi-monitor lease design. At startup, the sole monitor inspects any unfinished
session and either resumes at its next segment or finalizes it as partial. Keep
the stream fingerprint only to prevent the same active stream from being
created twice during restart recovery.

Define `room_voice_sessions.job_id` as
`REFERENCES jobs(id) ON DELETE CASCADE`. The session exists before its job, so
the field remains nullable until recording finalization. Targets use restrictive
deletion semantics while any session references them.

## 6. Authentication and Secret Handling

### 6.1 Required gate

Do not begin production automation until a read-only probe against the user's
own account confirms:

1. The current App version still accepts `team/room/info` and
   `team/voice/operate`.
2. The exact required headers and request bodies.
3. Whether `pa` changes per request, per timestamp, or per login session.
4. Token lifetime and whether a server request invalidates the official App
   session.
5. The returned stream protocol and participant shape.

### 6.2 Probe implementation

Add `scripts/room_voice_probe.py` as repository-owned code. It must:

- Be disabled unless
  `P48_RUN_ROOM_VOICE_PROBE=I_UNDERSTAND_THIS_USES_MY_PRIVATE_ACCOUNT`.
- Accept token, app info, `pa`, channel ID, and server ID only through
  environment variables or interactive terminal input.
- Never accept secrets as command-line arguments.
- Redact headers, stream URLs, query strings, tokens, account identifiers, and
  participant identifiers from output.
- Perform one bounded request by default; no background polling.
- Optionally write a redacted JSON schema fixture, never a live response.

The probe is run locally while the official App is logged in. Before and after
the request, confirm the phone remains logged in. A short FFmpeg recording is a
separate explicit probe and requires its own confirmation variable.

### 6.3 Production credentials

- Do not store the phone number, password, or SMS code.
- A repository-owned local login helper may perform one explicitly confirmed
  SMS login, with no automatic resend or retry, and write only the returned
  token plus generated device headers to a Git-ignored `0600` file.
- Keep the Pocket48 token and device/app values in root-readable deployment
  secrets, not SQLite or browser storage.
- Represent them with `SecretStr` settings and redact them from exceptions.
- Generate `pa` in repository-owned code only after its current algorithm has
  been independently understood and reviewed.
- Do not import the third-party `2.wasm` artifact. If clean-room `pa`
  generation is not feasible, stop at manual stream-URL POC and require a
  separate licensing and security decision before considering a reviewed
  sidecar.
- An authentication failure disables polling until an administrator explicitly
  re-enables it. It must not trigger repeated SMS logins or a tight retry loop.
- Add a global polling budget and counters. Version one stops for manual review
  after a configurable number of requests or consecutive anomalous responses
  per day. Treat account challenges, unexpected success envelopes, repeated
  403 responses, and App-session invalidation as account-health incidents, not
  ordinary retryable network failures.

## 7. Recording Design

Add:

- `src/pocket48_summarizer/clients/pocket48_voice.py` for validated,
  size-bounded Pocket48 REST calls and typed responses.
- `src/pocket48_summarizer/voice/monitor.py` for polling, deduplication, state
  transitions, jitter, request budgets, and exponential backoff.
- `src/pocket48_summarizer/voice/recorder.py` for FFmpeg lifecycle and segment
  finalization.
- `src/pocket48_summarizer/voice_cli.py` for the standalone process.

### 7.1 Polling

- Start with one target and a 10-second interval plus random jitter.
- Resolve and cache `serverId`, refreshing it only after an explicit stale
  response.
- A successful response with a non-empty supported stream URL starts recording.
- Three consecutive offline responses or 30 seconds without media progress ends
  a session. Make both values configurable within conservative bounds.
- HTTP 401/403 is a terminal authentication state until manual intervention.
- HTTP 429 and 5xx use capped exponential backoff and do not discard an active
  recorder.
- Enforce a conservative global daily request budget and expose remaining
  budget only to administrators.
- Validate all URLs against an RTMP/RTMPS host allowlist before passing them to
  FFmpeg.

### 7.2 Durable FFmpeg recording

Do not record one unbounded MP3 file. Use rolling five-minute segments so a
deploy, crash, or network interruption loses at most the current segment:

- Run FFmpeg with `-nostdin`, bounded network timeouts, no video, one audio
  stream, fixed sample rate/channel count, and the segment muxer.
- Write only below
  `DATA_DIR/room-voice/<session-id>/segments/`.
- Use deterministic zero-padded segment names created by the service.
- Do not interpolate remote values into a shell command; use an argument list.
- Track media progress from FFmpeg progress output rather than file mtime alone.
- On SIGINT, stop starting new recordings, ask FFmpeg to finalize its current
  segment, persist state, and exit within systemd's timeout.
- At startup, recover expired recording leases. If the same stream is still
  active, continue at the next segment index; otherwise finalize the partial
  recording.
- Enforce maximum session duration and maximum local bytes. Reaching either
  limit finalizes the available recording with an explicit stop reason.

After the session ends, concatenate/transcode segments once into the ASR format,
compute SHA-256 and media metadata, upload a permanent private copy, and then
create the processing job. Empty or extremely short recordings are retained as
failed diagnostics but are not sent to paid APIs.

## 8. Shared Processing Pipeline

Refactor `ReplayPipeline` so source acquisition is separate from common audio
processing:

1. Replay preparation continues to resolve metadata, parse danmaku, inspect HLS,
   and extract audio.
2. Room voice preparation verifies the finalized local recording and permanent
   private OSS object.
3. Both sources use one shared path for temporary ASR upload, vocabulary,
   DashScope submission/polling, transcript normalization, summarization,
   translation queueing, and temporary cleanup.

The refactor must preserve replay behavior and its resumability. A room voice
job must never call replay URL parsing, HLS inspection, or danmaku fetching.
`DurableWorker` continues to claim both job types, but the pipeline dispatcher
must branch on `job.source_type` before any replay-specific work. Existing
`claim_next_job()` and `recover_expired_jobs()` behavior must be tested for
private room voice jobs; source preparation is idempotent before the common
processing stages begin.

The permanent room recording is not stored in `jobs.oss_object_key`, because
that field is intentionally deleted after ASR. Keep the permanent object on
`room_voice_sessions.final_audio_oss_object_key`.

Update summary titles and prompts to say "房间上麦" rather than "直播" for this
source. With no danmaku, pass an empty peak list. Speaker diarization remains
optional; participant names must not be assigned to ASR speaker IDs unless the
audio provides evidence for that mapping.

## 9. API and UI

### 9.1 Admin monitoring page

Add an administrator-only `/admin/room-voice` page with:

- Authentication health without displaying credentials.
- One target editor: member, channel ID, optional server ID, polling interval,
  enabled toggle.
- Last check, last successful API response, current recording, duration, bytes,
  and last redacted error.
- Explicit "test once", "enable monitor", "disable monitor", and "stop current
  recording" actions protected by CSRF.

The web process writes desired state to SQLite. It does not launch FFmpeg
directly; the monitor process observes the state.

### 9.2 Existing home and detail UI

- Add source badges: `直播回放` and `房间上麦`.
- Public visitors never receive private room voice rows.
- The owner and administrators see active and completed room voice sessions on
  the existing homepage.
- While recording, the card shows an updating status and elapsed time but no
  live audio playback in version one.
- The existing job page branches on `source_type`. Room voice uses an
  authenticated HTML `<audio>` player backed by a short-lived signed OSS URL.
- Reuse synchronized transcript, bilingual translation, summary, timeline, and
  transcript-list UI.
- Hide replay-only controls: HLS player assumptions, danmaku panels, AI cover,
  and video clip editor.
- Display participant history as informational metadata, not as speaker
  attribution.
- Add an owner/admin delete action with an explicit confirmation. It is allowed
  only when both session and processing job are `completed` or `failed`, have no
  active worker ownership, and are not recording/finalizing/queued/processing.

Deletion uses a recoverable two-phase state machine because SQLite and OSS
cannot be one atomic transaction:

1. Under `BEGIN IMMEDIATE`, compare-and-set the session to `deleting`. Refuse if
   its status or job ownership changed.
2. Delete the permanent OSS object. Missing objects count as idempotent success.
   On a retryable OSS error, retain all rows and the object key in `deleting`
   state so cleanup can be retried.
3. After OSS success, delete the associated job in one database transaction;
   `ON DELETE CASCADE` removes the room session, transcript, access, summary
   chunks, translations, and other job-owned rows.
4. At worker startup and from the admin page, retry sessions stranded in
   `deleting`. Never present a successful deletion response until both object
   cleanup and database deletion complete.

All audio redirects and status endpoints call the same server-side
authorization check as the page. Signed URLs should use the shortest practical
TTL and must not be embedded on public pages.

## 10. Deployment and Operations

Add a pinned console entry point:

```toml
pocket48-voice-monitor = "pocket48_summarizer.voice_cli:main"
```

`voice_cli` must use a dedicated minimal service builder rather than
`build_services()`. The monitor process constructs only `Settings`, `Database`,
`JobRepository`, `Pocket48VoiceClient`, `FFmpegRunner`, the private `OSSStore`,
and `RoomVoiceMonitor`. It must not instantiate the replay worker, DashScope,
LLM, translation, clipper, AI-cover, HLS, or member-catalog services.

Add `deploy/systemd/pocket48-voice-monitor.service`:

- Same unprivileged `pocket48` user and hardened sandbox as the worker.
- `ENABLE_WORKER=false` and `ENABLE_CLIPPER=false`.
- Read/write access only to `/var/lib/pocket48-summarizer`.
- `Restart=on-failure`, conservative restart delay, SIGINT shutdown.
- Start only when `ROOM_VOICE_MONITOR_ENABLED=true`.

Update install, deploy, rollback, and backup scripts:

- Install the unit but leave it disabled by default.
- Run migrations before starting the monitor.
- Preserve active segment directories across releases.
- A release restart finalizes or resumes an active session; it must not delete
  partial segments.
- Keep migration 023 additive so it is safe while the old monitor version is
  still running. Deploy in this order: pause creation of new sessions, signal
  the monitor to finalize its current segment, migrate, switch worker/web
  releases, restart the monitor from persisted state, then re-enable polling.
  The existing replay-job drain is not sufficient because active recordings
  are tracked outside `jobs`.
- Back up the new SQLite rows, but not large local audio segments.
- Include monitor/auth heartbeat in an authenticated operations endpoint. The
  public `/healthz` may expose only `room_voice_monitor: enabled|disabled|stale`,
  never account or target details.

Add environment documentation with placeholders only. No real Pocket48 tokens,
headers, captured responses, stream URLs, or device identifiers may enter Git.

## 11. Implementation Sequence

### Phase 0: Authenticated protocol and media POC

Files:

- `scripts/room_voice_probe.py`
- `src/pocket48_summarizer/clients/pocket48_voice.py`
- `tests/test_pocket48_voice.py`
- `.env.example`
- `README.md`

Deliverables:

1. Typed parsing for redacted fixture responses.
2. One-shot authenticated status probe.
3. Optional explicit 60-second FFmpeg recording probe.
4. Written results covering token coexistence with the official App, `pa`
   lifetime, stream protocol, codec, silence behavior, and end-of-session
   response.

**Go/no-go gate:** Do not begin Phase 1 until the probe records playable audio
without logging the phone out and the `pa` generation strategy is understood.

### Phase 1: Schema, privacy boundary, and shared pipeline

Files:

- `src/pocket48_summarizer/migrations/023_room_voice.sql`
- `src/pocket48_summarizer/models.py`
- `src/pocket48_summarizer/repository.py`
- `src/pocket48_summarizer/pipeline.py`
- `src/pocket48_summarizer/services.py`
- `tests/test_db.py`
- `tests/test_repository.py`
- `tests/test_pipeline.py`
- `tests/test_routes.py`

Deliverables:

1. New source/visibility fields and room voice tables, including the synthetic
   room job identifiers and dedicated creation method.
2. Server-side private visibility enforcement that removes the current
   "completed means public" shortcut for private jobs.
3. Replay preparation separated from shared audio processing.
4. A fixture-created room voice job can complete transcription and summary
   without touching replay/HLS code.

### Phase 2: Durable monitor and recorder

Files:

- `src/pocket48_summarizer/voice/__init__.py`
- `src/pocket48_summarizer/voice/monitor.py`
- `src/pocket48_summarizer/voice/recorder.py`
- `src/pocket48_summarizer/voice_cli.py`
- `src/pocket48_summarizer/media/ffmpeg.py`
- `pyproject.toml`
- `tests/test_voice_monitor.py`
- `tests/test_voice_recorder.py`
- `tests/test_ffmpeg.py`

Deliverables:

1. One-target polling with jitter, request budgets, backoff, and auth lockout.
2. One-concurrent-session enforcement.
3. Rolling segments, graceful shutdown, restart recovery, and finalization.
4. Permanent private upload and processing-job enqueue.

### Phase 3: Admin controls and integrated private UI

Files:

- `src/pocket48_summarizer/routes.py`
- `src/pocket48_summarizer/templates/base.html`
- `src/pocket48_summarizer/templates/index.html`
- `src/pocket48_summarizer/templates/job.html`
- `src/pocket48_summarizer/templates/room_voice_admin.html`
- `src/pocket48_summarizer/static/app.js`
- `src/pocket48_summarizer/static/styles.css`
- `src/pocket48_summarizer/static/i18n.js`
- `tests/test_routes.py`

Deliverables:

1. Admin-only target and monitor controls.
2. Private active/completed cards in the existing homepage.
3. Audio player, transcript, summary, timeline, participant history, and delete
   action.
4. Replay-only controls absent for audio jobs.

### Phase 4: Production service and controlled rollout

Files:

- `deploy/systemd/pocket48-voice-monitor.service`
- `scripts/install-server.sh`
- `scripts/deploy-common.sh`
- `scripts/deploy-release.sh`
- `scripts/rollback-release.sh`
- `.github/workflows/deploy-production.yml`
- `deploy/README.md`

Deliverables:

1. Disabled-by-default hardened service.
2. Migration, deploy, rollback, and stale-heartbeat handling.
3. One-account production trial with one target.
4. Feature remains private and can be disabled without affecting replay jobs.

## 12. Validation

### Unit and integration tests

- Request construction, response bounds, schema drift, redaction, URL allowlist.
- Authentication terminal failure, account-health incidents, daily request
  budget, 429/5xx backoff, jitter, and recovery.
- Duplicate detection and one-active-session transaction safety.
- Bounded participant snapshot parsing.
- FFmpeg command construction without shell interpolation.
- Segment continuation after restart and partial finalization after stream loss.
- Size/duration limits and zero-byte/short-session rejection.
- Permanent/private OSS lifecycle versus temporary ASR-object cleanup.
- Delete compare-and-set behavior, refusal during recording/processing,
  idempotent OSS deletion, retry after an OSS failure, and job/session cascade
  cleanup.
- Replay regression coverage after pipeline extraction.
- Completed private rows unavailable through homepage, member filters, status,
  job, transcript, playback, SRT, raw ASR, summary, media, download, and audio
  routes for both anonymous and unrelated authenticated users.
- CSRF and admin authorization on every monitor mutation.

### Real probes

Each real probe requires an explicit confirmation variable and must not run in
CI:

1. One authenticated status request while no room voice is active.
2. One request while a target is active.
3. A 60-second local recording.
4. Forced network interruption and reconnect.
5. Monitor restart during recording.
6. End-to-end private OSS playback, ASR, summary, and cleanup.
7. Verify the official mobile App session remains usable.
8. Run an observed multi-hour monitoring window and verify the account receives
   no challenge, throttling, logout, or other restriction while remaining
   inside the configured request budget.

### Release gate

- Full existing test suite passes.
- New migrations pass from both a fresh database and a copy of production
  schema.
- No secret-like values appear in `git diff`, logs, fixtures, or workflow
  artifacts.
- Feature flag is off on first deployment.
- Enabling the monitor requires an authenticated administrator action plus
  valid server-side secrets.

## 13. Rollout and Rollback

1. Deploy schema and code with the monitor disabled.
2. Run the one-shot production probe manually.
3. Enable one target for a scheduled, observed session.
4. Confirm mobile login, recording continuity, private access, transcription,
   summary, permanent audio playback, and cleanup.
5. Leave concurrency at one until several sessions complete without auth or
   recovery failures.

Rollback disables and stops only `pocket48-voice-monitor`. Existing recordings
and jobs remain readable. Database additions are retained because destructive
down-migrations would risk losing recordings. In-progress segments are
finalized as partial recordings or retained for the configured failure
retention period.

## 14. Acceptance Criteria

- One configured room can be checked with the user's single account without
  repeatedly logging in.
- A controlled multi-hour monitoring trial stays within its request budget and
  does not invalidate or restrict the official App account session.
- An active room voice RTMP/RTMPS stream is recorded into crash-recoverable
  segments and finalized after the session ends.
- A permanent private audio copy is playable only by the owner or an
  administrator.
- The recording is transcribed and summarized through the existing processing
  services.
- The existing UI displays source type, status, audio, transcript, summary,
  timeline, and participant history.
- Public users and unrelated authenticated users cannot discover or retrieve
  room voice metadata, audio, transcript, or summary.
- Completed private room voice jobs remain private; only completed public replay
  jobs retain anonymous readability.
- Replay submission, replay processing, danmaku, video clipping, and existing
  deployment behavior remain unchanged.
- Authentication failures stop automated requests and surface a redacted,
  actionable administrator status.
- The entire feature can be disabled without disabling the web application or
  replay worker.

## 15. Review Record

The initial plan was reviewed against the repository by an independent Claude
Opus reviewer. Its blocking findings were incorporated:

- Define synthetic required `jobs.live_id`/`source_url` values and a dedicated
  room voice job creation path.
- Remove the current "completed means public" shortcut for private jobs across
  every list, page, API, and download route.
- Make worker dispatch, monitor-only service assembly, account request budgets,
  and deployment coordination explicit.
- Reduce version-one participant, polling, and multi-monitor complexity.

A second independent Claude Sonnet review confirmed those issues were resolved
and identified the deletion race. The final plan now uses a non-active-only,
recoverable two-phase deletion state machine with explicit foreign-key
semantics.
