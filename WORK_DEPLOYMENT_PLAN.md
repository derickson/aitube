# Work Deployment Plan

Branch: `feature/work-deployment` (worktree `.claude/worktrees/work-deployment`, based on `origin/main`).

## Goal

Run a second, **passive** AITube deployment ("work") as a public demo for coworkers,
hosted as a **single Docker container** in an ArgoCD CI/CD chain.

- **Prod (smaug)** stays the only active instance: cron polling, content-dlp, Hermes,
  summaries, classification. It writes to the prod Elasticsearch cluster as today and
  additionally **mirrors** selected content into the work Elasticsearch cluster.
- **Work** has only the work cluster's credentials. It never ingests, polls or
  summarizes. It only serves what prod mirrored into its cluster.
- Which pages, menu items and in-page capabilities exist is decided per deployment by a
  **feature registry** (each feature is marked prod, work, or both).

```
            cron (poll.sh)
                 │
   ┌─────────── smaug ────────────┐            ┌──── ArgoCD / k8s ────┐
   │ backend + frontend (compose) │            │  aitube (1 container) │
   │ DEPLOYMENT=prod              │            │  DEPLOYMENT=work      │
   └──────┬──────────────┬────────┘            └──────────┬───────────┘
          │ read/write   │ mirror (flagged subs only)     │ read (+ own state)
          ▼              ▼                                ▼
     prod ES cluster   work ES cluster  ◄─────────────────┘
```

## Decisions (defaults chosen; override any of them)

| # | Question | Default |
|---|----------|---------|
| 1 | Mirror personal state (consumed/viewed/interest/quarantine)? | **No.** A denylist strips it; work viewers can't write it either. |
| 2 | Mirror ad-hoc items (no subscription)? | **Not in v1.** Only items of flagged subscriptions. |
| 3 | Unflagging a subscription | **Purge** its items and its subscription doc from the work cluster. |
| 4 | Topic Flow / Embeddings on work | **Off** on work (cluster runs span the whole prod corpus and would leak labels). |
| 5 | Work ingress auth | Assume **open**, so every write endpoint is removed in work mode, not just hidden. |
| 6 | Demo/Zen toggles on work | **Off** (the work corpus is already curated by the mirror flag). |

## Part 1 — Feature registry

`backend/app/features.py` is the single source of truth.

```python
Feature(id, label, kind, deployments, route=None)
# kind: "nav" | "settings" | "toggle" | "capability" | "core"
```

- `DEPLOYMENT=prod|work` (default `prod`) picks the set. `FEATURES_ENABLE` /
  `FEATURES_DISABLE` (comma lists) override individual features at deploy time without
  rebuilding the image.
- `GET /api/config/` → `{deployment, features: [{id, label, kind, route}]}` (enabled only).
- **Backend enforcement:** every API route carries a `require_feature(id)` dependency
  (router-level in `main.py`, or per endpoint for routers that mix reads and writes).
  A disabled feature returns **404**. A unit test walks `app.routes` and fails if any
  route lacks a feature, so new endpoints must be classified.
- **Frontend:** `FeaturesProvider` fetches `/api/config/` at boot; the top nav, the gear
  menu and `<Routes>` are all built from it. A disabled route falls to NotFound; the gear
  is hidden when it has no items. In-page capabilities use `useFeature(id)`.
- Local preview of the work variant: `DEPLOYMENT=work make dev`.

### Feature matrix

| Feature id | Kind | Prod | Work | Covers |
|---|---|:-:|:-:|---|
| `core` | core | ✓ | ✓ | content search, item GET, subscription list, category settings GET, playback GET + batch progress, `/api/config/` |
| `timeline` | nav | ✓ | ✓ | `/` |
| `search` | nav | ✓ | ✓ | `/search/` |
| `topic_flow` | nav | ✓ | ✗ | `/topic-flow/`, topic-flow API |
| `embeddings` | nav | ✓ | ✗ | `/embeddings/`, embeddings API |
| `prediction` | nav | ✓ | ✗ | `/prediction/`, predictions API |
| `demo_mode` | toggle | ✓ | ✗ | gear switch |
| `zen_mode` | toggle | ✓ | ✗ | gear switch |
| `content` | settings | ✓ | ✗ | `/subscriptions/`, `/add-content/`, subscription writes, add-content preview/confirm, transcribe, item delete, CSV export |
| `work_mirror` | capability | ✓ | ✗ | "Mirror to work" toggle (also needs `WORK_ELASTICSEARCH_URL`) |
| `quarantine` | settings | ✓ | ✗ | `/quarantine/`, quarantine stats |
| `watch_time` | settings | ✓ | ✗ | `/watch-time/`, consumption stats |
| `engagement` | settings | ✓ | ✗ | `/engagement/`, engagement report |
| `search_explain` | settings | ✓ | ✓ | `/search-explain/` |
| `category_settings` | settings | ✓ | ✗ | `/category-settings/`, taxonomy writes, recategorize |
| `detox` | settings | ✓ | ✗ | `/detox/`, detox API, header badge |
| `user_state` | capability | ✓ | ✗ | consumed/viewed/interest writes, playback PUT, interest buttons |
| `chat` | capability | ✓ | ✗ | flyout Chat tab, chat API |
| `external_api` | capability | ✓ | ✗ | aitube-sync / Hermes callers: watchlist, submit_video, add-content ingest, transcript + quarantine POST, consumption report, delete-by-external-id |
| `polling` | capability | ✓ | ✗ | polling trigger API |

