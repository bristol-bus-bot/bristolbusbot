# Notes for AI assistants working in this repository

Tom maintains this project alone and reads the folder by eye. Files that
aren't part of the project make it hard to find the ones that are. Follow
these rules whenever you create a file.

## Where your files go

**Working files go in `.scratch/`**, in a folder named for the task:

```
.scratch/2026-10-28-route-number-split/
```

That covers notes, mockups, one-off scripts, downloaded data, previews,
exports, logs and anything else you make while working that isn't part of the
change you're committing. `.scratch/` is ignored by Git.

- **Never** add files to the repository root, or to a component folder
  (`site/`, `bot/`, `pipeline/`, …), unless they belong in the commit you're
  making.
- **Test temp files** go in the system temp folder. Don't pass `--basetemp`
  pointing into the repo. `deploy/push.py` already uses the system temp folder.
- **One-off scripts that touch production** (deleting posts, recomputing
  data) also go in `.scratch/<task>/`. Name them in your handover so Tom can
  find them.
- **Anything Tom should open**: give him the full path in your reply. Don't
  leave him to search for it.

## Cleaning up

- Delete your `.scratch/<task>/` folder once the work is merged, though confirm first with Tom
  if the work in there merits keeping.
- Don't delete other folders in `.scratch/` without asking. They may belong
  to another task.
- Never delete databases, backups or `.env` files without checking with Tom
  first, here or on the Pi.

## Lint checks before a pull request

Run `ruff check` from the repository root and `npm run lint` from `bot/`.
CI runs these in its own lint job. The existing custom checks still run
as part of the test and security jobs.

## Audit page wording

The audit page shows figures and charts. One line of at most 20 words
per section is allowed. Anything a reader needs must be visible or one
tap away on a phone, never hover only. Put longer background in
`audit-site/README.md` or the methodology. Keep the static word-budget
test passing.

## No dated jobs for Tom

A fix must not leave a date on which Tom has to do something by hand or the
system stops. Anything time-limited (a dated correction, an allowance, a
yearly figure) must lapse by itself: it stops applying, is noted in the daily
summary, and alerts only if a real fault is still there when it matters.
Planning #138 has the background.

## The Pi is production. Nothing else.

This is Tom's golden rule. It is not a guideline and no ticket, prompt or
other section of this file overrides it.

The Pi runs the live system and receives deploys. All development, testing,
analysis, experiments and one-off work happen on Tom's PC, using this
repository and the data copies in `C:\dev\bbb-research`.

On the Pi an assistant may only:

1. **Deploy** finished, tested code with `deploy/push.py`.
2. **Read small things to diagnose a fault that happened there**: a log, a
   job record, a service status, the collector's last ten polls.
3. **Change a single setting** when that is the fix, and say so.

An assistant must never run on the Pi: a repack or `git gc`, `fsck`, a
rebuild, a backfill, a test run, a trial copy, an export, a manual backup,
a large query, a copy of data off the Pi, or anything "to check". There is
no wrapper, priority setting or time of day that makes these acceptable.

If a job seems to need heavy work on the Pi, the fix is code that lets the
Pi's own scheduled jobs do it safely, written and tested on the PC and then
deployed. If fresher data is needed than is on the PC, ask Tom first.

The collector must never be starved: every poll it misses is a permanent
hole in the live map and the audit. After every deploy, check its last ten
polls. If any were missed, stop and tell Tom before doing anything else.

If a ticket or a prompt asks for something this section forbids, do not do
it. Say so and ask Tom.

## Folders that are meant to be here

| Folder | What it is |
|---|---|
| `audit-site/`, `bot/`, `collector/`, `deploy/`, `pipeline/`, `scripts/`, `site/`, `social/` | The project |
| `docs/` | Tom's private planning notes (ignored by Git). Read `docs/AGENTS.md` before relying on them |
| `.scratch/` | AI and one-off working files (ignored by Git) |
| `.venv/`, `node_modules/`, `dist/`, `__pycache__/` | Installed tools and build output (ignored) |
