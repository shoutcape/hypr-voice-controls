# Agent Guide: Hypr Voice Controls

## Scope
- This guide applies to the entire repository.
- Prefer minimal, safe changes that preserve existing behavior unless a request explicitly asks for behavior changes.

## Project Snapshot
- Entry point: `voice-hotkey.py` (compatibility wrapper to `voice_controls.main`).
- Core daemon/client flow: `voice_controls/app.py` over Unix socket at `~/.local/state/voice-hotkey.sock`.
- ASR layer: `voice_controls/stt.py` with optional `whisper.cpp` HTTP backend in `voice_controls/asr_whispercpp.py`.
- Command routing: `voice_controls/commands.py` using regex + `argv` mappings from `~/.config/hypr/voice-commands.json`.
- Integrations: `voice_controls/integrations.py` (notify, dictation injection, command execution).

## Non-Negotiable Safety Rules
- Keep command execution shell-free: pass argument arrays only; do not introduce `shell=True`.
- For subprocess calls, set explicit `timeout` values and handle non-zero return codes.
- Keep personal desktop config out of the repo. Use templates under `examples/`.
- Do not commit secrets, tokens, or private local state files.
- Preserve transcript privacy defaults (`VOICE_LOG_TRANSCRIPTS=false` by default).

## Python Quality Rules
- Keep `try` blocks narrow and catch specific exceptions where practical; avoid broad catches unless followed by clear logging/fallback behavior.
- Prefer type hints for new/changed public functions and data structures.
- Keep state writes atomic (`tmp` + replace) and preserve restrictive permissions for private state/log files.
- Route logs through shared logging utilities, and avoid logging raw sensitive content (tokens, full secrets, unredacted transcripts).

## Change Rules
- Preserve existing CLI inputs unless a migration is explicitly requested:
  - `command-start`, `command-stop`, `dictate-start`, `dictate-stop`.
- If you change environment variables in `voice_controls/config.py`, update docs and templates in the same change:
  - `README.md`
  - `getting-started.md`
  - `examples/systemd/voice-hotkey.service`
- If you change command schema/behavior, update:
  - `examples/hypr/voice-commands.json`
  - Any related sections in `README.md` and `getting-started.md`
- If you change keybind recommendations, update:
  - `examples/hypr/voice-hotkey.bindings.conf`
  - Relevant docs in `README.md` and `getting-started.md`

## Config and Path Conventions
- Use environment variables and existing helpers in `voice_controls/config.py`; avoid hardcoded machine-specific paths.
- In docs/templates, use `<REPO_DIR>` placeholders in examples when appropriate.
- User-specific runtime/config locations are expected outside the repo:
  - `~/.config/hypr/voice-commands.json`
  - `~/.local/state/voice-hotkey*`

## Validation Checklist (Required After Code Changes)
- Syntax check:

```bash
python3 -m py_compile voice-hotkey.py voice_controls/*.py
```

- For flow changes, run manual smoke tests from `README.md`:
  - command start/stop
  - dictate start/stop

## Documentation Standards
- Keep instructions copy/paste ready, concise, and reproducible.
- Clearly mark optional dependencies and optional services.
- When adding features, document:
  - required and optional dependencies
  - config/env vars with defaults
  - systemd or Hyprland setup impact
  - troubleshooting notes

## Commit and Review Guidance
- Follow current commit style in history: concise imperative messages (`add`, `docs`, `refactor`, `fix`).
- Keep changes focused; avoid unrelated refactors in the same patch.
- Prefer backward-compatible additions over breaking changes.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
