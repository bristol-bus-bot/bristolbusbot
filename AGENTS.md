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

## Folders that are meant to be here

| Folder | What it is |
|---|---|
| `audit-site/`, `bot/`, `collector/`, `deploy/`, `pipeline/`, `scripts/`, `site/`, `social/` | The project |
| `docs/` | Tom's private planning notes (ignored by Git). Read `docs/AGENTS.md` before relying on them |
| `.scratch/` | AI and one-off working files (ignored by Git) |
| `.venv/`, `node_modules/`, `dist/`, `__pycache__/` | Installed tools and build output (ignored) |
