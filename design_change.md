# Design Change: Offload transcript summarization to Hermes (existing `hermes -z` CLI), fall back to Haiku

## Goal

Every summary today is produced by Claude Haiku inside `summarizer.summarize_content()`, which we pay
per-token for. The Hermes agent on the VPS can run the same prompt through its own model. We're
evaluating **GPT-5.4 mini (via Hermes)** as the replacement for Haiku on this task. We want the
summary step to try Hermes first and **fall back to Haiku** on any failure, with no change to callers.

`summarize_content()` keeps its signature and `str | None` return type, so all six call sites
(`feed_poller.py`, `add_content.py`) are untouched.

This change ships in two parts:
1. **Wire-up** — make summarization able to use GPT-5.4 mini through Hermes, with Haiku fallback.
2. **Evaluation harness** — compare Haiku vs GPT-5.4-mini-via-Hermes output quality (and latency/cost)
   on the same inputs *before* we trust the swap, and keep an optional shadow comparison running after.

## Research: what Hermes already exposes (no modification needed)

I introspected the live VPS (`ssh hermes`, Hermes Agent **v0.14.0**, Python 3.11). Findings that drive
this design:

- **`hermes -z PROMPT` / `--oneshot PROMPT`** — *"send a single prompt and print ONLY the final
  response text to stdout. No banner, no spinner, no tool previews, no session_id line. Intended for
  scripts / pipes."* This is exactly our use case. **Verified working:**
  `ssh hermes hermes -z "..."` returns just the answer text.
- **Default model:** `gpt-5.5` via the **OpenAI Codex** provider (`hermes status`). Overridable per
  call with `-m MODEL`.
- **GPT-5.4 mini is selectable and honored.** `hermes -m gpt-5.4-mini -z "..."` works and returns
  clean output in exactly our target format (2–3 sentence summary + bullets) in ~11s. A bogus
  `-m gpt-bogus-9000` returns empty output — which both confirms `-m` is honored *and* that our
  "empty stdout → fall back to Haiku" rule covers a mistyped/unavailable model.
- **`-z` needs the prompt as an argument** — it does *not* read the prompt from stdin (`-z -` treats
  `-` literally). Our prompts embed a transcript up to 100k chars with arbitrary characters, so passing
  that as an SSH argument means the remote shell re-parses it → quoting hell.
- **Robust transport (verified):** pipe the raw prompt over SSH stdin and let the *remote* shell read
  it back as the argument:
  ```bash
  printf '%s' "$PROMPT" | ssh hermes 'hermes -z "$(cat)"'
  ```
  The prompt travels as raw bytes through the stdin pipe and never touches either shell's parser. A
  multi-line prompt containing `$HOME`, backticks, quotes, backslashes, `& | ; < >` round-tripped
  verbatim. This is the transport we use.
- **Latency:** ~10s baseline overhead per call (SSH connect + agent cold start) on top of inference.
  Budget a generous timeout.
- There is also a `hermes proxy` (OpenAI-compatible HTTP port) and `hermes dashboard` (port 9119), but
  the proxy needs a long-running service + an SSH tunnel. The `-z` CLI matches your existing
  `ssh hermes hermes *` pattern and needs nothing running, so we use it.

**Bottom line: zero changes on Hermes.** We shell out to a CLI that already exists. See
`hermes_handoff.md` for the small ops/prep checklist (key trust from Docker, model/fallback config).

## Code changes (all on the AI Tube side)

### 1. `backend/app/config.py` — add Hermes settings

```python
    # Hermes summarization offload via `hermes -z` over SSH. Disabled by default.
    hermes_enabled: bool = False
    hermes_ssh_target: str = "hermes"      # ssh destination: ~/.ssh/config alias, or user@host
    hermes_ssh_opts: str = ""              # extra ssh args, e.g. "-i /run/secrets/hermes_key -p 22"
    hermes_model: str = "gpt-5.4-mini"     # `-m` model for summaries; "" = Hermes default (gpt-5.5)
    hermes_profile: str = "aitube"         # `-p` dedicated neutral profile (no "Rex" persona)
    hermes_timeout_seconds: int = 120

    # Summarizer evaluation (Haiku vs Hermes). Off in normal operation.
    summary_eval_index: str = "aitube-summary-evals"
    # Shadow eval: fraction of live summaries (0.0–1.0) for which we ALSO run the other
    # engine and store the pair for comparison. 0 = off (no extra calls/cost).
    summary_eval_shadow_rate: float = 0.0
```

