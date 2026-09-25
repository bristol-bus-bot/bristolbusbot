# Repeatable writer comparison

From `bot/`, after installing the normal dependencies:

```sh
npm run audition -- tests/fixtures/audition/cases.json /path/to/local-report --responses tests/fixtures/audition/baseline.json --baseline tests/fixtures/audition/baseline.json
```

Open `/path/to/local-report/index.html`. This builds the current branch, replays
recorded responses through its actual writer, and places baseline and candidate
posts, evidence, draft checks and verifier verdicts side by side. It makes **zero
model calls**. The report is a standalone responsive HTML file; it does not fetch
scripts, fonts or data from the internet.

The original positional writer/verifier-array interface is replaced by a single
recorded run. That interface used an obsolete verifier signature and skipped the
current direction checks and repair loop. The deliberately bounded case limit is
now 200, supporting the later 60/200-case sequence tests; the fixed initial set is
30 cases. The case count does not grant a model-call budget.

## Preparing or testing another branch

```sh
npm run audition -- tests/fixtures/audition/cases.json /path/to/candidate --baseline tests/fixtures/audition/baseline.json
```

Without responses or `--live`, this prepares the first request and stops with
`awaiting_response`. Subsequent prompts depend on the accepted publication history,
so preparing all requests independently would give the wrong repetition/subject
behaviour. `checkpoint.json` and `run.json` retain the exact request.

Recorded responses are bound to the fixture digest and exact prompt/schema/
temperature/thinking configuration. When a branch changes those prompts, old
answers are refused instead of being presented as new candidate output. Supply a
matching candidate recording or obtain a new budget for model calls.

## Explicitly authorised live runs

The operator supplies `AI_API_KEY` through the environment; never put it in a
command argument, fixture, report or ledger. Use the approved model name:

```sh
npm run audition -- tests/fixtures/audition/cases.json /path/to/candidate --baseline tests/fixtures/audition/baseline.json --live --model APPROVED_MODEL --max-calls 120 --ledger /private/path/approved-run-ledger.json
```

`--max-calls` is an explicit budget, not a default or a currency quote. Every
attempt, including errors and transient retries, is written to the durable ledger
**before** network access. A lock prevents concurrent use of one ledger. Resuming
with the same ledger replays completed requests without paying again and refuses
an incomplete/uncertain prior attempt. It refuses changed model, fixture or budget
parameters. Preserve this ledger for the whole approved experiment; using a new
ledger is not permission to reset the budget. An interrupted process may leave its
lock; establish that it is no longer running and inspect uncertain attempts before
manually removing the lock. Do not automatically retry them.

The only provider destination is Google's Gemini `generateContent` endpoint. The
runner never constructs a social publisher or reads/writes live databases. No
production environment file is loaded by this command. Private credentials and
production extraction are operator responsibilities, outside the public harness.

## What is actually exercised

- The production single-writer method, subject choice, history, mechanical checks,
  verifier prompt, one targeted repair, and its bounded transient-retry handling.
- The clock is frozen to each observation for prompt rendering, while elapsed
  request deadlines still run. Traffic freshness uses that observation clock.
- Fallback uses the existing observation/reserve functions. The frozen event gate
  stands in for the final live collector recheck; no passing result proves a bus
  was still eligible at actual publication time.
- History, pending publications and editorial accounting are isolated in memory.
  Do not call the core replay concurrently in one process because it temporarily
  adjusts the clock. The CLI runs one sequence.

The report counts new calls, cached replays and ledger expenditure separately and
records token usage when returned by the provider. Mechanical PASS/verifier PASS
is evidence of these checks only, not human editorial approval. Fixture overlays
and provenance stay next to the outputs. A fallback has no verifier PASS.

Run `npm test` for the bot suite. The audition tests cover current direction
checks, targeted repair, verifier failure, fallback, history, a provider retry,
prompt-bound recordings, HTML escaping, budget exhaustion/resumption, uncertain
attempts and transport-storage failure. They mock provider responses and spend
nothing.
