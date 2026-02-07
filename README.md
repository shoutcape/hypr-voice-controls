# Voice Hotkey Local Workflow

Local git workflow to snapshot and restore active voice-control files.

## Tracked live files

- `~/.local/bin/voice-hotkey.py`
- `~/.config/hypr/bindings.conf`
- `~/Documents/Obsidian Notes/Research/OpenCode/Voice Commands/getting-started.md`

## Repo layout

- `live/...` mirrors the live filesystem paths.
- `scripts/snapshot.sh` copies current live files into `live/...`.
- `scripts/restore.sh` copies files from `live/...` back to live locations.

## Daily workflow

1. Pull current state into repo:

```bash
./scripts/snapshot.sh
```

2. Review and commit:

```bash
git status
git diff
git add -A
git commit -m "voice: <what changed>"
```

3. If needed, restore tracked files back to live paths:

```bash
./scripts/restore.sh
```

## Notes

- This repo is local-only by default.
- Paths with spaces are handled by the scripts.