`.env` to turn it on:

```
HERMES_ENABLED=true
HERMES_SSH_TARGET=hermes
```

**Docker (the chosen approach):** the container has no `~/.ssh/config`, so the `hermes` alias won't
resolve there. Rather than mounting the whole `~/.ssh`, we give the container an **explicit target +
mounted key**. The host (`make dev`) keeps using the `hermes` alias via env defaults; Docker overrides
the two env vars.

`.env` / Docker environment for the container:
```
HERMES_ENABLED=true
HERMES_SSH_TARGET=root@187.77.195.232
HERMES_SSH_OPTS=-i /run/secrets/hermes_key -o UserKnownHostsFile=/run/secrets/hermes_known_hosts
HERMES_MODEL=gpt-5.4-mini
HERMES_PROFILE=aitube   # AITube turns this into the `-p aitube` flag (not the HERMES_PROFILE env var)
```

Mount only the key + a pinned known_hosts entry in `docker-compose.yml` (read-only):
```yaml
  backend:
    environment:
      - HERMES_ENABLED=${HERMES_ENABLED:-false}
      - HERMES_SSH_TARGET=root@187.77.195.232
      - HERMES_SSH_OPTS=-i /run/secrets/hermes_key -o UserKnownHostsFile=/run/secrets/hermes_known_hosts
      - HERMES_MODEL=gpt-5.4-mini
      - HERMES_PROFILE=aitube
    volumes:
      - ~/.ssh/hermes_ed25519:/run/secrets/hermes_key:ro
      - ~/.ssh/hermes_known_hosts:/run/secrets/hermes_known_hosts:ro
```

Notes for the Docker path:
- The mounted key must be the **private** key matching an `authorized_keys` entry on the VPS (the
  existing `~/.ssh/hermes_ed25519` already works as `root` — reuse it, or mint a dedicated backend key
  and authorize it; see `hermes_handoff.md`).
- Pin the host key into `hermes_known_hosts` (`ssh-keyscan -H 187.77.195.232 > ~/.ssh/hermes_known_hosts`)
  so we keep `StrictHostKeyChecking` semantics inside the container instead of blindly accepting.
  Drop the `-o StrictHostKeyChecking=accept-new` reliance for Docker; the pinned file is the trust root.
- Key file perms: SSH ignores a key that's group/world-readable. A bind-mounted `:ro` file keeps the
  host's perms; ensure the host file is `chmod 600`. If perms still trip SSH in-container, add
  `-o IdentitiesOnly=yes` (already implied) and confirm the mount isn't `0644`.

### 2. New file `backend/app/services/hermes_client.py`

