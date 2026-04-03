# AITube

![AITube Screenshot](splash.jpg)

Personal feed reader for YouTube, podcasts, and RSS with AI-powered curation. One unified timeline, no algorithms.

## Stack

- **Backend:** Python 3.12 / FastAPI
- **Frontend:** React 19 / Vite / TypeScript
- **Data:** Elasticsearch
- **Ingestion:** [content-dlp](https://github.com/derickson/content-dlp) HTTP service + yt-dlp for YouTube captions
- **AI:** Claude Sonnet for summaries, ad detection, interest scoring, and content chat

## Setup

### Quick Start (recommended)

Requires [uv](https://docs.astral.sh/uv/) and Node.js 22+.

```bash
# Install all dependencies (frontend + backend)
make init

# Copy and configure environment variables
cp .env.example .env
# Edit .env with your Elasticsearch and Anthropic API keys

# Start dev servers with hot reload (backend :3103, frontend :8103)
make dev

# Stop servers
make stop
```

### Manual Setup

#### Backend

```bash
# Install dependencies (venv lives at ~/.venvs/aitube)
uv sync

# Run the dev server
uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 3103 --reload
```

The API starts at `http://localhost:3103`. Health check: `GET /health`.

#### Frontend

```bash
cd frontend
npm install
npm run dev
```

The dev server starts at `http://localhost:8103/aitube/` and proxies API requests to the backend.

### Docker

```bash
docker compose up --build
```

- Backend: `http://localhost:3103`
- Frontend: `http://localhost:8103/aitube/`

### Reverse Proxy

The frontend is hosted under the `/aitube/` path, making it easy to serve alongside other apps behind a reverse proxy. Point your reverse proxy at `http://host:8103/aitube/` for the UI and `http://host:3103/api/` for the backend API.

### Cron

Poll feeds automatically every 30 minutes (adjust paths for your environment):

```
*/30 * * * * cd /path/to/aitube && uv run python -m backend.scripts.poll_feeds >> /path/to/aitube/cron.log 2>&1
```

Or poll manually:

```bash
uv run python -m backend.scripts.poll_feeds
```

## Configuration

Set in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `ELASTICSEARCH_URL` | `http://localhost:9200` | Elasticsearch endpoint |
| `ELASTICSEARCH_API_KEY` | | Elasticsearch API key |
| `ANTHROPIC_API_KEY` | | Claude API key for summaries and ad detection |
| `CONTENT_DLP_URL` | `http://localhost:7055` | content-dlp HTTP service URL |
| `YOUTUBE_MAX_AGE_DAYS` | `5` | Only poll YouTube videos newer than this |
| `PODCAST_MAX_AGE_DAYS` | `5` | Only poll podcast episodes newer than this |
| `RSS_MAX_AGE_DAYS` | `90` | Only poll RSS articles newer than this |
| `ELASTIC_APM_SERVER_URL` | | Elastic APM server URL (enables backend observability) |
| `ELASTIC_APM_API_KEY` | | APM agent API key (for Elastic Cloud Serverless) |
| `ELASTIC_APM_SECRET_TOKEN` | | APM secret token (for self-managed APM Server) |
| `ELASTIC_APM_ENVIRONMENT` | `development` | APM environment label |
| `VITE_ELASTIC_APM_SERVER_URL` | | APM server URL for frontend RUM (build-time) |
| `VITE_ELASTIC_APM_ENVIRONMENT` | `development` | APM environment for frontend RUM (build-time) |

### Observability (Elastic APM)

Optional. Set `ELASTIC_APM_SERVER_URL` to enable backend tracing (services: `aitube-backend`, `aitube-poller`). Set `VITE_ELASTIC_APM_SERVER_URL` to enable frontend Real User Monitoring (service: `aitube-frontend`).

**Auth:** Use `ELASTIC_APM_API_KEY` for Elastic Cloud Serverless (create via Kibana `POST kbn:/api/apm/agent_keys`), or `ELASTIC_APM_SECRET_TOKEN` for self-managed APM Server. If both are set, API key takes precedence. Frontend RUM requires no secret — the public APM URL is sufficient.

**Note:** Frontend APM vars (`VITE_*`) are baked in at Docker build time. Rebuild the frontend container after changing them.

## Adding Content

### Subscriptions (ongoing feeds)

Paste any URL into the subscription manager — the system auto-detects the type and resolves metadata:

- **YouTube:** channel URLs (`youtube.com/@handle`) or video URLs
- **Podcasts:** direct RSS feeds, Apple Podcasts links, or Spotify links
- **RSS/Atom:** direct feed URLs or any website (auto-discovers `<link rel="alternate">` feeds, probes common feed paths relative to the URL and domain root)

The resolver fetches the feed name, thumbnail, description, and sample items for preview before subscribing. A warning is shown if no feed is discovered. YouTube Shorts are automatically filtered out.

### Individual items (Add Content page)

Use the **Add Content** page in the nav bar to add individual items without creating a subscription:

- **YouTube videos** — paste any video URL. Preview shows title, thumbnail, duration, and channel name.
- **Podcast episodes** — paste an MP3/audio URL. Title is automatically extracted from the transcript using AI after processing.
- **Web articles** — paste any article URL. The page is scraped via Jina Reader during preview, showing the extracted title and thumbnail.

Content type is auto-detected from the URL. A preview card shows available metadata before you confirm. Processing (transcription, summarization) happens in the background after confirmation. All ad-hoc items appear in the timeline with `subscription_id: "adhoc"`.

## Content Pipeline

When new content is discovered during polling:

1. **YouTube videos:** captions fetched via yt-dlp (instant, no download). Falls back to content-dlp transcription if captions unavailable. Videos that fail transcript fetch (e.g., rate limits) are automatically retried on subsequent poll cycles.
2. **Podcast episodes:** audio downloaded and transcribed locally via content-dlp. Claude detects ads in the first 90 seconds and sets the playback position to skip past them.
3. **RSS articles:** full page scraped to markdown via content-dlp webscrape.
4. **All types:** Claude generates a summary with a bullet-point breakdown of key topics. Videos and podcasts include clickable timestamps that seek the player. Duplicate content items are automatically detected and removed after each poll cycle.


## Features

- **Unified timeline** with faceted search (type, watched/unwatched, interest, source) powered by Elasticsearch
- **Flyout content viewer** with embedded YouTube player, HTML5 audio player, and distraction-free article reader
- **Content chat** — ask questions about any content item with streaming AI responses; configurable agents with clickable timestamp citations for video/podcast seek
- **Timestamped transcripts** with live playback highlighting and click-to-seek
- **Viewed tracking** — marks content as viewed on first open, independent of playback completion
- **Playback tracking** with resume from last position and 90% auto-complete
- **Consumption report** API — JSON report of engagement signals (consumed, viewed, watch percentage, interest) with filters
- **Interest voting** (up/down) per content item to mark what's interesting
- **AI summaries** with bullet-point breakdowns and clickable timestamp links for video/podcast seek
- **Ad skip** for podcasts — Claude detects sponsor reads and sets playback past them
- **Smart URL resolution** for YouTube channels, Apple Podcasts, Spotify, and RSS discovery
- **Light/dark theme** toggle
- **Ad-hoc content** — add any YouTube video, podcast MP3, or web article directly via the Add Content page with metadata preview before processing
- **Subscription management** with per-feed interest notes, type-colored cards, search, and filters

## Project Structure

```
backend/
  app/
    main.py              # FastAPI entry point
    config.py            # Settings from .env
    routers/
      subscriptions.py   # CRUD + URL resolution
      content.py         # Search, facets, CSV export, interest, consumed, viewed
      consumption_report.py # Engagement report endpoint
      playback.py        # Position tracking
      polling.py         # Feed poll triggers
      chat.py            # Streaming content Q&A with agents
      add_content.py   # Add Content preview + confirm (YouTube, podcast, article)
    services/
      elasticsearch.py   # ES client, index mappings, lifecycle
      content_dlp.py     # HTTP client for content-dlp service on host
      feed_poller.py     # Poll subscriptions, transcribe, summarize
      url_resolver.py    # Smart URL resolution
      youtube_captions.py # yt-dlp caption fetching
      ad_detector.py     # Claude-powered podcast ad detection
      summarizer.py      # Claude-powered content summaries
      metadata_extractor.py # LLM-powered metadata extraction for ad-hoc content
      content_cleanup.py # Two-stage article cleanup (regex + LLM)
      agents.py          # Agent registry for content chat
    models/              # Pydantic schemas
  scripts/
    poll_feeds.py        # Crontab entry point
frontend/
  public/
    images/              # Pixel art assets (logo, empty states)
  src/
    components/
      Timeline.tsx           # Content grid with facet sidebar
      ContentView.tsx        # Flyout player/reader with transcript
      ContentTabs.tsx        # Tab switcher for content view panels
      ChatPanel.tsx          # Streaming chat for content Q&A
      SubscriptionManager.tsx # Subscription CRUD with URL resolver
      AddContent.tsx         # Ad-hoc content submission with preview
      ErrorBanner.tsx        # Error display with clipboard copy
    api/client.ts        # Typed backend API client
    theme/               # Light/dark theme
```

## API Endpoints

All API paths use trailing slashes. This is required for compatibility with reverse proxies that add trailing slashes via rewrite rules.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health/` | Health check |
| POST | `/api/subscriptions/resolve/` | Auto-detect feed type and metadata from any URL |
| POST | `/api/subscriptions/` | Add a subscription |
| GET | `/api/subscriptions/` | List subscriptions (with content counts) |
| GET | `/api/subscriptions/{id}/` | Get subscription |
| PATCH | `/api/subscriptions/{id}/` | Update subscription |
| DELETE | `/api/subscriptions/{id}/` | Delete subscription |
| GET | `/api/content/` | Search content with facets (type, consumed, interest) |
| GET | `/api/content/export/csv/` | Export all content as CSV |
| GET | `/api/content/{id}/` | Get content item |
| PUT | `/api/content/{id}/consumed/` | Set consumed status |
| PUT | `/api/content/{id}/viewed/` | Mark content as viewed |
| PUT | `/api/content/{id}/interest/` | Set interest (up/down/none) |
| POST | `/api/content/{id}/transcribe/` | Trigger transcription for a content item |
| DELETE | `/api/content/{id}/` | Delete content item |
| DELETE | `/api/content/by-external-id/{external_id}/` | Delete content item by external_id |
| POST | `/api/content/playback-progress/` | Batch get playback progress |
| GET | `/api/playback/{id}/` | Get playback position |
| PUT | `/api/playback/{id}/` | Update playback position |
| POST | `/api/polling/trigger/` | Trigger feed poll (all active subscriptions) |
| POST | `/api/polling/trigger/{id}/` | Trigger feed poll (single subscription) |
| GET | `/api/chat/agents/` | List available chat agents |
| POST | `/api/chat/{item_id}/stream/` | Stream chat response for a content item |
| GET | `/api/watchlist/` | Unwatched YouTube videos |
| POST | `/api/submit_video/` | Submit YouTube URLs for background processing |
| GET | `/api/consumption_report/` | Engagement report (consumed, viewed, watch %, interest) |
| POST | `/api/add-content/preview/` | Preview metadata for any URL (YouTube, podcast MP3, article) |
| POST | `/api/add-content/confirm/` | Confirm and process previewed content in background |

## Automation API

The watchlist endpoints are designed for external tools and scripts that want to interact with AITube programmatically.

### Get unwatched YouTube videos

Returns all YouTube videos that haven't been marked as watched, sorted by publish date (newest first).

```bash
curl http://localhost:3103/api/watchlist/
```

Supports pagination via `size` (default 50, max 200) and `offset` query parameters:

```bash
curl "http://localhost:3103/api/watchlist/?size=10&offset=0"
```

Response is a JSON array of content items:

```json
[
  {
    "id": "abc123",
    "title": "Video Title",
    "url": "https://www.youtube.com/watch?v=...",
    "type": "video",
    "consumed": false,
    "published_at": "2026-03-30T12:00:00Z",
    "duration_seconds": 612,
    "subscription_id": "sub_id_or_adhoc",
    ...
  }
]
```

The response excludes `summary`, `transcript`, and `content_markdown` to keep payloads lightweight. Use `GET /api/content/{id}/` to fetch the full item.

### Submit ad-hoc YouTube videos

Send an array of YouTube URLs for AITube to fetch, transcribe, and summarize in the background. These don't need to belong to any subscription. The endpoint returns immediately — processing happens asynchronously.

```bash
curl -X POST http://localhost:3103/api/submit_video/ \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]}'
```

Response:

```json
{
  "accepted": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
  "skipped": [],
  "errors": []
}
```

- **accepted** — URLs that will be processed in the background
- **skipped** — URLs for videos already in the system (deduped by video ID)
- **errors** — URLs that couldn't be parsed as valid YouTube links

Accepted URL formats: `youtube.com/watch?v=`, `youtu.be/`, `youtube.com/embed/`, `youtube.com/shorts/`

Processing per video takes 1-3 minutes (caption fetch + AI summarization). Once complete, the video appears in the watchlist and content search. Ad-hoc videos are stored with `subscription_id: "adhoc"`.

### Consumption report

Returns engagement signals for recent content items, sorted by publish date (newest first). Joins content metadata with playback state to compute watch percentage.

```bash
# All content (default: 100 items)
curl http://localhost:3103/api/consumption_report/

# Filter by content type
curl "http://localhost:3103/api/consumption_report/?content_type=video&size=10"

# Filter by subscription
curl "http://localhost:3103/api/consumption_report/?subscription_id=sub123"

# Single content item
curl "http://localhost:3103/api/consumption_report/?content_item_id=abc123"
```

Response:

```json
[
  {
    "subscription_id": "sub123",
    "content_item_id": "abc123",
    "content_type": "video",
    "title": "Video Title",
    "published_at": "2026-03-30T12:00:00Z",
    "consumed": true,
    "viewed": true,
    "watch_percentage": 92.5,
    "interest": "up"
  }
]
```

- **consumed** — `true` if 90%+ watched/listened, or manually marked
- **viewed** — `true` if the user has opened the content in the UI
- **watch_percentage** — playback position as percent of duration (`null` for articles or if no playback data)
- **interest** — `"up"`, `"down"`, or `null`

### Delete a video by external ID

Delete a content item directly using its external ID, without needing to look up the internal document ID first. YouTube videos use the format `yt_{video_id}`.

```bash
curl -X DELETE http://localhost:3103/api/content/by-external-id/yt_dQw4w9WgXcQ/
```

Response:

```json
{
  "status": "deleted",
  "external_id": "yt_dQw4w9WgXcQ",
  "id": "abc123-internal-doc-id"
}
```

Returns 404 if no content item with that external ID exists.

### Add ad-hoc content (any type)

The Add Content API supports YouTube videos, podcast MP3s, and web articles via a two-step preview-then-confirm flow.

**Step 1: Preview** — Detect content type and fetch metadata:

```bash
# YouTube video
curl -X POST http://localhost:3103/api/add-content/preview/ \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'

# Podcast MP3
curl -X POST http://localhost:3103/api/add-content/preview/ \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/episode.mp3"}'

# Web article
curl -X POST http://localhost:3103/api/add-content/preview/ \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/article"}'
```

Response includes a `preview_id` and detected metadata:

```json
{
  "preview_id": "uuid",
  "url": "https://...",
  "detected_type": "video",
  "title": "Video Title",
  "thumbnail_url": "https://...",
  "duration_seconds": 212,
  "published_at": "2026-03-30T00:00:00+00:00",
  "description": "...",
  "author": "Channel Name",
  "file_size_bytes": null
}
```

Type detection: YouTube URLs are detected by regex, audio files (`.mp3`, `.m4a`, etc.) become podcast episodes, everything else is treated as an article. For articles, Jina Reader scrapes the page during preview to extract title and thumbnail.

**Step 2: Confirm** — Submit for background processing:

```bash
curl -X POST http://localhost:3103/api/add-content/confirm/ \
  -H "Content-Type: application/json" \
  -d '{"preview_id": "uuid-from-step-1", "title_override": "Optional custom title"}'
```

Returns `{"status": "accepted"}` immediately. Processing happens in the background:
- **YouTube:** captions + AI summary (1-3 min)
- **Podcast:** audio transcription + LLM title extraction + AI summary (3-10 min depending on length)
- **Article:** markdown cleanup + LLM metadata extraction + AI summary (30-60 sec)

All ad-hoc content uses `subscription_id: "adhoc"`. Preview data expires after 30 minutes. Returns 409 if the content already exists.
