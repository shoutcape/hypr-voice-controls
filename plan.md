# Hypr Voice Controls v1 Implementation Plan

This document is the execution plan for evolving the current repo into a low-latency, streaming voice-control system with optional wake word support.

## Locked Decisions

- Repository: continue in this repo (`/home/shoutcape/Github/hypr-voice-controls`)
- ASR backend target: `whisper.cpp` server first
- Overlay: Hyprland native layer notification path
- Wake engine: `openWakeWord`
- Wake phrase: `hey hyper` (custom model)
- Wake profile: aggressive first, tune conservative later if needed
- Wake controls: runtime CLI toggle commands
- Wake model storage: user config path (`~/.config/hypr-voice-controls/wakeword/`)
- Dictation injector: `wtype`
- Language: English-only for v1
- Matching: hybrid (exact first, fuzzy fallback)
- Destructive command safety confirmations: enabled
- TTS scope: short confirmations only
- Barge-in: only after activation
- Wake greeting: speak `hello` on wake activation only (not on PTT)

## Primary Goals

1. Reduce command latency using streaming audio + VAD endpointing + warm ASR service.
2. Keep existing user-facing controls stable while migrating internals.
3. Maintain deterministic command execution and safe destructive-action handling.
4. Keep architecture modular so each service can be restarted/debugged independently.

## Non-Goals (v1)

- Cloud ASR/TTS dependencies
- Full conversational assistant behavior
- Dynamic multi-mic profile switching

## Current Baseline (What Exists)

- CLI entrypoints already wired from Hypr binds (`command-start/stop`, `dictate-start/stop`, etc.)
- Daemonized request/response over Unix socket
- Local command map from `~/.config/hypr/voice-commands.json`
- Non-streaming capture/transcribe flow using `ffmpeg` clip files and `faster-whisper`

## Target Architecture (v1)

- `voice-hotkey.py` and CLI inputs remain stable for compatibility.
- `voice_hotkey/app.py` continues to be the integration point, routing to new orchestrator flow.
- New orchestrator handles:
  - frame-based capture lifecycle
  - VAD speech start/end
  - streaming ASR client interactions
  - mode routing (command/dictation)
  - wake/TTS/barge-in coordination

### Service Layout

- `voice-hotkey.service` (orchestrator daemon)
- `whispercpp-asr.service` (long-lived whisper.cpp server)
- `tts.service` (optional local TTS RPC endpoint)
- `wakeword.service` (optional openWakeWord daemon)

## Phased Delivery

## Phase M1: Streaming Core

### Objectives

- Introduce streaming path with minimal user-facing changes.
- Achieve low-latency finalization from VAD endpointing.

### Work Items

1. Add orchestrator module and lifecycle states (`idle`, `capturing`, `processing`, `speaking`).
2. Add streaming audio module for 20ms, mono, 16k PCM chunks.
3. Add VAD module for speech start detection and silence endpointing.
4. Add whisper.cpp server client module with partial/final transcript events.
5. Add overlay module for partial transcript updates with rate limiting.
6. Route hold-to-command and hold-to-dictate into orchestrator path.
7. Preserve legacy clip-based flow behind a temporary fallback switch.

### Exit Criteria

- Press/hold command path produces partial text during speech and final text soon after silence.
- Dictation and command modes continue functioning via existing CLI inputs.
- No regressions in daemon startup and socket request handling.

## Phase M2: Intent Routing, Dictation, and TTS

### Objectives

- Improve command recognition reliability and response feedback.

### Work Items

1. Add intent router module.
2. Keep exact regex command map as first-pass matcher.
3. Add fuzzy fallback using `rapidfuzz` with configurable threshold.
4. Add structured intents:
   - `launch <app>`
   - `open project <name>`
   - `search web <query>`
5. Add app resolution using config aliases + desktop entry fallback scan.
6. Add project resolution using explicit map + fallback resolver hook.
7. Switch dictation injection to `wtype` primary path.
8. Add TTS client RPC (`speak`, `stop`) for short confirmations.
9. Implement barge-in that interrupts TTS only after activation.
10. Add destructive-action confirmation flow.

### Exit Criteria

- Hybrid matching works predictably with confidence threshold.
- Dictation text reliably enters focused app via `wtype`.
- TTS confirmations play and can be interrupted by activated user speech.
- Risky commands require confirmation before execution.

## Phase M3: Wakeword + Hardening

### Objectives

- Add custom wakeword support with practical controls.

### Work Items

1. Add wakeword daemon module using `openWakeWord`.
2. Load custom `hey hyper` model from user config path.
3. Add aggressive threshold defaults and cooldown/debounce controls.
4. Add CLI controls:
   - `wakeword-enable`
   - `wakeword-disable`
   - `wakeword-toggle`
   - `wakeword-status`
