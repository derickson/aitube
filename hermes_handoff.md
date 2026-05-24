# Hermes Prep Checklist: serving AI Tube summarization via `hermes -z`

**Nothing to build on Hermes.** AI Tube will call the existing one-shot CLI you already ship. This
doc is just the ops prep so the call is reliable, clean, and cheap. (Verified against Hermes Agent
**v0.14.0** on the VPS.)

## The exact call AI Tube will make (verified working)

```bash
printf '%s' "$PROMPT" | ssh hermes 'hermes -p aitube -t "" -m gpt-5.4-mini -z "$(cat)"'
```
- `-p aitube` → run under a dedicated **neutral profile** (no "Rex" persona — see §3). **Must come
  first**, mirroring the profile's auto-generated wrapper `exec hermes -p aitube "$@"`.
  ⚠ The `HERMES_PROFILE` env var does **not** select a profile — it's silently ignored. Use `-p`.
- `-t ""` → no toolsets (pure completion, faster, no tool calls).
- `-m gpt-5.4-mini` → the model under evaluation.

Confirmed end-to-end on the VPS: this returns a clean 2–3 sentence summary + 5 bullets with no persona
preamble, in ~10s.

- The full summarization prompt is piped over **SSH stdin** and read back remotely with `"$(cat)"`,
  so arbitrary content (transcripts up to ~100k chars, quotes, backticks, `$`, etc.) needs no
  escaping. Verified: a multi-line prompt with shell metacharacters round-trips verbatim.
- `hermes -z` returns **only the final response text** on stdout — that text becomes the content's
  summary. No JSON, no envelope.
- The prompt already contains all instructions (2–3 sentence summary, then exactly 5 markdown bullets,
  `[M:SS]` timestamps when present). **Hermes should just answer the prompt as written** — no extra
  preamble, persona lines, or commentary.
- If the call fails, times out, or returns empty, AI Tube silently falls back to its paid Claude Haiku
  model. So a Hermes miss never breaks summaries — it just costs us tokens. Your job is to win the
  happy path often enough to cut the bill.

## Checklist

### 1. Trust the AI Tube backend's SSH key (esp. from Docker)
The host already works as `ssh hermes` (alias → `root@187.77.195.232`, key `~/.ssh/hermes_ed25519`).
**The Dockerized backend will connect explicitly as `root@187.77.195.232` with that same
`hermes_ed25519` key mounted read-only** (no `~/.ssh/config` in the container). So if `ssh hermes`
works from the host today, the container works once the key is mounted — that key is already
authorized. Nothing new required unless you mint a *dedicated* backend key, in which case add its
public key to `root`'s `~/.ssh/authorized_keys`.

Optional hardening: restrict that key to the one-shot command:
```
command="hermes -p aitube -t '' -m gpt-5.4-mini -z \"$(cat)\"",no-port-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA… aitube-backend
```
(With a forced command, the client-sent command is ignored and the prompt still arrives on stdin.)

### 2. Confirm the summarization model — GPT-5.4 mini
AI Tube will call with **`-m gpt-5.4-mini`** (Hermes' system default stays `gpt-5.5`). Verified:
`hermes -m gpt-5.4-mini -z "..."` returns clean, correctly-formatted output in ~11s. Prep for volume:
- Make sure `gpt-5.4-mini` stays selectable on the OpenAI Codex provider and isn't rate-capped.
- Add **fallback providers** so a throttled/down primary doesn't fail the call:
  `hermes fallback add` (picker mirrors `hermes model`). If Hermes returns nothing, AI Tube falls back
  to Haiku anyway — but a Hermes-side fallback keeps the cheaper path winning more often.

### 3. The dedicated `aitube` profile — ALREADY CREATED & VERIFIED
Your default profile answers in-character as **Rex** ("Good day, sir. Rex at your service…"). That
persona would land verbatim in our timeline as a content summary, and in the eval it would unfairly
tank gpt-5.4-mini's scores. **Verified:** the persona lives in the profile's `SOUL.md` and is **not**
removed by `--ignore-rules`, `--ignore-user-config`, or `-t ''`. The only clean fix is a separate
profile, which is now set up:

- `hermes profile create aitube --no-skills` → neutral `SOUL.md` (generic Nous default, no Rex).
- Its model is set to the **structured dict** `{default: gpt-5.4-mini, provider: openai-codex,
  base_url: …}` in `~/.hermes/profiles/aitube/config.yaml`.
- It reads the **shared** `~/.hermes/auth.json` for OpenAI Codex credentials (no separate login).
- Verified: `printf hello | hermes -p aitube -t "" -z "$(cat)"` → "Hello! How can I help today?"
  (neutral), and real summary prompts come back correctly formatted with no persona.

**Reproduce / rebuild** (e.g. if Hermes is reinstalled) — note two pitfalls I hit:
```bash
hermes profile create aitube --no-skills      # fresh, NOT --clone (cloning copies Rex's SOUL.md)
```
- ⚠ Do **not** rely on `hermes config set model gpt-5.4-mini` — it writes `model:` as a *bare string*,
  which drops the provider and breaks the profile ("No inference provider configured"). The model must
  be the structured dict above. Set it via `hermes -p aitube model` (interactive picker) or edit
  `~/.hermes/profiles/aitube/config.yaml` so `model:` is the 4-line dict.
- ⚠ Use the `-p aitube` flag, never `HERMES_PROFILE=` (ignored) — and `hermes -p aitube config set …`
  to target the profile's own config rather than the default profile's.

Because it's a separate profile, its sessions/memory/config stay fully isolated from your main "Rex"
profile — hundreds of summary calls won't clutter your primary Hermes.

### 4. Sanity-test the real call before AI Tube enables it
```bash
printf 'Summarize in one sentence: the sky is blue because of Rayleigh scattering.' \
  | ssh hermes 'hermes -p aitube -t "" -m gpt-5.4-mini -z "$(cat)"'
```
Expect a single clean sentence on stdout — **no "Rex"/persona preamble**, no banner, fast. If Rex
still shows up, the `aitube` profile's `SOUL.md` isn't neutral (re-check §3).

### 5. Get ready for the evaluation run
Before cutover, AI Tube runs an offline eval that summarizes ~30 items with **both** Haiku and
`gpt-5.4-mini`-via-Hermes and scores them head-to-head. That means a short burst of ~30 back-to-back
`-m gpt-5.4-mini` one-shot calls. Prep:
- Ensure the OpenAI Codex provider has quota/headroom for a burst (eval uses bounded concurrency ~3).
- **Keep output clean for a fair comparison** — see §3. Any persona text ("Rex is present…") bleeding
  into a summary would unfairly tank Hermes' scores in the eval, so run these under the isolated
  profile / `--ignore-rules` before the eval, not after.

## Performance expectations
~10s baseline overhead per call (SSH connect + agent startup) plus inference. AI Tube's timeout is
120s and calls run in the background polling pipeline, so latency is fine — but if your provider
throttles under load, expect AI Tube to fall back to Haiku for the throttled items.

## Future reuse
This is just `hermes -z` with a different prompt. Any other "run this prompt for us" handoff (metadata
extraction, cluster labeling, etc.) can use the identical call with a different prompt on stdin — no
new Hermes-side work.
