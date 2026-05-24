# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
make init              # Install frontend (npm) + backend (uv) dependencies
make dev               # Dev servers with hot reload (Vite + Uvicorn --reload)
make run               # Production-like local servers (build frontend first)
make stop              # Kill local servers
make docker-redeploy   # Stop, build, and restart Docker containers (also stops local servers)
make docker-stop       # Stop Docker containers
make docker-start      # Start Docker containers
```

**Ports:** Backend :3103, Frontend :8103. Frontend base path is `/aitube/`.

**Polling feeds manually:**
```bash
uv run python -m backend.scripts.poll_feeds
```

**Evaluating summarizers (Haiku vs Hermes/GPT-5.4 mini):**
```bash
HERMES_ENABLED=true uv run python -m backend.scripts.eval_summarizers --n 30
```

## Architecture

AITube is a self-hosted feed reader that unifies YouTube, podcasts, and RSS into a single timeline with AI-powered summaries and interest scoring.

### Backend (Python 3.12 + FastAPI)

- **API routers** (`backend/app/routers/`): REST endpoints with trailing slashes (required for reverse proxy). Content search returns faceted aggregations from Elasticsearch. `consumption_report.py` joins content items with playback state to return engagement signals (consumed, viewed, watch_percentage, interest).
- **Services** (`backend/app/services/`): Core processing pipeline:
  - `feed_poller.py` — Polls subscriptions, orchestrates the full pipeline: fetch feed → parse entries → dedup → scrape/transcribe → cleanup → summarize → index → deduplicate content → backfill missing transcripts
  - `content_cleanup.py` — Two-stage article cleanup: deterministic pre-clean (regex patterns) then head+tail LLM cleanup via Claude Haiku
  - `content_dlp.py` — HTTP client to content-dlp service on host (port 7055), not subprocess calls
  - `youtube_captions.py` — yt-dlp for captions + livestream detection (via `is_live`/`was_live`)
  - `summarizer.py` — content summaries with bullet-point breakdowns and timestamps. Tries Hermes (GPT-5.4 mini via SSH) first when `HERMES_ENABLED=true`, falling back to Claude Haiku on any failure. Both engines run the identical prompt from `_build_summary_prompt`.
  - `hermes_client.py` — offloads a prompt to the Hermes agent on a VPS via `ssh … 'hermes -p aitube -t "" -m gpt-5.4-mini -z "$(cat)"'` (prompt piped over stdin, read remotely with `"$(cat)"` so it needs no escaping). Returns None on any failure so the caller falls back to Haiku.
  - `summary_eval.py` — head-to-head Haiku vs Hermes: runs both engines on one item, applies deterministic format checks, and scores them with a neutral Claude Sonnet judge (blind + A/B-randomized). Backs `scripts/eval_summarizers.py`.
  - `metadata_extractor.py` — Claude Haiku for extracting podcast titles from transcripts and article metadata from scraped markdown
  - `elasticsearch.py` — Async ES client with index mappings and lifecycle
- **Config** (`backend/app/config.py`): Pydantic Settings reading from `.env`. Key: `content_dlp_url` defaults to localhost:7055, overridden to `host.docker.internal:7055` in Docker via `docker-compose.yml` environment block.

### Frontend (React 19 + TypeScript + Vite)

- `Timeline.tsx` — Main view with facet sidebar (type, status, interest, source subscription) and content card grid
- `ContentView.tsx` — Flyout panel with YouTube/audio players, article reader, transcript viewer with auto-scroll
- `AddContent.tsx` — Ad-hoc content submission with URL preview and confirm flow (YouTube, podcast MP3, article)
- `api/client.ts` — Typed fetch wrapper for all API calls

Vite config sets `base: "/aitube/"` and proxies `/aitube/api` to backend in dev mode.

### Data Flow

1. Cron runs `poll_feeds.py` every 30 minutes
2. For each subscription: fetch feed XML → parse entries → filter by age → dedup against ES
3. Per new item: scrape content (RSS) or fetch captions (YouTube) → cleanup markdown → generate AI summary → index to ES
4. Post-poll: deduplicate content items by URL, backfill missing transcripts (up to 5/cycle)
5. Frontend fetches from content search API with server-side filtering and faceted aggregations
6. Ad-hoc content: user pastes URL in Add Content page → backend detects type → preview metadata → confirm triggers background pipeline (same as polling but for individual items)

### Key Design Decisions

- **content-dlp runs on the host** (needs GPU for transcription), backend calls it via HTTP, not subprocess
- **YouTube captions via yt-dlp** happen after dedup check to avoid unnecessary API calls
- **Livestream filtering** uses yt-dlp's `is_live`/`was_live` metadata
- **RSS `<link>` parsing** falls back to `<guid>` because BeautifulSoup's HTML parser treats `<link>` as void
- **Date normalization** (`_normalize_date_to_iso`) handles RFC 2822 and other formats before ES indexing
- **Article cleanup** uses deterministic pre-clean to strip nav/footer patterns, then Claude Haiku for final polish using head+tail strategy for long articles
- **Viewed tracking** (`viewed` field) flips to `true` on first content open in the flyout panel, independent of `consumed`
- **Auto-mark-consumed** triggers at 90% playback for videos/podcasts, or on article flyout open
- **APM auth** supports both API key (Elastic Cloud Serverless) and secret token (self-managed). API key takes precedence if both are set. Frontend RUM vars (`VITE_*`) are build-time only — requires Docker rebuild to change.

### Elasticsearch Indices

- `aitube-subscriptions` — Feed subscriptions (youtube_channel, podcast, rss)
- `aitube-content-items` — All content with full-text search, facets on type/subscription_id/consumed/viewed/user_interest
- `aitube-playback-state` — Playback position tracking
- `aitube-quarantine-events` — Audit log of post-ingest quarantines (one row per item; subsequent calls are no-ops)
- `aitube-summary-evals` — Haiku-vs-Hermes summarization eval records (both summaries, format violations, latency, judge scores/winner)

### Quarantine / transcript-judge API

External trusted callers (aitube-sync, Hermes/Rex) can inspect the first ~5 min of a video's transcript and remove bad recommendations from the active timeline without polluting watch time or the preference model. The judgement layer itself lives outside AI Tube.

Auth: same posture as the rest of the API — perimeter is enforced by nginx basic-auth in front of the backend, so the FastAPI handlers do no auth themselves.

- `GET /api/content/{item_id}/transcript/?max_seconds=300` — returns segments + joined text up to the cap. If transcript not yet generated: `{"ok": false, "error": "transcript_not_ready", ...}` with 200. Also returns `viewed/consumed/user_interest/quarantined` so callers can reconcile.
- `GET /api/content/by-external-id/yt_{video_id}/transcript/?max_seconds=300` — same, looked up by external_id.
- `POST /api/content/{item_id}/quarantine/` — body `{"reason_code", "reason", "source", "mark_viewed": true, "watch_seconds": 0}`. Sets `consumed=true` + `viewed=true` (so it leaves the watchlist), persists `quarantined_at/quarantine_reason_code/quarantine_reason/quarantine_source` on the doc, and appends one row to `aitube-quarantine-events`. Idempotent: a second call returns `already_quarantined=true` and writes nothing. Does **not** set `user_interest=down` (would pollute preference model). Does **not** write to `aitube-playback-state` (no fake watch time). The content item is preserved, not deleted.
- `POST /api/content/by-external-id/yt_{video_id}/quarantine/` — same by external_id.

Suggested n8n webhook names if exposed: `aitube-content-transcript`, `aitube-quarantine-content-item`.

### Environment

- Python managed by `uv`, virtual environment at `~/.venvs/aitube`
- Frontend served via Nginx in Docker, Vite preview for `make run`, Vite dev server for `make dev`
- Dev server accessed directly on port 8103; production goes through reverse proxy at `azathought.com/aitube/`
