# Extraction evaluation

The extraction pipeline (prompt -> real LLM -> tolerant parse -> strict validation -> grounding -> **deterministic** deadline
resolution -> threshold decision) was run against hand-labelled, fully synthetic emails with real models. Expected values are
written by hand, never produced by the code under test. Every check is graded independently and failures are reported, not tuned away.

    source scripts/dev-env.sh
    apps/api/.venv/bin/python -u scripts/eval_extraction.py --dataset dev|heldout --provider openai_compat|gemini --model <m> --out docs/eval/<file>.json

| Set | Items | Purpose |
|---|---|---|
| `dev` (`apps/api/tests/data/eval_emails.jsonl`) | 35 | what the prompt and pipeline were developed against |
| `heldout` (`.../eval_emails_heldout.jsonl`) | 18 | written *before* the prompt was changed, to see whether a change generalises |

The costly error is a **wrongly auto-created** obligation (a false positive that is confident, grounded and above the
threshold), so it is reported on its own line. What matters most is the pipeline's safety properties, not label accuracy:
detection, the create/review/ignore decision, deadlines, ambiguity flags, owner, grounding and contract adherence.

## Results (Qwen2.5-7B, run locally through Ollama - a deliberately small model)

| | prompt v1 | prompt v2 | prompt v3 |
|---|---|---|---|
| **dev** fully correct | 31 / 35 | 31 / 35 | 31 / 35 |
| dev: deadline (`due`) | 25 / 27 | 27 / 27 | 27 / 27 |
| dev: owner | 2 / 3 | 3 / 3 | 3 / 3 |
| dev: false positives / negatives | 0 / 0 | **1** / 0 | 0 / 0 |
| dev: type label | 24 / 26 | 23 / 26 | 22 / 26 |
| **held-out** fully correct | 12 / 18 | 15 / 18 | 17 / 18 |
| held-out: deadline (`due`) | 9 / 12 | 12 / 12 | 12 / 12 |
| held-out: false positives / negatives | 0 / **1** | **1** / 0 | 0 / 0 |
| wrongly auto-created | none | promo mail (dev + held-out) | none |

Common to every run: `contract` 35/35 (no off-schema output reaches the database), `grounded` all, prompt-injection text
ignored, 0 repair retries needed, decisions inside the accepted set 100% on v3.

**What the evaluation found (and what was done):**

1. *Event times were not read as deadlines* (calendar invites, "can we move it to Friday at 2pm"): both a local and a hosted
   model returned no deadline phrase for these. The prompt now says that the time of an event is the `deadline_text`.
2. *A resolver defect, found by the held-out set:* for "10:00 AM - 11:00 AM" the resolver used the **last** time, so a meeting
   would have reminded at its *end*. Multiple times had no test at all. Now a range resolves to its start, unrelated times to the
   earliest (flagged), with regression tests. (An early reminder is harmless; a late one is not.)
3. *The prompt change caused a regression* (v2): promotional mail with a deadline was auto-created. The fix (v3) balances the
   prompt, but note that two promo items were used to diagnose it, so those two are **no longer a clean test**; the remaining
   held-out numbers are the honest signal. The class of error is also handled structurally: a confident model can still be
   wrong, which is why nothing below the auto-create threshold, without a verifiable quote, or with an ambiguous deadline is
   ever created without a human.
4. Owner mistakes on "I will send X" (received vs sent) improved with explicit direction guidance, but a 7B model still
   confuses them at times; type labels (`RENEWAL` for a password expiry) remain the weakest field and drive nothing critical.

## Hosted model (Gemini) - partial, with the reason

The free tier of the supplied Gemini key allows ~20 requests per model per day, so a 53-item run needs several models and days.
Measured on prompt **v1**, `gemini-3-flash-preview`: 10 of 14 attempted items answered, **10 / 10 fully correct** (0 false
positives/negatives, all deadlines exact); the other four hit a transient `503`/`429` and then the daily quota, which the
pipeline reported as `LLM_QUOTA_EXHAUSTED` and stopped on (`docs/eval/extraction-gemini-3-flash-preview.json`).
`gemini-flash-lite-latest` (a slow, frequently overloaded preview) answered 17 of 24 with the same two event-time misses the
local model showed. These runs pre-date prompt v2/v3, so they are not comparable to the table above. **Not evaluated:** any paid
hosted model.

## Limits of this evaluation

53 synthetic English emails, single-message, one labeller (the author). It shows that the pipeline is safe and behaves as
designed with real models; it is **not** a claim about accuracy on real mail. Small local models are far weaker than the
hosted models the default configuration targets - which is the point of validating every reply instead of trusting it.

Raw per-item results: `*.json` in this directory.