## Part 2 — Work mirror (prod side)

- **Config:** `WORK_ELASTICSEARCH_URL`, `WORK_ELASTICSEARCH_API_KEY` (unset = mirroring is a
  no-op). Prod holds both credential sets; work holds only its own, under the normal
  `ELASTICSEARCH_*` names, so no read path needs to know which cluster it's on.
- **Subscription flag:** `mirror_to_work: bool` on the model, the update payload and the
  mapping; a toggle per row on the Content page (feature `work_mirror`).
- **`services/work_mirror.py`** (second lazy `AsyncElasticsearch` client):
  - `ensure_work_indices()` creates subscriptions, the content index, category config and
    playback state on the work cluster from the same `INDEX_MAPPINGS`.
  - `sync_to_work(days=14)` final phase of `poll_all_active`: for flagged subscriptions,
    bulk-upserts items discovered in the last N days (covers late summary / category /
    engagement updates), deletes work docs for those subscriptions that no longer exist on
    prod in that window (dedup), pushes flagged subscription docs and the category config.
  - `mirror_subscription(sub_id)` full backfill when a subscription is flagged.
  - `unmirror_subscription(sub_id)` purges items + subscription doc from work.
  - Field **denylist**: `consumed`, `viewed`, `user_interest`, `quarantine*`,
    `content_dlp_cache_id`, `summary_error*`, `transcript_attempts*`.
  - Upserts are `update` + `doc_as_upsert`, so any work-side fields survive.
- **Script:** `scripts/sync_to_work.py [--full] [--sub ID] [--days N] [--dry-run]`.
- Mirroring is best-effort: a work-cluster failure is logged and never fails a poll.

Caveat: if prod uses the v2 content index (`semantic_text`), the work cluster needs the
same inference endpoint (`SEMANTIC_INFERENCE_ID`), and mirroring triggers inference there.

## Part 3 — Single container

- Root `Dockerfile`, multi-stage: node builds the frontend, `python:3.12-slim` runs it.
  FastAPI serves `frontend/dist` at `/aitube/` with SPA fallback, and a path rewrite maps
  `/aitube/api/*` → `/api/*`. That replaces the nginx container and the reverse proxy's
  prefix strip. One process (uvicorn), non-root, port 8000, probes on `/health/`.
- `SERVE_FRONTEND_DIR` setting turns static serving on (only set in this image), so
  `make dev` and the smaug compose deployment are unchanged.
- `.dockerignore` (there was none; keeps `.env`, `node_modules`, `.venv` out of the image).
- `docker-compose.work.yml` + `make work-build` / `make work-run` to test locally against
  the work cluster with `.env.work`.
- Base path stays `/aitube/` for now.

### What stays outside the work container

| Dependency | Prod | Work |
|---|---|---|
| content-dlp on host :7055 | needed | not used |
| Hermes SSH + mounted keys | needed | not used |
| cron `poll.sh` | needed | not used |
| nginx frontend container | used | merged into the app |
| reverse-proxy basic auth | only auth | gone → features remove writes |
| `aitube-engagement` ingest pipeline | used | not needed (scores mirrored) |
| EIS inference endpoint | used | needed only with v2 index |
| Anthropic / OpenRouter / Jina keys | used | not needed |

## Part 4 — CI/CD (pending ArgoCD skills from Dave)

GitHub Actions image build + push, and manifests (Deployment, Service, Ingress, Secret
for `ELASTICSEARCH_URL` / `ELASTICSEARCH_API_KEY`, `DEPLOYMENT=work`) in whatever shape the
ArgoCD chain expects.

## Order of work / status

- [x] 1. Feature registry (backend gating, `/api/config/`, coverage test)
- [x] 2. Frontend driven by features (nav, gear, routes, capabilities)
- [x] 3. Work mirror (config, service, subscription flag + toggle, poll phase, script, tests)
- [x] 4. Single-container image, `.dockerignore`, static serving, make targets
      (`make work-run` + `.env.work.example` instead of a compose file)
- [x] 5. CLAUDE.md docs
- [ ] 6. CI + ArgoCD manifests (after skills are provided)

### Verified (2026-11-20)

- Unit tests: feature sets, route coverage, work-mode 404s, frontend serving.
- Mirror integration tests against two real ES 9.2 nodes (full/windowed sync, dedup
  deletes, unflag purge, work-side fields survive, dry run, unconfigured no-op).
- End to end with the built image: fake prod node → `sync_to_work --full` → work node →
  container in `DEPLOYMENT=work`. Only flagged items served, no personal fields; nav is
  Timeline + Search, gear has only Search Explain, no watched/interest/Chat controls,
  prod-only URLs show NotFound, every write endpoint 404s. Same image in
  `DEPLOYMENT=prod`: full nav, Work toggle backfills (2 items) and purges from the UI.

### Before going live

- Prod `.env` on smaug: add `WORK_ELASTICSEARCH_URL` / `WORK_ELASTICSEARCH_API_KEY`
  (an API key scoped to write the `aitube-*` indices on the work cluster).
- The work deployment's own key can be read-only on `aitube-*` (it writes nothing
  with the current feature set). Its startup `ensure_indices` just logs a warning if
  it can't create indices, which is fine once prod's mirror has created them.
- If prod runs the v2 content index, create the same inference endpoint on the work
  cluster first, and set `CONTENT_ITEMS_INDEX` identically on both.
- Topic Flow / Embeddings / Prediction stay off on work until cluster runs are mirrored
  in a demo-safe way.