5. Wire wake trigger to orchestrator capture path (same as PTT).
6. Add wake-only greeting: speak `hello` on wake activation.
7. Add wake metrics logging (score, accepted/rejected triggers).
8. Add health checks/retries for ASR/TTS/wake service dependencies.

### Exit Criteria

- Wakeword can be toggled on/off at runtime.
- Wake activation triggers capture and greeting as specified.
- False-trigger behavior is manageable with aggressive default + cooldown.
- System recovers cleanly from dependent service restarts.

## File-by-File Plan

## New Files

- `voice_hotkey/orchestrator.py`
- `voice_hotkey/audio_stream.py`
- `voice_hotkey/vad.py`
- `voice_hotkey/asr_whispercpp.py`
- `voice_hotkey/overlay.py`
- `voice_hotkey/intent_router.py`
- `voice_hotkey/tts_client.py`
- `voice_hotkey/wakeword.py`

## Modified Files

- `voice_hotkey/app.py` (input handling, orchestration routing, new CLI inputs)
- `voice_hotkey/config.py` (new env flags and defaults)
- `voice_hotkey/integrations.py` (dictation injector update to `wtype` path)
- `voice_hotkey/commands.py` (integration point for exact-first matching and fuzzy fallback hooks)
- `README.md` (new architecture, dependencies, setup, toggles, troubleshooting)
- `examples/systemd/voice-hotkey.service` (service behavior updates)
- `examples/systemd/*.service` (new ASR/TTS/wake templates)

## Configuration Plan

Add environment variables with sane defaults and backward-compatible behavior:

- `VOICE_ASR_BACKEND=whispercpp_server`
- `VOICE_WHISPER_SERVER_URL=http://127.0.0.1:<port>`
- `VOICE_FRAME_MS=20`
- `VOICE_SAMPLE_RATE=16000`
- `VOICE_VAD_AGGRESSIVENESS=2`
- `VOICE_VAD_MIN_SPEECH_MS=120`
- `VOICE_VAD_END_SILENCE_MS=800`
- `VOICE_OVERLAY_ENABLED=true`
- `VOICE_DICTATION_INJECTOR=wtype`
- `VOICE_MATCH_FUZZY_THRESHOLD=<value>`
- `VOICE_WAKEWORD_ENABLED=false`
- `VOICE_WAKEWORD_MODEL_PATH=~/.config/hypr-voice-controls/wakeword/`
- `VOICE_WAKEWORD_PHRASE=hey hyper`
- `VOICE_WAKEWORD_PROFILE=aggressive`
- `VOICE_WAKEWORD_COOLDOWN_MS=1500`
- `VOICE_WAKE_GREETING_ENABLED=true`
- `VOICE_WAKE_GREETING_TEXT=hello`

## Testing Strategy

## Unit/Module Validation

- VAD state transitions with synthetic frame sequences
- Intent routing correctness (exact, fuzzy, slot parsing)
- Wake toggle command handling and state persistence

## Integration Validation

- End-to-end PTT command flow latency
- End-to-end dictation flow into focused app using `wtype`
- ASR server disconnect/restart behavior
- TTS playback + barge-in interruption
- Wake trigger behavior with runtime toggles

## Manual Smoke Tests

- `command-start` -> speak command -> `command-stop`
- `dictate-start` -> speak text -> `dictate-stop`
- `wakeword-toggle` + wake phrase trigger
- TTS interruption during activated speech

## Risks and Mitigations

- **Risk:** wake false positives in noisy environments
  - **Mitigation:** cooldown, min consecutive detections, threshold tuning, optional disable toggle

- **Risk:** overlay spam or stale transcript display
  - **Mitigation:** update throttling and explicit session end clear

- **Risk:** service dependency race conditions at startup
  - **Mitigation:** retry/backoff and clear status notifications

- **Risk:** dictation injection compatibility differences across apps
  - **Mitigation:** primary `wtype` path + optional fallback strategy

## Definition of Done (v1)

1. Existing bind-driven UX still works with new internals.
2. Streaming + VAD endpointing improves responsiveness over baseline.
3. Command and dictation modes are stable and predictable.
4. Wakeword can be enabled/disabled at runtime and triggers `hello` + capture.
5. Documentation and systemd templates are updated and reproducible.

## Immediate Next Steps

1. Implement M1 skeleton modules and wire `app.py` to orchestrator path.
2. Add configuration scaffolding and guardrails for fallback behavior.
3. Run syntax checks and first manual smoke tests.
