# AITube

AITube is a personal feed-reader-style web app for consuming YouTube videos, podcasts, and RSS content in a single unified timeline. The goal is to replace algorithm-driven doom scrolling with a curated, self-hosted experience where AI helps surface what matters most.


## Technology Stack

### Backend — Python (FastAPI + FastMCP)
- **Package manager:** uv
- **Virtual environment:** `~/.venvs/aitube` (created via `uv venv ~/.venvs/aitube`)
- **Framework:** FastAPI for REST endpoints; FastMCP for AI agent/skill integration
- **Key dependencies:** `fastapi`, `uvicorn`, `elasticsearch`, `anthropic`, `httpx`, `pydantic`
- **Project layout:** `uv init` with `pyproject.toml` at repo root

### Frontend — React
- Single-page app, bundled with Vite
- Lives in `frontend/` subdirectory
- Light/dark theme toggle

### Data — Elasticsearch Serverless
- Primary cloud-hosted data store for all content, subscriptions, playback state, and search
- One index per document type (subscriptions, content items, playback history)

### Content Ingestion — content-dlp (local CLI)
Installed separately on the host. Subcommands used by AITube:
| Subcommand | Purpose | Key flags |
|---|---|---|
| `content-dlp youtube <url>` | Fetch YouTube metadata + audio | `--no-audio`, `--video`, `--transcript`, `--force` |
| `content-dlp podcast <rss-url>` | Parse podcast RSS, fetch episodes | `--episodes N`, `--no-audio`, `--transcript` |
| `content-dlp webscrape <url>` | Scrape page to markdown via Jina Reader | `--no-content`, `--force` |
| `content-dlp transcribe <file>` | Local audio-to-text via Parakeet TDT | |

All subcommands output JSON to stdout (status on stderr). Results are cached in `~/content-dlp-data/` by content ID.

### Scheduling — crontab (bare metal)
A crontab entry on the host machine runs a polling script on a schedule (e.g. every 30 min) that calls backend endpoints to check feeds for new content.

### Deployment — Docker
- `backend/Dockerfile` — Python FastAPI service
- `frontend/Dockerfile` — Nginx serving the built React app
- `docker-compose.yml` — orchestrates both containers, exposes ports


## Project Structure

```
aitube/
├── pyproject.toml              # uv-managed Python project
├── uv.lock
├── docker-compose.yml
├── plan.md
├── backend/
│   ├── Dockerfile
│   ├── app/
│   │   ├── main.py             # FastAPI app entry point
│   │   ├── config.py           # Settings (ES connection, API keys, etc.)
│   │   ├── routers/
│   │   │   ├── subscriptions.py
│   │   │   ├── content.py
│   │   │   ├── playback.py
│   │   │   └── polling.py
│   │   ├── services/
│   │   │   ├── elasticsearch.py
│   │   │   ├── content_dlp.py  # Wraps CLI calls to content-dlp
│   │   │   ├── summarizer.py   # Claude Sonnet 4.6 summarization + interest scoring
│   │   │   └── feed_poller.py
│   │   ├── models/             # Pydantic schemas
│   │   └── mcp/                # FastMCP tool definitions
│   └── scripts/
│       └── poll_feeds.py       # Entry point for crontab
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── App.tsx
│       ├── components/
│       │   ├── Timeline.tsx
│       │   ├── Player.tsx      # YouTube embed + HTML5 audio
│       │   ├── Transcript.tsx
│       │   ├── Reader.tsx      # RSS distraction-free reader
│       │   ├── Chatbot.tsx
│       │   └── SubscriptionManager.tsx
│       ├── hooks/
│       ├── api/                # Backend API client
│       └── theme/              # Light/dark theme
└── cron/
    └── aitube.cron             # Crontab entry file
```


## Elasticsearch Index Design

### `subscriptions`
```json
{
  "id": "uuid",
  "type": "youtube_channel | podcast | rss",
  "url": "feed or channel URL",
  "name": "display name",
  "description": "feed description",
  "interest_notes": "user-written annotation for AI curation",
  "status": "active | muted | unfollowed",
  "added_at": "datetime",
  "last_polled_at": "datetime"
}
```

