# AITube

Personal feed reader for YouTube, podcasts, and RSS with AI-powered curation. One unified timeline, no algorithms.

## Stack

- **Backend:** Python 3.12 / FastAPI / FastMCP
- **Frontend:** React / Vite / TypeScript
- **Data:** Elasticsearch Serverless
- **Ingestion:** content-dlp CLI
- **AI:** Claude Sonnet 4.6 for summarization and interest scoring

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

## Adding Subscriptions

Paste any URL into the subscription manager — the system auto-detects the type and resolves metadata:

- **YouTube:** channel URLs (`youtube.com/@handle`) or video URLs
- **Podcasts:** direct RSS feeds, Apple Podcasts links, or Spotify links
- **RSS/Atom:** direct feed URLs or any website (auto-discovers `<link rel="alternate">` feeds)

The resolver fetches the feed name, thumbnail, description, and sample items for preview before subscribing.

## Project Structure

```
backend/
  app/
    main.py           # FastAPI entry point
    config.py         # Settings from .env
    routers/          # API endpoints (subscriptions, content, playback, polling)
    services/
      elasticsearch.py  # ES client, index mappings, lifecycle
      content_dlp.py    # Async wrapper for content-dlp CLI
      feed_poller.py    # Poll subscriptions for new content
      url_resolver.py   # Smart URL resolution (YouTube, Apple Podcasts, Spotify, RSS discovery)
    models/           # Pydantic schemas
    mcp/              # FastMCP tool definitions
  scripts/
    poll_feeds.py     # Crontab entry point
frontend/
  src/
    components/       # React components (Timeline, SubscriptionManager, etc.)
    api/              # Typed backend API client
    theme/            # Light/dark theme
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/api/subscriptions/resolve` | Auto-detect feed type and metadata from any URL |
| POST | `/api/subscriptions` | Add a subscription |
| GET | `/api/subscriptions` | List subscriptions |
| GET | `/api/subscriptions/{id}` | Get subscription |
| PATCH | `/api/subscriptions/{id}` | Update subscription |
| DELETE | `/api/subscriptions/{id}` | Delete subscription |
| GET | `/api/content` | List/search content items |
| GET | `/api/content/{id}` | Get content item |
| GET | `/api/playback/{id}` | Get playback position |
| PUT | `/api/playback/{id}` | Update playback position |
| POST | `/api/polling/trigger` | Trigger feed poll (all active subscriptions) |
| POST | `/api/polling/trigger/{id}` | Trigger feed poll (single subscription) |
