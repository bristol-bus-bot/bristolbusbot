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

## The Pi runs the live system and receives deploys. Nothing else.

This is Tom's golden rule. It overrides anything else in this file or in a
task's instructions. All development, testing, analysis, experiments and
one-off work happen on Tom's PC, in `C:\dev\bristolbusbot` and the data
copies in `C:\dev\bbb-research`.

On the Pi you may only:

1. Deploy finished, tested code with `deploy/push.py`.
2. Read small things to diagnose a fault that happened there: a log, a job
   record, a service status, the collector's last ten polls.
3. Change a single setting when that is the fix.

You may not run on the Pi: `git gc` or any repack, `fsck`, rebuilds,
backfills, test runs, trial copies, exports, manual backups, large queries,
or anything "to check". `bbb-heavy` is not a licence to do them. If a job
seems to need heavy work on the Pi, the fix is code that makes the Pi's own
scheduled jobs do it safely, written and tested on the PC, then deployed. If
you need fresher data than is on the PC, ask Tom first.

Why: on 14 and 17 Nov 2026 manual work on the Pi starved the collector, and
on 17 Nov a manual `git gc` ran the Pi out of memory and it restarted. Every
poll the collector misses is a permanent hole in the live map and the audit.

### How the Pi's own jobs protect the collector

The collector is the one process that must never be starved. It has ten
times the normal share of CPU and disk, and the disks use the BFQ scheduler
so that share is honoured (planning #139). Every disk-heavy scheduled job
runs at low CPU and disk priority and takes the shared heavy-I/O lock, so
only one runs at a time. `bbb-heavy` applies the same lock and priorities
for scheduled or deployed code that needs them; it is not for manual work.

## Folders that are meant to be here

| Folder | What it is |
|---|---|
| `audit-site/`, `bot/`, `collector/`, `deploy/`, `pipeline/`, `scripts/`, `site/`, `social/` | The project |
| `docs/` | Tom's private planning notes (ignored by Git). Read `docs/AGENTS.md` before relying on them |
| `.scratch/` | AI and one-off working files (ignored by Git) |
| `.venv/`, `node_modules/`, `dist/`, `__pycache__/` | Installed tools and build output (ignored) |
