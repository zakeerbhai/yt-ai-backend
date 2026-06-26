# YouTube AI Automation Manager — Backend (Slices 1 + 2)

**Slice 1** is the core pipeline: upload a video → it's stored, transcribed,
and AI metadata is generated automatically.

**Slice 2** (this update) adds real auth: Firebase-verified users, the
YouTube OAuth connect flow, encrypted token storage with automatic
refresh, and ownership checks on every route.

## What's included

### Core pipeline (Slice 1)
- FastAPI app with async PostgreSQL (SQLAlchemy 2.0 + Alembic)
- `POST /api/videos/upload` — accepts a video file, kicks off the pipeline
- Background pipeline (Celery + Redis): Cloudinary upload →
  transcription (AssemblyAI, which fetches the remote URL directly) →
  AI metadata generation (Gemini, strict JSON schema validated with
  Pydantic) → auto-generated thumbnail frame → `ready_for_review` (or
  auto-publish if the channel is in `full_auto` mode). Each stage is
  resumable on retry — see "Hardening pass" below.

### Auth (Slice 2)
- `app/core/firebase.py` — Firebase Admin SDK init (lazy, from a
  service account JSON file — never the client web API key)
- `app/core/auth.py` — `get_current_user` dependency: verifies the
  `Authorization: Bearer <firebase_id_token>` header, auto-provisions a
  local `User` row on first sight of a given Firebase identity
