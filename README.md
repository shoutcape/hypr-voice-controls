# Hypr Voice Controls

Local git workflow to snapshot and restore active voice-control files.

The repository is the source of truth for all tracked files. Live paths are symlinked to repo files.

## Tracked live files

- `~/.local/bin/voice-hotkey.py`
- `~/.config/hypr/bindings.conf`
- `~/Documents/Obsidian Notes/Research/OpenCode/Voice Commands/getting-started.md`

## Repo layout

- `live/...` mirrors the live filesystem paths.
- `scripts/snapshot.sh` verifies all live paths are symlinked to repo files.
- `scripts/restore.sh` creates/refreshes symlinks from live paths to repo files.

## Tracked files

- `live/.local/bin/voice-hotkey.py`
- `live/Documents/Obsidian Notes/Research/OpenCode/Voice Commands/getting-started.md`

## Daily workflow

1. Restore symlink and tracked files to live paths:

```bash
./scripts/restore.sh
```

2. Verify symlinks are correct:

```bash
./scripts/snapshot.sh
```

3. Review and commit:

```bash
git status
git diff
git add -A
git commit -m "voice: <what changed>"
```

4. Edit tracked files in repo paths:

```bash
$EDITOR "live/.local/bin/voice-hotkey.py"
$EDITOR "live/Documents/Obsidian Notes/Research/OpenCode/Voice Commands/getting-started.md"
```

## Notes

- This repo is local-only by default.
- Paths with spaces are handled by the scripts.
- All live tracked paths resolve to repo files via symlink.