Small and summary-specific (no generic RPC envelope — we're calling a plain CLI):

```python
"""Run a prompt through the Hermes agent's one-shot CLI over SSH."""

import asyncio
import logging
import shlex

import elasticapm

from backend.app.config import settings

logger = logging.getLogger(__name__)


async def run_oneshot(prompt: str, *, model: str | None = None) -> str | None:
    """Send `prompt` to `hermes -z` over SSH; return the response text, or None on any failure.

    The prompt is piped over SSH stdin and read remotely via "$(cat)", so it needs no shell
    escaping regardless of size or content.
    """
    if not settings.hermes_enabled or not settings.hermes_ssh_target:
        return None

    use_model = model or settings.hermes_model
    # Prompt comes from stdin via $(cat) and needs no escaping. We interpolate only
    # the (trusted, simple) profile/model values, and shell-quote them.
    #   -p <profile>  -> dedicated neutral profile (strips the "Rex" persona). MUST come
    #                    first, like the auto-generated wrapper `exec hermes -p aitube "$@"`.
    #   -t ''         -> no toolsets (pure completion, faster, no tool calls)
    #   -m <model>    -> gpt-5.4-mini
    parts = ["hermes"]
    if settings.hermes_profile:
        parts.append(f"-p {shlex.quote(settings.hermes_profile)}")
    parts.append("-t ''")
    if use_model:
        parts.append(f"-m {shlex.quote(use_model)}")
    parts.append('-z "$(cat)"')
    remote_cmd = " ".join(parts)
    ssh_cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        *shlex.split(settings.hermes_ssh_opts),
        settings.hermes_ssh_target,
        remote_cmd,
    ]

    with elasticapm.capture_span("Hermes oneshot", span_type="external", span_subtype="ssh",
                                 labels={"model": use_model or "default"}):
        try:
            proc = await asyncio.create_subprocess_exec(
                *ssh_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode()),
                timeout=settings.hermes_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("Hermes timed out after %ds", settings.hermes_timeout_seconds)
            return None
        except OSError as e:
            logger.warning("Hermes ssh spawn failed: %s", e)
            return None

    if proc.returncode != 0:
        logger.warning("Hermes ssh exited %s: %s",
                       proc.returncode, stderr.decode(errors="replace")[:300])
        return None

    text = stdout.decode(errors="replace").strip()
    return text or None
```

### 3. `backend/app/services/summarizer.py` — try Hermes first, then Haiku

Refactor so the prompt is built once and post-processing is shared by both paths:

- Extract the current inline f-string into `_build_summary_prompt(...) -> str`.
- Extract the heading-prefix stripping (the `## Summary` / `**Summary:**` cleanup) into
  `_postprocess_summary(text: str) -> str`.
- In `summarize_content`, after building `source_text` and the prompt:

```python
    prompt = _build_summary_prompt(
        title, type_label, source_text, description, author, has_timestamps, content_type
    )

    # 1) Try Hermes (no Anthropic spend). Any miss falls through to Haiku.
    if settings.hermes_enabled:
        from backend.app.services.hermes_client import run_oneshot
        hermes_text = await run_oneshot(prompt)
        if hermes_text:
            logger.info("Generated summary via Hermes for: %s (%d chars)",
                        title[:50], len(hermes_text))
            return _postprocess_summary(hermes_text)
        logger.info("Hermes summary unavailable for %s, falling back to Haiku", title[:50])

    # 2) Fall back to Haiku — the existing retry loop, unchanged.
    ...
```

The existing Haiku loop stays exactly as-is, so with `hermes_enabled=false` (the default) behavior is
identical to today.

## Behavior / failure matrix

| Condition                                   | Result                               |
|---------------------------------------------|--------------------------------------|
| `hermes_enabled=false`                      | Straight to Haiku (today's path)     |
| SSH connect/timeout/non-zero exit           | Log warning → Haiku                  |
| Hermes prints empty output                  | Log warning → Haiku                  |
| Hermes prints text                          | Use it (post-processed); no Haiku spend |

Because `-z` prints only the final text, the same `_postprocess_summary` we apply to Haiku output
handles any stray heading prefix from Hermes too.

## Risks / mitigations

- **Persona bleed (handled by a dedicated profile):** Hermes' default profile has a configured
  persona — a stray prompt reliably replied *"Good day, sir. Rex at your service…"*. **Verified:**
  this persona lives in the profile's `SOUL.md`, and is **not** removed by `--ignore-rules`,
  `--ignore-user-config`, or an empty toolset. The clean fix is a dedicated **neutral profile**
  selected per-call with the **`-p aitube`** flag (NOT `HERMES_PROFILE` — that env var is silently
  ignored). The `aitube` profile is created once on the VPS (see `hermes_handoff.md`) with a neutral
  `SOUL.md`, its own `model: gpt-5.4-mini` config, and it reads the shared `~/.hermes/auth.json` Codex
  credentials — fully isolated from the user's main "Rex" profile. **End-to-end verified:**
  `hermes -p aitube -t '' -m gpt-5.4-mini -z "$(cat)"` returns clean, correctly-formatted summaries
  with no persona. We pass `-t ''` to disable tools (verified accepted) for pure, fast completions.
- **Latency:** ~10s+ per call; summarization runs in the background pipeline, so acceptable. Timeout
  defaults to 120s.
- **Throughput:** polling can summarize many items per cycle; if Hermes' provider rate-limits, calls
  fail and we fall back to Haiku — no breakage, just spend. Configure Hermes fallbacks (handoff doc).

## Evaluation: Haiku vs GPT-5.4-mini-via-Hermes

We don't flip the swap on faith — we measure it. Two mechanisms, sharing one comparison/scoring core.

### Shared core: `backend/app/services/summary_eval.py`

```python
async def compare_engines(item: dict) -> dict:
    """Run BOTH engines on the same source, score them, return one eval record."""
```

For a given content item (with transcript/source already available) it:

1. Builds the **one prompt** via `summarizer._build_summary_prompt(...)` — both engines get identical
   instructions, so we're comparing models, not prompts.
2. Generates **Haiku** output (existing Haiku path) and **Hermes/gpt-5.4-mini** output
   (`hermes_client.run_oneshot(prompt, model="gpt-5.4-mini")`), timing each.
3. Runs deterministic **format checks** on each (cheap, no LLM): has a 2–3 sentence lead; exactly 5
   `- ` bullets; `[M:SS]`/`[H:MM:SS]` timestamps present iff `has_timestamps`; length within bounds.
4. Runs an **LLM-as-judge** (Claude Sonnet, a neutral third model) given the source + both summaries
   **blind and A/B-randomized** (to kill position bias), returning structured JSON:
   ```json
   {
     "winner": "A|B|tie",
     "scores": {
       "A": {"faithfulness": 1-5, "specificity": 1-5, "format": 1-5, "conciseness": 1-5},
       "B": {"faithfulness": 1-5, "specificity": 1-5, "format": 1-5, "conciseness": 1-5}
     },
     "rationale": "one paragraph"
   }
   ```
   - **faithfulness** — grounded in the transcript, no hallucinated claims.
   - **specificity** — cuts through clickbait to the real thesis (the stated product goal).
   - **format** — adheres to the 2–3 sentence + 5-bullet (+timestamps) structure.
   - **conciseness** — no padding.

   After un-blinding A/B, the record maps winner → `haiku`/`hermes`.

The record (one per item) is written to a new ES index **`aitube-summary-evals`**:

```json
{
  "item_id", "external_id", "title", "type", "evaluated_at",
  "haiku":  {"summary", "latency_ms", "format_violations": [...]},
  "hermes": {"summary", "model": "gpt-5.4-mini", "latency_ms", "format_violations": [...]},
  "judge":  {"engine": "claude-sonnet", "winner": "hermes", "scores": {...}, "rationale": "..."}
}
```

### Mechanism 1 — offline batch eval (the decision gate)

New script `backend/scripts/eval_summarizers.py`:

```bash
uv run python -m backend.scripts.eval_summarizers \
    --n 30 --types video,podcast_episode,article --judge claude-sonnet-4-6
```

- Samples N recent items from ES that already have a transcript/source (no re-scraping/re-transcribing).
- Calls `compare_engines` for each (bounded concurrency, e.g. 3, to be kind to Hermes' provider).
- Writes every record to `aitube-summary-evals` **and** a local `evals/<timestamp>.jsonl`.
- Prints an **aggregate report**: Hermes win-rate, mean score per dimension per engine, format-violation
  counts, latency p50/p95 per engine, and an estimated Haiku token cost avoided.

This is the number we decide on: if GPT-5.4 mini wins/ties on quality with no format regressions and
acceptable latency, we make it primary.

### Mechanism 2 — optional production shadow eval

Controlled by `summary_eval_shadow_rate` (default `0.0` = off). When the production path produces a
summary, with probability `shadow_rate` it *also* runs the other engine and stores a `compare_engines`
record — without affecting which summary the user actually gets. This gives ongoing drift monitoring
after cutover, at a controlled extra cost. Keep at `0.0` until after the offline gate; then a small
rate (e.g. `0.05`) for a while.

### Index lifecycle

Add `aitube-summary-evals` to `elasticsearch.py` mappings/lifecycle alongside the existing indices
(keyword fields for `type`/`judge.winner`/`hermes.model`, `date` for `evaluated_at`, `text` for the
summaries). Update the **Elasticsearch Indices** list in `CLAUDE.md`.

## Testing plan

1. **Unit (mocked):** monkeypatch `hermes_client.run_oneshot` → return text (assert used) / return
   `None` (assert Haiku path). For eval, mock both engines + judge and assert A/B un-blinding maps the
   winner to the right engine.
2. **Live smoke:** `HERMES_ENABLED=true`, run `uv run python -m backend.scripts.poll_feeds` on one
   subscription; confirm log shows "via Hermes" and the summary lands in ES.
3. **Kill-switch:** bad `HERMES_SSH_TARGET` → confirm summaries still generate via Haiku with one
   warning per item.

## Rollout

1. Ship with `HERMES_ENABLED=false` (no-op, zero risk).
2. Work through the `hermes_handoff.md` prep checklist on the VPS (key trust from Docker, model +
   fallback config, optional isolated profile).
3. **Eval gate:** run `eval_summarizers.py --n 30` and review the aggregate report. Only proceed if
   GPT-5.4 mini wins/ties on quality with no format regressions and acceptable latency.
4. Flip `HERMES_ENABLED=true`, `make docker-redeploy`, watch logs/APM for the Hermes-vs-Haiku split
   and fallback rate.
5. Optionally set `SUMMARY_EVAL_SHADOW_RATE=0.05` for ongoing drift monitoring; revisit
   `aitube-summary-evals` periodically.