- `app/core/ownership.py` — `get_owned_channel` / `get_owned_video`:
  every route depends on these so users can only ever touch their own
  data (404, not 403, on mismatch — doesn't leak existence)
- `GET /api/auth/youtube/connect` → redirects to Google's OAuth consent
  screen (signed, time-limited state token for CSRF protection)
- `GET /api/auth/youtube/callback` → exchanges code for tokens, fetches
  channel identity via Data API, encrypts + stores tokens
- `DELETE /api/auth/youtube/{channel_id}` → disconnects a channel
- `app/services/youtube_auth_helper.py` — builds an authenticated
  `YouTubeService` for a channel, transparently refreshing + persisting
  a new access token if the stored one has expired
- `GET /api/me`, `GET /api/channels`, `PATCH /api/channels/{id}` (set
  `auto_publish_mode` per channel)
- **Every video/channel route now requires auth + ownership** — verified
  with live `TestClient` requests (not just code review) that
  unauthenticated requests get a 401 on every protected route

## Live integration testing pass (this update)

Everything up to this point had been verified with fake credentials and
no real database — imports, lint, a `TestClient` with no real Postgres
behind it. This pass went further: I installed a real PostgreSQL and
Redis instance, generated and applied the first Alembic migration
against it, ran real HTTP requests through the full FastAPI app backed
by that real database, and ran an actual Celery worker process against
the real Redis queue. This is the closest verification possible without
your real Gemini/AssemblyAI/Cloudinary/YouTube credentials — and it
caught two bugs that none of the earlier (import-only, mocked) checks
could have found:

- **Missing task registration (would have broken every publish):** the
  Celery app's `include` list only loaded `video_pipeline`, never
  `publish_pipeline`. Since `publish_video_task` is only imported lazily
  from inside `video_pipeline.py`, a worker started exactly the way the
  setup instructions say (`celery -A app.workers.celery_app worker`)
  would never register the publish task at all. Queuing it would
  silently log `Received unregistered task` and discard the message —
  meaning every video a person approved would sit at `status=scheduled`
  forever, with no error and no video ever reaching YouTube. Caught by
  actually starting a worker and watching the registered `[tasks]` list
  in its startup log; fixed by adding `publish_pipeline` to `include`.
- **Crash inside the error handler itself:** both Celery tasks called
  `asyncio.run()` twice in sequence — once for the main attempt, once
  for the except-block's failure-recording write. Since SQLAlchemy's
  async engine is a module-level singleton whose connection pool is
  tied to whichever event loop created it, the second `asyncio.run()`
  call tried to reuse a connection bound to the first (already-closed)
  loop and crashed with `RuntimeError: ... attached to a different
  loop`. In practice this meant: if a publish ever failed, the code path
  whose entire job is "write down what went wrong" would itself crash,
  so the video would be stuck with no status update and no error
  message — the worst version of a silent failure. Caught by running a
  worker, deliberately triggering a failure (an invalid encrypted
  token), and reading the resulting traceback rather than assuming a
  clean Celery log meant a clean run. Fixed by collapsing each task into
  a single `asyncio.run()` call so there's exactly one event loop and
  one connection pool for the whole task execution, success or failure.

Also generated and committed the first Alembic migration
(`alembic/versions/..._init_schema.py`) after confirming it applies
cleanly to a real Postgres instance and produces the exact schema the
models define (verified column-by-column and the `videostatus` enum
values directly via `psql`) — this was previously left as a step for
you to run blind.

What's still unverified: anything that requires your real API keys —
actual Gemini output quality, actual AssemblyAI transcription behavior,
actual YouTube upload success, actual OAuth consent flow. Those need a
real run with real credentials, which only you can do.

## NOT included yet (next slices)

- Mobile app (Flutter) — only the web frontend exists
- Analytics dashboard endpoints (YouTube Analytics API wrapper exists
  in `youtube_service.py`, just needs routes)
- Content calendar / "best time to publish" scheduler service (Gemini
  service has `suggest_best_publish_time`, needs a route + cron trigger)
- Captions (SRT/VTT) endpoint (AssemblyAI service supports generating
  these, just needs a route)
- Firebase push notifications (FCM token registration endpoint exists
  as a model, sending logic not yet built)

## Setup

```bash
cd yt-ai-backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Now edit .env and fill in REAL values for every variable.
```

### Firebase setup
1. Firebase Console → Project Settings → Service Accounts → Generate
   new private key.
2. Save the JSON to `./secrets/firebase-service-account.json` (this
   path is gitignored — never commit it).
3. Set `FIREBASE_PROJECT_ID` in `.env` to match.
4. On the frontend, use Firebase Auth (Google sign-in) to get an ID
   token, then send it as `Authorization: Bearer <token>` on every API
   request.

### Google OAuth setup (for YouTube connect)
1. Google Cloud Console → APIs & Services → Credentials → Create OAuth
   2.0 Client ID (type: Web application).
2. Add an authorized redirect URI matching
   `GOOGLE_OAUTH_REDIRECT_URI` in your `.env`
   (e.g. `http://localhost:8000/api/auth/youtube/callback`).
3. Enable the **YouTube Data API v3** and **YouTube Analytics API** in
   the same Google Cloud project.
4. Put the client ID/secret into `.env`.
5. The frontend should send the logged-in user to
   `GET /api/auth/youtube/connect` (a real browser navigation, not an
   XHR/fetch, since it's a redirect chain through Google).

### Database / Redis
```bash
createdb yt_ai_manager
alembic upgrade head   # migration already generated & verified — see below
                        # only re-run `alembic revision --autogenerate`
                        # if you change a model afterward

redis-server &
celery -A app.workers.celery_app worker --loglevel=info   # separate terminal
uvicorn app.main:app --reload
```

API docs at `http://localhost:8000/docs` once running.

## Hardening pass (this update)

Went through the upload → transcribe → generate and approve → publish
pipelines line by line, simulating what a real video would hit, without
real API credentials. Found and fixed real bugs, not cosmetic ones:

- **Dead code removed**: `extract_audio()` (ffmpeg) was called in the
  pipeline but its output was never used — AssemblyAI takes a remote
  URL directly and never needed the local audio file. Removed the call,
  the module, the `ffmpeg-python` dependency, and the now-impossible
  `EXTRACTING_AUDIO` status (cleaned up on the frontend too).
- **Silent/music-only videos now fail clearly**: previously a video
  with no speech would either crash inside Gemini with a generic error,
  or (worse) get whatever Gemini hallucinated from an empty prompt. Now
  `TranscriptionResult.has_speech` checks for this explicitly and the
  pipeline stops with a specific, actionable error message instead.
- **Duplicate-publish bug (the serious one)**: the publish task retried
  its *entire* body on any failure, including `yt.upload_video()`. If
  the YouTube upload succeeded but a later step (pinned comment,
  playlist) threw, a retry would upload the same video to the real
  channel a second time. Fixed by persisting `youtube_video_id`
  immediately after upload and gating all retries on it — once a video
  is on YouTube, no retry path can upload it again.
- **Retry-after-delete bug**: the pipeline task deleted the local
  uploaded file in its `finally` block unconditionally. On a Celery
  retry (same task, same file path argument), the second attempt would
  immediately fail trying to read a file the first attempt already
  deleted. Now the delete only happens once the file's actually been
  consumed (uploaded to Cloudinary), and the retry decision checks the
  file still exists before re-raising.
- **No resumability**: a transient failure during Gemini generation
  (stage 3 of 4) would cause a full pipeline retry — re-uploading to
  Cloudinary and re-transcribing with AssemblyAI from scratch, wasting
  time and paid API quota for work that already succeeded. Each stage
  now checks whether its output already exists on the `Video` row
  before redoing it.
- **Leaked temp files**: the publish task downloaded the video locally
  for the YouTube upload but never deleted it. Now cleaned up in a
  `finally` block.
- **AssemblyAI had no retry**: transient network blips during
  transcription killed the whole pipeline run immediately. Added a
  `tenacity` retry that backs off on transient errors but does NOT
  retry on an AssemblyAI-reported transcription failure (retrying won't
  fix bad audio).

These were found by careful reading and static verification (checking
the actual control flow, not just that imports succeed) — a different
pass from the live Postgres/Redis/Celery testing described above. What's
still genuinely untested is anything behind your real API keys: actual
Gemini output quality, AssemblyAI transcription behavior, and a real
YouTube upload. Those need a real run with your credentials.

## Security notes (read before deploying)

1. **Never commit `.env`** or `secrets/firebase-service-account.json` —
   both gitignored, keep it that way.
2. **Rotate any API key that has ever been pasted into a chat, ticket,
   or shared doc.** Treat it as compromised the moment it left a
   secrets manager.
3. OAuth access/refresh tokens are encrypted before being stored
   (`app/services/token_cipher.py`) and refreshed transparently
   (`app/services/youtube_auth_helper.py`) — don't bypass either.
4. Every video/channel route requires a verified Firebase user AND
   ownership of the resource. If you add new routes, depend on
   `get_current_user` and/or `get_owned_channel`/`get_owned_video` —
   don't roll your own check.
5. `FULL_AUTO` publish mode means a video goes live on a real channel
   with zero human review. Make sure your frontend makes this mode's
   risk explicit to users before they enable it per-channel.

