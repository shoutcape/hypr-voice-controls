---
title: Voice Commands Getting Started (Current Implementation Audit)
tags: [arch-linux, omarchy, hyprland, voice-control, faster-whisper, nvidia]
sources:
  - title: faster-whisper
    url: https://github.com/SYSTRAN/faster-whisper
  - title: CTranslate2 installation
    url: https://opennmt.net/CTranslate2/installation.html
  - title: Hyprland binds docs
    url: https://wiki.hypr.land/Configuring/Binds/
  - title: Hyprland dispatchers docs
    url: https://wiki.hypr.land/Configuring/Dispatchers/
  - title: ArchWiki PipeWire
    url: https://wiki.archlinux.org/title/PipeWire
  - title: OpenAI Whisper repository
    url: https://github.com/openai/whisper
date: 2026-02-07
status: active
---

# Scope

This note replaces older architecture-first research and documents what is actually implemented right now on this machine.

Context note: [[OpenCode/Voice Commands/2026-02-07 voice commands for omarchy arch linux]] was removed because it mixed future recommendations with active state.

Related: [[Hyprland]] [[PipeWire]] [[Systemd User Services]]

# Evidence used

- Runtime command handler: `/home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py`
- Active desktop keybinds: `/home/shoutcape/.config/hypr/bindings.conf`
- Tracked Omarchy copy of binds: `/home/shoutcape/Github/omarchy-config/omarchy-config/config/user/hypr/bindings.conf`

# Currently implemented voice commands

## Hotkeys and modes

- `code:194` press: start command capture (`--input command-start`)
- `code:194` release: stop capture + transcribe + execute match (`--input command-stop`)
- `code:195` press: start dictation capture (`--input dictate-start`)
- `code:195` release: stop capture + transcribe + paste text (`--input dictate-stop`)
- `code:197` press: open language chooser for dictation (`--input dictate-language`)

## Command allowlist

- `workspace one`, `workspace 1`, `työtila yksi`, `tyotila yksi`, `työtila ykkönen`, `tyotila ykkonen` -> `hyprctl dispatch workspace 1`
- `workspace two`, `workspace 2`, `työtila kaksi`, `tyotila kaksi`, `työtila kakkonen` -> `hyprctl dispatch workspace 2`
- `volume up`, `ääni kovemmalle`, `aani kovemmalle`, `laita ääntä kovemmalle` (+ fuzzy/mishear variants) -> `pamixer -i 5`
- `volume down`, `ääni hiljemmalle`, `aani hiljemmalle`, `laita ääntä hiljemmalle` (+ fuzzy/mishear variants) -> `pamixer -d 5`
- `lock`, `lock screen`, `lukitse näyttö`, `lukitse naytto` -> `loginctl lock-session`

## Matching and safety behavior

- Transcript is normalized (lowercase, punctuation stripped, polite prefixes removed).
- Match flow is regex allowlist first, then narrow fuzzy fallback.
- Execution uses fixed argv via `subprocess.run([...], timeout=8)`.
- No shell interpolation and no `shell=True` in command execution path.

# Pipeline behavior

- Audio capture: `ffmpeg` on Pulse/PipeWire default source, mono `16kHz`, hold cap `15s`.
- STT engine: `faster_whisper` hybrid models, device `cuda`, compute `float16`:
  - command mode: `tiny`
  - dictation mode: `medium`
- Dictation language: saved `fi`/`en`; command mode also uses the selected saved language (no auto-detect).
- Dictation output: `wl-copy` then Hyprland `sendshortcut` paste attempts.
- Logging: `/home/shoutcape/.local/state/voice-hotkey.log`.

# Model A/B test phrases

Use these exact phrases so logs are easy to compare between model changes.

## Command mode phrases (`code:194` hold/release)

- English:
  - `workspace one`
  - `workspace two`
  - `volume up`
  - `volume down`
  - `lock screen`
- Finnish:
  - `työtila yksi`
  - `työtila kaksi`
  - `ääni kovemmalle`
  - `ääni hiljemmalle`
  - `lukitse näyttö`
This is a dictation latency test
Volume down, volume up and lock screen are command phrases.
How quickly does this appear in the text box?
Tämä on Sonelun viivetesti.
Äänikovemmalle ja äänihiljemmalle ovat komentolauseita.
kuinka nopeasti tämä ilmestyy tekstikenttään.
## Dictation mode phrases (`code:195` hold/release)

- English:
  - `This is a dictation latency test.`
  - `Volume down, volume up, and lock screen are command phrases.`
  - `How quickly does this appear in the text box?`
- Finnish:
  - `Tama on sanelun viivetesti.`
  - `Aani kovemmalle ja aani hiljemmalle ovat komentolauseita.`
  - `Kuinka nopeasti tama ilmestyy tekstikenttaan?`

## Log check commands

```bash
tail -n 120 /home/shoutcape/.local/state/voice-hotkey.log
```

```bash
rg "Input source=voice_hold|Dictation hold|Voice hotkey end status|Paste attempt" /home/shoutcape/.local/state/voice-hotkey.log
```

# Tradeoffs

- Current hotkey design is low-risk and deterministic but not hands-free.
- Command set is small and reliable, but limited to desktop primitives (workspace, volume, lock).
- GPU Whisper improves accuracy/latency, but adds CUDA runtime coupling and dependency complexity.
- Fuzzy matching improves usability, but can increase edge-case false positives if expanded too far.
# Recommendation

Keep this hotkey-driven architecture as the canonical baseline and grow command coverage incrementally. Prioritize:

1. Add explicit confirmation for dangerous actions before expanding command scope.
2. Add lightweight cooldown/debounce controls to reduce repeated triggers.
3. Keep command routing deterministic (allowlist phrase -> fixed argv) as a hard constraint.

# Uncertainty

- The active implementation is file-based and user-session based; no systemd user service was found for voice runtime supervision.
- No wake-word layer was found in active config/scripts; if one exists outside these paths, it was not visible in this audit.

# Sources

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [CTranslate2 installation](https://opennmt.net/CTranslate2/installation.html)
- [Hyprland binds docs](https://wiki.hypr.land/Configuring/Binds/)
- [Hyprland dispatchers docs](https://wiki.hypr.land/Configuring/Dispatchers/)
- [ArchWiki: PipeWire](https://wiki.archlinux.org/title/PipeWire)
- [OpenAI Whisper repository](https://github.com/openai/whisper)