### `content_items`
```json
{
  "id": "uuid",
  "subscription_id": "ref to subscription",
  "external_id": "youtube video ID / episode GUID / page URL",
  "type": "video | podcast_episode | article",
  "title": "string",
  "url": "original URL",
  "published_at": "datetime",
  "discovered_at": "datetime",
  "duration_seconds": "number (null for articles)",
  "thumbnail_url": "string",
  "summary": "AI-generated summary",
  "interest_score": "0.0–1.0 (AI-rated relevance)",
  "interest_reasoning": "short explanation from LLM",
  "transcript": {
    "text": "full transcript text (if available)",
        "chunks": [
            {
                "text": "the transcript again in timestamp alligned text snippets",
                "start": 0.0,
                "end": 6.4
            },
            {
                "text": "the second snippet",
                "start": 6.4,
                "end": 12.4
            },
            ...
        ]
  },
  "content_markdown": "scraped article body (RSS items)",
  "content_dlp_cache_id": "content-dlp cache key for re-fetching",
  "metadata": "object — flexible per-type metadata"
}
```

### `playback_state`
```json
{
  "content_item_id": "ref to content_items",
  "position_seconds": "number (5-second granularity)",
  "consumed": "boolean (true when ≥90% watched/listened)",
  "last_updated_at": "datetime"
}
```


## Features — Detailed

### 1. Unified Timeline (Landing Page)
- Default view: unwatched/unread items, newest first
- Filter/sort by: source type, subscription, genre, interest score, date range
- Full-text search across titles, summaries, and transcripts (powered by ES)
- Muted subscriptions hidden from default view but accessible via filter
- Interest score badge on each card so high-value items stand out

### 2. Subscription Management
- Add subscriptions by URL (auto-detect type: YouTube channel, podcast RSS, web RSS)
- Per-subscription fields: name, status (active/muted/unfollowed), interest notes
- Interest notes are free-text guidance for the AI scorer (e.g. "only reviews, skip interviews")
- List view with last-polled time, item count, and quick mute/unfollow actions

### 3. Content Feed Polling
- Crontab triggers `poll_feeds.py` on schedule
- For each active subscription:
  1. Fetch latest items via content-dlp (`--no-audio` for discovery pass)
  2. Diff against ES to find new items
  3. For new items: store metadata, run AI summarization + interest scoring
  4. Optionally fetch audio/transcript for high-interest items only (saves bandwidth)
- AI scoring pipeline (Claude Sonnet 4.6 via Anthropic API):
  - Inputs: item title, description/summary, subscription interest notes, recent consumption history
  - Outputs: 0.0–1.0 interest score + short reasoning string
  - Batch-friendly: score multiple items per API call to reduce cost

### 4. Content Consumption
- **YouTube:** Embedded YouTube iframe player with API for playback position tracking
- **Podcasts:** HTML5 `<audio>` element with custom controls; position tracked every 5 seconds
- **RSS articles:** Clean reader view rendering `content_markdown`, with inline images and a link to the original URL
- Playback position persisted to ES; resume from where you left off
- Item marked consumed at 90% completion; hidden from default "unwatched" timeline

### 5. Transcript Viewer + Clickable Timestamps
- Display timestamped transcript alongside the player
- Click any line to seek the player to that timestamp
- Highlight the currently-playing transcript line

### 6. In-Context Chatbot
- Sidebar chat panel per content item
- Context: the item's transcript or article markdown
- Powered by Claude API; useful for Q&A, "explain this part", "summarize the key points"
- No persistent chat history needed (ephemeral per session)


## Implementation Phases

### Phase 1 — Foundation
- `uv init` the project, set up `~/.venvs/aitube`
- Scaffold FastAPI backend with health check
- Set up Elasticsearch indices
- Scaffold React frontend with Vite, routing, and theme toggle
- Docker Compose for local dev

### Phase 2 — Subscriptions + Polling
- CRUD API for subscriptions
- Subscription management UI
- Feed polling service (content-dlp integration)
- New content detection and storage in ES

### Phase 3 — Timeline + Consumption
- Timeline API (search, filter, sort)
- Timeline UI with content cards
- YouTube and audio players with playback tracking
- RSS reader view
- Playback state persistence

### Phase 4 — AI Features
- Summarization pipeline (Claude Sonnet 4.6)
- Interest scoring with subscription context
- Transcript viewer with clickable timestamps
- In-context chatbot

### Phase 5 — Polish
- Crontab setup and monitoring
- Docker production builds
- Error handling, loading states, empty states
- Mobile-responsive layout


## content-dlp Reference

Installed separately. Use `content-dlp --help` for full docs.

```
content-dlp youtube "https://www.youtube.com/watch?v=VIDEO_ID"
content-dlp podcast "https://feeds.example.com/podcast.xml"
content-dlp podcast --episodes 3 --no-audio "https://feeds.example.com/podcast.xml"
content-dlp webscrape "https://example.com/page"
content-dlp transcribe /path/to/audio.mp3
```

JSON output to stdout. Results cached in `~/content-dlp-data/` by content ID. Use `--force` to bypass cache.
