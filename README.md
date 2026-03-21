# AITube

Personal feed reader for YouTube, podcasts, and RSS with AI-powered curation. One unified timeline, no algorithms.

## Stack

- **Backend:** Python 3.12 / FastAPI / FastMCP
- **Frontend:** React / Vite / TypeScript
- **Data:** Elasticsearch Serverless
- **Ingestion:** [content-dlp](https://github.com/your-repo/content-dlp) CLI
- **AI:** Claude Sonnet 4.6 for summarization and interest scoring

## Setup

### Backend

Requires [uv](https://docs.astral.sh/uv/).

```bash
# Install dependencies (venv lives at ~/.venvs/aitube)
uv sync

# Copy and configure environment variables
cp .env.example .env
# Edit .env with your Elasticsearch and Anthropic API keys

# Run the dev server
uv run aitube-server
```

The API starts at `http://localhost:8000`. Health check: `GET /health`.

### Frontend

Requires Node.js 22+.

```bash
cd frontend
npm install
npm run dev
```

The dev server starts at `http://localhost:5173` and proxies `/api` requests to the backend.

### Docker

```bash
docker compose up --build
```

- Backend: `http://localhost:8000`
- Frontend: `http://localhost:3000`

## Project Structure

```
backend/
  app/
    main.py           # FastAPI entry point
    config.py         # Settings from .env
    routers/          # API endpoints (subscriptions, content, playback, polling)
    services/         # Elasticsearch, content-dlp wrapper, AI summarizer
    models/           # Pydantic schemas
    mcp/              # FastMCP tool definitions
  scripts/
    poll_feeds.py     # Crontab entry point
frontend/
  src/
    components/       # React components (Timeline, Player, Reader, etc.)
    api/              # Backend API client
    theme/            # Light/dark theme
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/api/subscriptions` | Add a subscription |
| GET | `/api/subscriptions` | List subscriptions |
| GET | `/api/subscriptions/{id}` | Get subscription |
| PATCH | `/api/subscriptions/{id}` | Update subscription |
| DELETE | `/api/subscriptions/{id}` | Delete subscription |
| GET | `/api/content` | List/search content items |
| GET | `/api/content/{id}` | Get content item |
| GET | `/api/playback/{id}` | Get playback position |
| PUT | `/api/playback/{id}` | Update playback position |
| POST | `/api/polling/trigger` | Trigger feed poll |
