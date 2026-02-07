# Hypr Voice Controls

Repository-only workflow for voice control script and docs.

## Repo layout

- `voice-hotkey.py` is the runtime script.
- `getting-started.md` is the implementation and testing note.
- `scripts/snapshot.sh` validates there are no legacy symlinks in old live paths.
- `scripts/restore.sh` is a compatibility check script (no symlink creation).

## Tracked files

- `voice-hotkey.py`
- `getting-started.md`

## Daily workflow

1. Edit files directly in this repository:

```bash
$EDITOR "voice-hotkey.py"
$EDITOR "getting-started.md"
```

2. Verify no legacy symlinks remain:

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

## Notes

- This repo is local-only by default.
- Paths with spaces are handled by the scripts.
- Hyprland binds should point directly to `voice-hotkey.py` in this repo.

## New system setup

- Required binary: `ffmpeg`
- Optional binaries: `hyprctl`, `wl-copy`, `notify-send`, `zenity`
- Python deps: `faster-whisper` (+ CUDA wheels if using GPU)

Environment overrides:

```bash
export VOICE_COMMAND_MODEL=tiny
export VOICE_DICTATE_MODEL=medium
export VOICE_DEVICE="cuda,cpu"
export VOICE_COMPUTE_TYPE=float16
export VOICE_AUDIO_BACKEND=pulse
export VOICE_AUDIO_SOURCE=default
```
