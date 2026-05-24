# How to revert transcript summaries back to Claude Haiku

You enabled **Hermes (GPT-5.4 mini)** for transcript summaries on 2026-05-24 as a trial.
Summaries currently run on Hermes, falling back to Haiku only when Hermes fails. This note
is how to switch back to Haiku-only.

> Context for future-you: in the pre-trial eval, Haiku won the head-to-head **73%** of the
> time (mainly on *specificity*) and was **~4–5× faster**. Hermes was better on bullet/format
> adherence. So reverting to Haiku is the "known-good" choice unless the trial changed your mind.

---

## Option A — Quick revert (recommended): just flip the flag

The Hermes code stays in place; you only turn the feature off. Haiku takes over immediately.

1. Edit `.env` (it's gitignored — this is a local change, not a commit):
   ```
   HERMES_ENABLED=false
   ```
   (Change the existing `HERMES_ENABLED=true` line near the bottom.)

2. Redeploy prod:
   ```bash
   cd /home/dave/dev/aitube
   docker compose stop && docker compose build && docker compose up -d
   ```

3. Verify it's back on Haiku — new summaries should log `Generated summary via Haiku`
   and you should see **no** `via Hermes` lines:
   ```bash
   docker compose logs backend --tail 50 | grep -iE "via Hermes|via Haiku"
   ```

That's it. To turn Hermes back on later, set `HERMES_ENABLED=true` and redeploy again.

---

## Option B — Remove the feature from the codebase entirely

Only if you've decided you never want it. This reverts the whole merge.

```bash
cd /home/dave/dev/aitube
git revert -m 1 464f3c1     # the Hermes merge commit; -m 1 keeps main's mainline
# resolve any trivial conflicts, then:
docker compose stop && docker compose build && docker compose up -d
```
Then optionally delete the trial branch: `git branch -d enable-hermes-summarization`.

You usually do **not** need Option B — Option A (flag off) already routes 100% of summaries
through Haiku. Keeping the code dormant costs nothing.

---

## Optional cleanup on the Hermes VPS (not required)

The dedicated `aitube` profile on the VPS is harmless if left in place. If you want it gone:
```bash
ssh hermes 'hermes profile delete aitube'
```
Your default "Rex" profile (gpt-5.5) is untouched and was restored to its original config
during setup.

---

## Reference — what the trial changed

- **Flag:** `HERMES_ENABLED` in `.env` (the on/off switch). Also `HERMES_MODEL=gpt-5.4-mini`,
  `HERMES_PROFILE=aitube` — supplied with defaults by `docker-compose.yml`.
- **Code:** `backend/app/services/hermes_client.py` (SSH oneshot) and the Hermes-first branch
  in `backend/app/services/summarizer.py`. Both no-op when `HERMES_ENABLED=false`.
- **Commit:** `464f3c1` on `main`. Trial branch: `enable-hermes-summarization`.
- **Eval harness:** `backend/scripts/eval_summarizers.py` — re-run a comparison anytime with
  `HERMES_ENABLED=true uv run python -m backend.scripts.eval_summarizers --n 30`.
- **Full design / setup notes:** `design_change.md`, `hermes_handoff.md`.
