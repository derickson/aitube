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

## Architecture

AITube is a self-hosted feed reader that unifies YouTube, podcasts, and RSS into a single timeline with AI-powered summaries and interest scoring.

### Backend (Python 3.12 + FastAPI)

- **API routers** (`backend/app/routers/`): REST endpoints with trailing slashes (required for reverse proxy). Content search returns faceted aggregations from Elasticsearch. `consumption_report.py` joins content items with playback state to return engagement signals (consumed, viewed, watch_percentage, interest).
- **Services** (`backend/app/services/`): Core processing pipeline:
  - `feed_poller.py` — Polls subscriptions, orchestrates the full pipeline: fetch feed → parse entries → dedup → scrape/transcribe → cleanup → summarize → index → deduplicate content → backfill missing transcripts
  - `content_cleanup.py` — Two-stage article cleanup: deterministic pre-clean (regex patterns) then head+tail LLM cleanup via Claude Haiku
  - `content_dlp.py` — HTTP client to content-dlp service on host (port 7055), not subprocess calls
  - `youtube_captions.py` — yt-dlp for captions + livestream detection (via `is_live`/`was_live`)
  - `summarizer.py` — Claude Sonnet for content summaries with bullet-point breakdowns and timestamps
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

### Environment

- Python managed by `uv`, virtual environment at `~/.venvs/aitube`
- Frontend served via Nginx in Docker, Vite preview for `make run`, Vite dev server for `make dev`
- Dev server accessed directly on port 8103; production goes through reverse proxy at `azathought.com/aitube/`
