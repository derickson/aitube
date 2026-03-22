# AITube

Personal feed reader for YouTube, podcasts, and RSS with AI-powered curation. One unified timeline, no algorithms.

## Stack

- **Backend:** Python 3.12 / FastAPI / FastMCP
- **Frontend:** React / Vite / TypeScript
- **Data:** Elasticsearch Serverless
- **Ingestion:** content-dlp CLI + yt-dlp for YouTube captions
- **AI:** Claude Sonnet 4.6 for summaries, ad detection, and interest scoring

## Setup

### Backend

Requires [uv](https://docs.astral.sh/uv/).

```bash
# Install dependencies (venv lives at ~/.venvs/aitube)
# Set UV_PROJECT_ENVIRONMENT=~/.venvs/aitube or use .env.uv
uv sync

# Copy and configure environment variables
cp .env.example .env
# Edit .env with your Elasticsearch and Anthropic API keys

# Run the dev server
UV_PROJECT_ENVIRONMENT=~/.venvs/aitube uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 3103 --reload
```

The API starts at `http://localhost:3103`. Health check: `GET /health`.

### Frontend

Requires Node.js 22+.

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

Poll feeds automatically every 30 minutes:

```
*/30 * * * * cd /home/dave/dev/aitube && UV_PROJECT_ENVIRONMENT=/home/dave/.venvs/aitube /home/dave/.venvs/aitube/bin/python -m backend.scripts.poll_feeds >> /home/dave/dev/aitube/cron.log 2>&1
```

## Configuration

Set in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `ELASTICSEARCH_URL` | `http://localhost:9200` | ES Serverless endpoint |
| `ELASTICSEARCH_API_KEY` | | ES API key |
| `ANTHROPIC_API_KEY` | | Claude API key for summaries and ad detection |
| `YOUTUBE_MAX_AGE_DAYS` | `5` | Only poll YouTube videos newer than this |
| `PODCAST_MAX_AGE_DAYS` | `5` | Only poll podcast episodes newer than this |
| `RSS_MAX_AGE_DAYS` | `120` | Only poll RSS articles newer than this |

## Adding Subscriptions

Paste any URL into the subscription manager — the system auto-detects the type and resolves metadata:

- **YouTube:** channel URLs (`youtube.com/@handle`) or video URLs
- **Podcasts:** direct RSS feeds, Apple Podcasts links, or Spotify links
- **RSS/Atom:** direct feed URLs or any website (auto-discovers `<link rel="alternate">` feeds)

The resolver fetches the feed name, thumbnail, description, and sample items for preview before subscribing. YouTube Shorts are automatically filtered out.

## Content Pipeline

When new content is discovered during polling:

1. **YouTube videos:** captions fetched via yt-dlp (instant, no download). Falls back to content-dlp Parakeet transcription if captions unavailable.
2. **Podcast episodes:** audio downloaded and transcribed locally via content-dlp Parakeet TDT. Claude detects ads in the first 90 seconds and sets the playback position to skip past them.
3. **RSS articles:** full page scraped to markdown via content-dlp webscrape.
4. **All types:** Claude generates a 2-3 sentence summary that cuts through clickbait to explain the actual topic, opinion, or thesis.

## Features

- **Unified timeline** with faceted search (type, watched/unwatched, interest) powered by Elasticsearch
- **Flyout content viewer** with embedded YouTube player, HTML5 audio player, and distraction-free article reader
- **Timestamped transcripts** with live playback highlighting and click-to-seek
- **Playback tracking** with resume from last position and 90% auto-complete
- **Interest voting** (up/down) per content item to mark what's interesting
- **AI summaries** that explain what content is actually about
- **Ad skip** for podcasts — Claude detects sponsor reads and sets playback past them
- **Smart URL resolution** for YouTube channels, Apple Podcasts, Spotify, and RSS discovery
- **Light/dark theme** toggle
- **Subscription management** with per-feed interest notes, type-colored cards, search, and filters

## Project Structure

```
backend/
  app/
    main.py              # FastAPI entry point
    config.py            # Settings from .env
    routers/
      subscriptions.py   # CRUD + URL resolution
      content.py         # Search, facets, CSV export, interest, consumed
      playback.py        # Position tracking
      polling.py         # Feed poll triggers
    services/
      elasticsearch.py   # ES client, index mappings, lifecycle
      content_dlp.py     # Async wrapper for content-dlp CLI
      feed_poller.py     # Poll subscriptions, transcribe, summarize
      url_resolver.py    # Smart URL resolution
      youtube_captions.py # yt-dlp caption fetching
      ad_detector.py     # Claude-powered podcast ad detection
      summarizer.py      # Claude-powered content summaries
    models/              # Pydantic schemas
    mcp/                 # FastMCP tool definitions
  scripts/
    poll_feeds.py        # Crontab entry point
frontend/
  src/
    components/
      Timeline.tsx           # Content grid with facet sidebar
      ContentView.tsx        # Flyout player/reader with transcript
      SubscriptionManager.tsx # Subscription CRUD with URL resolver
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
| PUT | `/api/content/{id}/interest/` | Set interest (up/down/none) |
| POST | `/api/content/{id}/transcribe/` | Trigger transcription for a content item |
| POST | `/api/content/playback-progress/` | Batch get playback progress |
| GET | `/api/playback/{id}/` | Get playback position |
| PUT | `/api/playback/{id}/` | Update playback position |
| POST | `/api/polling/trigger/` | Trigger feed poll (all active subscriptions) |
| POST | `/api/polling/trigger/{id}/` | Trigger feed poll (single subscription) |
| DELETE | `/api/content/{id}/` | Delete content item |
