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

## Heavy work on the Pi

The collector is the one process on the Pi that must never be starved: every
poll it misses is a permanent hole in the live map and the audit. It has ten
times the normal share of CPU and disk, and the disks use the BFQ scheduler so
that share is honoured (planning #139). Work done by hand has to cooperate:

- **One heavy job at a time, always through `bbb-heavy`**:
  `bbb-heavy -- <command>`. It waits for the shared heavy-I/O lock (so it
  queues behind the backup, rollup and timetable jobs) and runs at idle disk
  and lowest CPU priority. If you aren't sure whether something is heavy
  (a copy, export, backup, rebuild or large query), treat it as heavy.
- **No copies off the Pi while a backfill, rebuild, backup or rollup runs.**
  Prefer files already on Tom's PC in `C:\dev\bbb-research` (the live
  timetable, the research export, an audit copy). If a copy is unavoidable,
  rate-limit it and run it through the wrapper:
  `rsync --bwlimit=10000 --rsync-path="bbb-heavy -- rsync" <pi>:<file> .`
- **After any heavy job, check the collector's last ten polls** (`poll_log`
  in audit.db, or `journalctl -u bbb-collector`). If any were missed, stop
  and tell Tom before doing anything else.

## Folders that are meant to be here

| Folder | What it is |
|---|---|
| `audit-site/`, `bot/`, `collector/`, `deploy/`, `pipeline/`, `scripts/`, `site/`, `social/` | The project |
| `docs/` | Tom's private planning notes (ignored by Git). Read `docs/AGENTS.md` before relying on them |
| `.scratch/` | AI and one-off working files (ignored by Git) |
| `.venv/`, `node_modules/`, `dist/`, `__pycache__/` | Installed tools and build output (ignored) |
