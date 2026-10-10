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

**Repairing summaries that stored an API error payload:**
```bash
uv run python -m backend.scripts.repair_error_summaries --dry-run
HERMES_ENABLED=true uv run python -m backend.scripts.repair_error_summaries
```

**Repairing feed markup in already-indexed docs:**
```bash
uv run python -m backend.scripts.clean_feed_markup --dry-run   # report only
uv run python -m backend.scripts.clean_feed_markup             # rewrite title/description/author
```

**Backfilling topics (subcategories) for already-categorized items:**
```bash
uv run python -m backend.scripts.backfill_categories --missing-subcategory --dry-run --limit 40
uv run python -m backend.scripts.backfill_categories --missing-subcategory
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
  - `feed_poller.py` — Polls subscriptions, orchestrates the full pipeline: fetch feed → parse entries → dedup → scrape/transcribe → cleanup → summarize → index → deduplicate content → backfill missing transcripts → backfill missing summaries → retry failed summaries
  - `content_cleanup.py` — Two-stage article cleanup: deterministic pre-clean (regex patterns) then head+tail LLM cleanup via Claude Haiku
  - `content_dlp.py` — HTTP client to content-dlp service on host (port 7055), not subprocess calls
  - `youtube_captions.py` — yt-dlp for captions + livestream detection (via `is_live`/`was_live`)
  - `summarizer.py` — content summaries with bullet-point breakdowns and timestamps. Tries Hermes (GPT-5.4 mini via SSH) first when `HERMES_ENABLED=true`, falling back to Claude Haiku on any failure. Both engines run the identical prompt from `_build_summary_prompt`. `summarize_content()` returns `(summary, error)`; on total failure the item is stored with `summary_error`/`summary_failed_at` instead of a summary, so `feed_poller.retry_failed_summaries()` can find and retry it later.
  - `hermes_client.py` — offloads a prompt to the Hermes agent on a VPS via `ssh … 'hermes -p aitube -t "" -m gpt-5.4-mini -z "$(cat)"'` (prompt piped over stdin, read remotely with `"$(cat)"` so it needs no escaping). `run_oneshot()` returns `(text, error)`; text is None on any failure (disabled, timeout, non-zero exit, empty output, **upstream API error**) so the caller falls back to Haiku, and `error` carries the raw failure reason for persistence. **Important:** `hermes -z` prints upstream API errors to *stdout* and still exits 0 (e.g. "HTTP 404: The model `gpt-5.5` does not exist or you do not have access to it.", "API call failed after 3 retries: HTTP 503: Service Unavailable"), so a zero exit status is not proof of success. Without a body check the error text is stored as the item's summary, which also hides the item from `retry_failed_summaries` (it keys off `summary_error`, which stays unset). `looks_like_error_response()` is the gate: it rejects an empty body, a *leading* `HTTP <status>` line (anchored — a real summary may discuss status codes), and anything under `_MIN_SUMMARY_CHARS` (200). The length check is load-bearing, since not every error payload starts with a status line. Measured over all 3,233 indexed summaries: shortest legitimate summary 307 chars, every error payload under ~120, zero false positives. It is deliberately **not** a format check — gating on the prompt's "exactly 5 bullets" would reject 7.7% of real summaries (102 have no bullets at all). `scripts/repair_error_summaries.py` imports the same predicate so detection and repair can't drift.
  - `summary_eval.py` — head-to-head Haiku vs Hermes: runs both engines on one item, applies deterministic format checks, and scores them with a neutral Claude Sonnet judge (blind + A/B-randomized). Backs `scripts/eval_summarizers.py`.
  - `feed_text.py` — `clean_feed_text()`: the single sanitizer for free text pulled out of feed XML. Unwraps CDATA, resolves HTML entities, strips markup, drops control chars. Needed because `<title>` is an RCDATA element for BeautifulSoup's HTML parser, so a `<![CDATA[...]]>` section inside it arrives as literal text (elsewhere bs4 unwraps it into a `CData` node). Applied in `_parse_rss_feed_entry`, `_parse_youtube_feed_entry`, `_parse_dlp_item` (the last gate before indexing, so podcast items from content-dlp are covered too) and `url_resolver`.
  - `corridor.py` — metadata-only client for Corridor Digital (corridordigital.com), which has no RSS or public API. Reads the site's own *unauthenticated* JSON endpoints on `content-cache.corridordigital.com` (`channels/v16/season/{id}` = a show + every episode, `v3/metadata/{id}` = one video's title/HTML description/primary `seasonId`). Playback is Widevine DRM behind the user's login, so AI Tube never fetches video, audio, or subtitles, and never summarizes or transcribes these items: they're indexed as `type: "video"` with `external_id: "corridor_{mediaId}"` and the flyout shows a "Watch on Corridor" link (clicking it marks the item consumed). `backfill_missing_transcripts` excludes the `corridor_` prefix. Endpoints are undocumented and versioned, so parsers tolerate missing fields.
  - `content_classifier.py` — Jev (OpenRouter decisions model) categorization against the editable taxonomy in `aitube-category-config`. A category may carry **subcategories** (shown as "Topics" in the UI; today only AI and Software has them, 16 topics reverse-engineered from the AI Engineer World's Fair tracks plus the rest of the corpus). Jev answers the category and one subcategory question per category-with-subcategories in a **single** decisions call, and `classify_content()` returns `({"category", "subcategory"}, error)`, the partial doc to write; subcategory is None (clearing stale values) when the chosen category has none. A stored category with no `subcategories` key gets the bootstrap `SUBCATEGORIES` defaults, but an explicitly saved `[]` means none.
  - `demo_mode.py` — Demo Mode (a per-browser flip switch in the settings dropdown, persisted in localStorage) restricts the whole site to the AI and Software category (looked up by slug `ai_software`, so renaming the label doesn't break it). The frontend's `apiFetch` sends `X-Demo-Mode: 1`, and every content-bearing router takes the `DemoMode` dependency and ANDs in `demo_filters()`: content search (+ cache key), predictions, single-item GET (404 out of scope), quarantine stats, topic flow, embeddings, subscriptions (sources with no in-scope items are hidden), and the engagement report. Clustering runs span the whole corpus, so in Demo Mode a cluster is kept only if ≥50% of its members are in scope (`demo_cluster_sizes`); otherwise its label (e.g. a gaming cluster with two stray AI items) would leak. It's a presentation filter, not access control.
  - `metadata_extractor.py` — Claude Haiku for extracting podcast titles from transcripts and article metadata from scraped markdown
  - `elasticsearch.py` — Async ES client with index mappings and lifecycle
- **Config** (`backend/app/config.py`): Pydantic Settings reading from `.env`. Key: `content_dlp_url` defaults to localhost:7055, overridden to `host.docker.internal:7055` in Docker via `docker-compose.yml` environment block.

### Frontend (React 19 + TypeScript + Vite)

- `Timeline.tsx` — Main view with facet sidebar (type, status, interest, category, topic, source subscription) and content card grid. Category and Topic are tri-state (+ require / − mute); the Category facet is hidden in Demo Mode, where it would be a single bucket
- `demo/DemoMode.tsx` — Demo Mode context + localStorage persistence; `App.tsx` keys `<Routes>` on it so flipping the switch remounts (and refetches) the current page. `EmbeddingsView` colors by Topic by default in Demo Mode and drops the Category option
- `ContentView.tsx` — Flyout panel with YouTube/audio players, article reader, transcript viewer with auto-scroll
- `AddContent.tsx` — Ad-hoc content submission with URL preview and confirm flow (YouTube, podcast MP3, article)
- `api/client.ts` — Typed fetch wrapper for all API calls

Vite config sets `base: "/aitube/"` and proxies `/aitube/api` to backend in dev mode.

### Data Flow

1. Cron runs `poll_feeds.py` every 30 minutes
2. For each subscription: fetch feed XML → parse entries → filter by age → dedup against ES
3. Per new item: scrape content (RSS) or fetch captions (YouTube) → cleanup markdown → generate AI summary → index to ES
4. Post-poll: deduplicate content items by URL, backfill missing transcripts (up to 5/cycle), backfill missing summaries (up to 10/cycle), retry summaries that failed on a Hermes GPT-quota cooldown (up to 10/cycle, items from the last 48h whose `summary_error` matches "does not exist or you do not have access to it" or starts with an `HTTP <status>` line)
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

- `aitube-subscriptions` — Feed subscriptions (youtube_channel, podcast, rss, corridor_show). A `corridor_show` URL is the canonical `https://www.corridordigital.com/season/{id}`; pasting a Corridor *video* URL into Add Subscription subscribes to that video's primary show, while pasting a show URL into Add Content is rejected with a pointer to Subscriptions.
- `aitube-content-items` — All content with full-text search, facets on type/subscription_id/consumed/viewed/user_interest/category/subcategory. `summary_error`/`summary_failed_at` record the last summarization failure (cleared on success) so failed summaries can be found and retried.
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
