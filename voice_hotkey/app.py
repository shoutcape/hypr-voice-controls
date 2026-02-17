import argparse
import fcntl
import itertools
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .audio import build_ffmpeg_wav_capture_cmd, record_clip, stop_recording_pid
from .commands import match_command, normalize
from .config import (
    COMMAND_STATE_PATH,
    DAEMON_CONNECT_TIMEOUT,
    DAEMON_MAX_REQUEST_BYTES,
    DAEMON_RESPONSE_TIMEOUT,
    DAEMON_START_DELAY,
    DAEMON_START_RETRIES,
    DICTATE_SECONDS,
    DICTATE_STATE_PATH,
    LOCK_PATH,
    LOG_TRANSCRIPTS,
    RUNTIME_V2_ENABLED,
    SOCKET_PATH,
    STATE_MAX_AGE_SECONDS,
    WAKE_GREETING_ENABLED,
    WAKE_GREETING_TEXT,
    WAKE_INTENT_VAD_END_SILENCE_MS,
    WAKE_SESSION_MAX_SECONDS,
    WAKE_START_SPEECH_TIMEOUT_MS,
    WAKE_VAD_RMS_THRESHOLD,
    WAKE_VAD_MIN_SPEECH_MS,
    WAKE_VAD_END_SILENCE_MS,
    VENV_PYTHON,
    DICTATION_INJECTOR,
)
from .integrations import (
    inject_text_into_focused_input,
    notify,
    run_command,
    speak_text,
)
from .logging_utils import LOGGER
from .overlay import show_partial
from .orchestrator import CANCELLED_EXIT_CODE, run_endpointed_command_session
from .runtime.controller import serve_with_thread_pool
from .runtime.job_queue import RuntimeJobQueue
from .runtime.state_machine import RuntimeStateMachine
from .state_utils import (
    get_saved_dictation_language,
    is_capture_state_active_payload,
    read_wakeword_enabled,
    state_required_substrings,
    set_wakeword_enabled,
    write_private_text,
)
from .stt import dictation_model_name, is_model_loaded, preload_models, transcribe, warm_model


INPUT_MODE_DESCRIPTIONS: dict[str, str] = {
    "voice": "Capture short command clip and execute matching action",
    "dictate": "Capture dictation clip and paste into focused app",
    "dictate-start": "Start press/hold dictation recording",
    "dictate-stop": "Stop dictation hold, transcribe, and paste",
    "command-start": "Start press/hold command recording",
    "command-stop": "Stop command hold, transcribe, and execute action",
    "command-auto": "Endpointed command session until VAD endpoint",
    "wake-start": "Wake session intent capture and routed handling",
    "wakeword-enable": "Enable wakeword runtime state",
    "wakeword-disable": "Disable wakeword runtime state",
    "wakeword-toggle": "Toggle wakeword runtime state",
    "wakeword-status": "Read wakeword runtime state",
    "runtime-status": "Read runtime queue/state health snapshot",
    "runtime-status-json": "Read runtime queue/state snapshot as JSON",
}

ALLOWED_INPUT_MODES = set(INPUT_MODE_DESCRIPTIONS)

WAKEWORD_INPUT_MODES = {
    "wake-start",
    "wakeword-enable",
    "wakeword-disable",
    "wakeword-toggle",
    "wakeword-status",
}

NON_AUDIO_INPUT_MODES = {
    "wakeword-enable",
    "wakeword-disable",
    "wakeword-toggle",
    "wakeword-status",
    "runtime-status",
    "runtime-status-json",
}

RUNTIME_V2_QUEUED_INPUT_MODES = {
    "voice",
    "dictate",
    "dictate-stop",
    "command-stop",
    "command-auto",
    "wake-start",
}

RUNTIME_V2_DIRECT_INPUT_MODES = ALLOWED_INPUT_MODES - RUNTIME_V2_QUEUED_INPUT_MODES

AUDIO_RESCAN_SERVICES = ("wireplumber", "pipewire", "pipewire-pulse")
RUNTIME_V2_CONNECTION_WORKERS = 4
RUNTIME_V2_EXECUTION_QUEUE_MAX = 8
RUNTIME_STATE_MACHINE = RuntimeStateMachine()
RUNTIME_EXECUTION_QUEUE = RuntimeJobQueue(max_size=RUNTIME_V2_EXECUTION_QUEUE_MAX, logger=LOGGER)
DAEMON_REQUEST_IDS = itertools.count(1)

LEGACY_INPUT_ALIASES = {
    "text": "dictate",
}

WAKE_PREFIXES = (
    "hey hyper",
    "hey hypr",
    "heyhyper",
    "heyhypr",
)

WAKE_AUTO_DICTATION_MIN_WORDS = 4

WAKE_INTENT_COMMAND_KEYWORDS = {
    "command",
    "commands",
}

WAKE_INTENT_DICTATE_KEYWORDS = {
    "dictate",
    "dictation",
    "write",
}


def _format_available_actions() -> str:
    lines = ["Available actions:"]
    for mode in sorted(ALLOWED_INPUT_MODES):
        lines.append(f"  {mode:16} {INPUT_MODE_DESCRIPTIONS[mode]}")
    return "\n".join(lines)


def _normalize_input_mode(input_mode: str) -> str:
    mapped = LEGACY_INPUT_ALIASES.get(input_mode, input_mode)
    if mapped != input_mode:
        LOGGER.info("Normalized legacy input=%s to input=%s", input_mode, mapped)
    return mapped


def _resolve_admission_class(input_mode: str) -> str:
    if not RUNTIME_V2_ENABLED:
        return "legacy"
    if input_mode in RUNTIME_V2_QUEUED_INPUT_MODES:
        return "queued"
    return "direct"


def _run_cli_command(argv: list[str], *, timeout_seconds: int) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def _print_audio_inventory() -> int:
    sections = [
        ("cards", ["pactl", "list", "short", "cards"]),
        ("sinks", ["pactl", "list", "short", "sinks"]),
        ("sources", ["pactl", "list", "short", "sources"]),
    ]
    failures = 0
    for title, cmd in sections:
        rc, out, err = _run_cli_command(cmd, timeout_seconds=8)
        print(f"[{title}]")
        if rc != 0:
            failures += 1
            detail = err.strip() or f"command_failed rc={rc}"
            print(detail)
        else:
            rendered = out.strip() if out.strip() else "(none)"
            print(rendered)
        print()
    if failures:
        LOGGER.warning("Audio inventory had %s failing section(s)", failures)
        return 1
    return 0


def _rescan_audio_devices() -> int:
    restart_cmd = ["systemctl", "--user", "restart", *AUDIO_RESCAN_SERVICES]
    rc, _out, err = _run_cli_command(restart_cmd, timeout_seconds=20)
    if rc != 0:
        LOGGER.error("Audio rescan failed rc=%s err=%s", rc, err.strip())
        print("Failed to restart user audio services.")
        if err.strip():
            print(err.strip())
        return 1

    for _attempt in range(10):
        cards_rc, cards_out, _cards_err = _run_cli_command(["pactl", "list", "short", "cards"], timeout_seconds=5)
        if cards_rc == 0 and cards_out.strip():
            break
        time.sleep(0.4)

    print("Audio services restarted.")
    return _print_audio_inventory()


def _reset_services(entry_script: Path | None) -> int:
    configured_script = os.environ.get("VOICE_RESET_SCRIPT", "").strip()
    if configured_script:
        script_path = Path(configured_script).expanduser()
        if not script_path.exists():
            LOGGER.error("Configured reset script not found path=%s", script_path)
            print(f"Configured reset script not found: {script_path}")
            print("Set VOICE_RESET_SCRIPT to a valid path or unset it to use default lookup.")
            return 1
    else:
        candidate_paths: list[Path] = []
        if entry_script is not None:
            candidate_paths.append(entry_script.parent / "scripts" / "reset-voice-services.sh")
        candidate_paths.append(Path(__file__).resolve().parent.parent / "scripts" / "reset-voice-services.sh")
        script_path = next((path for path in candidate_paths if path.exists()), candidate_paths[0])

    if not script_path.exists():
        LOGGER.error("Reset script not found path=%s", script_path)
        print(f"Reset script not found: {script_path}")
        print("Run from the repository checkout or set VOICE_RESET_SCRIPT to the script path.")
        return 1

    rc, out, err = _run_cli_command([str(script_path)], timeout_seconds=45)
    if out.strip():
        print(out.strip())
    if rc != 0:
        if err.strip():
            print(err.strip())
        LOGGER.error("Service reset failed rc=%s path=%s err=%s", rc, script_path, err.strip())
        return 1
    return 0


def _sanitize_transcript(value: str) -> str:
    if LOG_TRANSCRIPTS:
        return repr(value)
    return f"<redacted len={len(value)}>"


def _strip_wake_prefix(text: str, *, preserve_case: bool = False) -> str:
    spoken = text.strip()
    lowered = spoken.lower()
    for prefix in WAKE_PREFIXES:
        if lowered.startswith(prefix):
            source = spoken if preserve_case else lowered
            remainder = source[len(prefix) :].lstrip(" ,.:;!?-")
            return remainder
    return spoken


def _strip_leading_wake_mode_keywords(text: str) -> str:
    tokens = text.split()
    while tokens:
        keyword = re.sub(r"[^a-z0-9]+", "", tokens[0].lower())
        if keyword in WAKE_INTENT_DICTATE_KEYWORDS or keyword in WAKE_INTENT_COMMAND_KEYWORDS:
            tokens.pop(0)
            continue
        break
    return " ".join(tokens).strip()


def _recv_json_line(sock: socket.socket) -> dict:
    chunks: list[bytes] = []
    total = 0
    while True:
        block = sock.recv(1024)
        if not block:
            break
        chunks.append(block)
        total += len(block)
        if total > DAEMON_MAX_REQUEST_BYTES:
            raise ValueError("request_too_large")
        if b"\n" in block:
            break

    raw = b"".join(chunks)
    if not raw:
        raise ValueError("empty_request")

    line = raw.split(b"\n", 1)[0].strip()
    if not line:
        raise ValueError("empty_request")
    return json.loads(line.decode("utf-8"))


def _say_wake_greeting() -> None:
    if not WAKE_GREETING_ENABLED or not WAKE_GREETING_TEXT:
        return
    if not speak_text(WAKE_GREETING_TEXT):
        LOGGER.warning("Wake greeting skipped: no TTS backend available")


def validate_environment() -> bool:
    required_tools = ["ffmpeg"]
    missing_required = [tool for tool in required_tools if not shutil.which(tool)]
    if missing_required:
        LOGGER.error("Missing required tools: %s", ", ".join(missing_required))
        notify("Voice", f"Missing required tools: {', '.join(missing_required)}")
        return False

    optional_tools = ["hyprctl", "wl-copy", "notify-send"]
    if DICTATION_INJECTOR == "wtype":
        optional_tools.append("wtype")
    missing_optional = [tool for tool in optional_tools if not shutil.which(tool)]
    if missing_optional:
        LOGGER.warning("Missing optional tools: %s", ", ".join(missing_optional))

    if DICTATION_INJECTOR == "wtype" and not shutil.which("wtype"):
        LOGGER.warning("Dictation injector is set to wtype but wtype is not installed; using clipboard fallback")
        notify("Voice", "wtype missing: using clipboard fallback")

    return True


def _wait_for_captured_audio(audio_path: Path, timeout_seconds: float = 2.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if audio_path.exists() and audio_path.stat().st_size > 0:
            break
        time.sleep(0.05)


def _load_press_hold_state(state_path: Path, state_label: str) -> dict | None:
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.error("Failed to parse %s state: %s", state_label, exc)
        state_path.unlink(missing_ok=True)
        return None


def _stop_press_hold_recorder(
    *,
    pid: int,
    started_at: float | int | None,
    stale_label: str,
    stop_label: str,
    required_substrings: list[str],
) -> None:
    if pid <= 0:
        return

    capture_state = {
        "pid": pid,
        "started_at": started_at,
        "pid_required_substrings": required_substrings,
    }
    if not is_capture_state_active_payload(capture_state):
        LOGGER.warning(
            "Skipping inactive %s recorder stop pid=%s started_at=%s max_age=%ss",
            stale_label,
            pid,
            started_at,
            STATE_MAX_AGE_SECONDS,
        )
        return

    stop_recording_pid(pid, stop_label, required_substrings=required_substrings)


def _process_press_hold_transcription(
    *,
    audio_path: Path,
    language: str,
    no_speech_source: str,
    transcribe_mode: str,
    transcribe_failure_label: str,
    transcribe_failure_source: str,
    on_transcription: Callable[[str, str | None, float | None, str], int],
) -> int:
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        notify("Voice", "No speech captured")
        LOGGER.info("Voice hotkey end status=no_speech source=%s", no_speech_source)
        return 0

    try:
        text, detected_language, language_probability = transcribe(audio_path, language=language, mode=transcribe_mode)
    except Exception as exc:
        notify("Voice", f"Transcription failed: {type(exc).__name__}")
        LOGGER.exception("%s transcription failed: %s", transcribe_failure_label, exc)
        LOGGER.info("Voice hotkey end status=transcription_failed source=%s", transcribe_failure_source)
        return 1

    return on_transcription(text, detected_language, language_probability, language)


@dataclass(frozen=True)
class _PressHoldStartProfile:
    state_path: Path
    tmp_prefix: str
    source_key: str
    preempt_label: str
    preempt_fn: Callable[[], int]
    notify_text: str
    start_log_key: str


@dataclass(frozen=True)
class _PressHoldStopProfile:
    state_path: Path
    no_active_source: str
    no_active_trigger_source: str
    no_active_notify: str
    state_label: str
    processing_notify: str
    stale_label: str
    stop_label: str
    no_speech_source: str
    transcribe_mode: str
    transcribe_failure_label: str
    transcribe_failure_source: str


def _start_press_hold_session(
    profile: _PressHoldStartProfile,
) -> int:
    if profile.state_path.exists():
        LOGGER.info("Voice hotkey source=%s detected existing active state; preempting old %s", profile.source_key, profile.preempt_label)
        profile.preempt_fn()

    language = get_saved_dictation_language()
    tmpdir = tempfile.mkdtemp(prefix=profile.tmp_prefix)
    audio_path = Path(tmpdir) / "capture.wav"

    try:
        proc = subprocess.Popen(
            build_ffmpeg_wav_capture_cmd(audio_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError:
        notify("Voice", "ffmpeg not found")
        LOGGER.error("Could not start %s recorder: ffmpeg not found", profile.preempt_label)
        return 1

    state = {
        "pid": proc.pid,
        "tmpdir": tmpdir,
        "audio_path": str(audio_path),
        "pid_required_substrings": ["ffmpeg", str(audio_path)],
        "language": language,
        "started_at": time.time(),
    }
    write_private_text(profile.state_path, json.dumps(state))
    notify("Voice", profile.notify_text.format(language=language))
    LOGGER.info("Voice hotkey %s pid=%s language=%s audio=%s", profile.start_log_key, proc.pid, language, audio_path)
    return 0


def _stop_press_hold_session(
    profile: _PressHoldStopProfile,
    on_transcription: Callable[[str, str | None, float | None, str], int],
) -> int:
    if not profile.state_path.exists():
        LOGGER.info("Voice hotkey end status=%s source=%s", profile.no_active_source, profile.no_active_trigger_source)
        notify("Voice", profile.no_active_notify)
        return 0

    state = _load_press_hold_state(profile.state_path, profile.state_label)
    if state is None:
        return 1

    pid = int(state.get("pid", 0))
    audio_path = Path(state.get("audio_path", ""))
    tmpdir = Path(state.get("tmpdir", ""))
    language = state.get("language", get_saved_dictation_language())
    required_substrings = state_required_substrings(state)
    started_at = state.get("started_at")

    notify("Voice", profile.processing_notify)

    _stop_press_hold_recorder(
        pid=pid,
        started_at=started_at,
        stale_label=profile.stale_label,
        stop_label=profile.stop_label,
        required_substrings=required_substrings,
    )

    _wait_for_captured_audio(audio_path)

    try:
        return _process_press_hold_transcription(
            audio_path=audio_path,
            language=language,
            no_speech_source=profile.no_speech_source,
            transcribe_mode=profile.transcribe_mode,
            transcribe_failure_label=profile.transcribe_failure_label,
            transcribe_failure_source=profile.transcribe_failure_source,
            on_transcription=on_transcription,
        )
    finally:
        profile.state_path.unlink(missing_ok=True)
        if tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


def _complete_dictation_output(spoken: str, *, source: str) -> int:
    if not spoken:
        notify("Voice", "No speech detected")
        LOGGER.info("Voice hotkey end status=no_speech source=%s", source)
        return 0

    if inject_text_into_focused_input(spoken):
        notify("Voice", "Dictation pasted")
        LOGGER.info("Voice hotkey end status=ok source=%s text=%s", source, _sanitize_transcript(spoken))
        return 0

    notify("Voice", "Dictation paste failed")
    LOGGER.info("Voice hotkey end status=paste_failed source=%s text=%s", source, _sanitize_transcript(spoken))
    return 1


def start_press_hold_dictation() -> int:
    return _start_press_hold_session(
        _PressHoldStartProfile(
            state_path=DICTATE_STATE_PATH,
            tmp_prefix="voice-dictate-hold-",
            source_key="dictate_start",
            preempt_label="dictation",
            preempt_fn=stop_press_hold_dictation,
            notify_text="Recording... release keys to transcribe ({language})",
            start_log_key="dictate_start",
        )
    )


def start_press_hold_command() -> int:
    return _start_press_hold_session(
        _PressHoldStartProfile(
            state_path=COMMAND_STATE_PATH,
            tmp_prefix="voice-command-hold-",
            source_key="command_start",
            preempt_label="command",
            preempt_fn=stop_press_hold_command,
            notify_text="Listening for command ({language})... release keys to run",
            start_log_key="command_start",
        )
    )


def stop_press_hold_dictation() -> int:
    def _on_dictation_transcription(
        text: str,
        detected_language: str | None,
        language_probability: float | None,
        selected_language: str,
    ) -> int:
        selected_dictation_model = dictation_model_name()
        if not is_model_loaded(selected_dictation_model):
            LOGGER.info("Dictation model not yet cached model=%s", selected_dictation_model)

        spoken = text.strip()
        probability = language_probability if language_probability is not None else 0.0
        LOGGER.info(
            "Dictation hold language_selected=%s language_detected=%s probability=%.3f text=%s",
            selected_language,
            detected_language,
            probability,
            _sanitize_transcript(spoken),
        )
        return _complete_dictation_output(spoken, source="dictate_hold")

    return _stop_press_hold_session(
        _PressHoldStopProfile(
            state_path=DICTATE_STATE_PATH,
            no_active_source="no_active_dictation",
            no_active_trigger_source="dictate_stop",
            no_active_notify="No active dictation",
            state_label="dictation",
            processing_notify="Key released. Processing dictation...",
            stale_label="dictation",
            stop_label="Dictation ffmpeg",
            no_speech_source="dictate_hold",
            transcribe_mode="dictate",
            transcribe_failure_label="Dictation hold",
            transcribe_failure_source="dictate_hold",
        ),
        on_transcription=_on_dictation_transcription,
    )


def stop_press_hold_command() -> int:
    def _on_command_transcription(
        text: str,
        detected_language: str | None,
        language_probability: float | None,
        _selected_language: str,
    ) -> int:
        return handle_command_text(
            text,
            source="voice_hold",
            language=detected_language,
            language_probability=language_probability,
        )

    return _stop_press_hold_session(
        _PressHoldStopProfile(
            state_path=COMMAND_STATE_PATH,
            no_active_source="no_active_command",
            no_active_trigger_source="command_stop",
            no_active_notify="No active voice command",
            state_label="command",
            processing_notify="Key released. Processing command...",
            stale_label="command",
            stop_label="Command ffmpeg",
            no_speech_source="voice_hold",
            transcribe_mode="command",
            transcribe_failure_label="Command hold",
            transcribe_failure_source="voice_hold",
        ),
        on_transcription=_on_command_transcription,
    )


def run_dictation() -> int:
    selected_language = "en"

    with tempfile.TemporaryDirectory(prefix="voice-dictate-") as tmpdir:
        audio_path = Path(tmpdir) / "capture.wav"
        notify("Voice", f"Dictation ({selected_language}) for {DICTATE_SECONDS}s...")
        if not record_clip(audio_path, duration_seconds=DICTATE_SECONDS):
            notify("Voice", "Mic capture failed")
            LOGGER.info("Voice hotkey end status=mic_capture_failed source=dictate")
            return 1

        try:
            text, detected_language, language_probability = transcribe(audio_path, language=selected_language, mode="dictate")
        except Exception as exc:
            notify("Voice", f"Dictation failed: {type(exc).__name__}")
            LOGGER.exception("Dictation transcription failed: %s", exc)
            LOGGER.info("Voice hotkey end status=transcription_failed source=dictate")
            return 1

        spoken = text.strip()
        probability = language_probability if language_probability is not None else 0.0
        LOGGER.info(
            "Dictation language_selected=%s language_detected=%s probability=%.3f text=%s",
            selected_language,
            detected_language,
            probability,
            _sanitize_transcript(spoken),
        )
        return _complete_dictation_output(spoken, source="dictate")


def _handle_wake_intent(
    raw_text: str,
    *,
    language: str | None,
    language_probability: float | None,
) -> int:
    spoken_after_prefix = _strip_wake_prefix(raw_text, preserve_case=True)
    spoken_after_prefix = _strip_leading_wake_mode_keywords(spoken_after_prefix)
    clean = normalize(spoken_after_prefix)
    probability = language_probability if language_probability is not None else 0.0
    LOGGER.info(
        "Wake intent language=%s probability=%.3f raw=%s normalized=%s mode=length_based",
        language,
        probability,
        _sanitize_transcript(raw_text),
        _sanitize_transcript(clean),
    )
    if not spoken_after_prefix:
        LOGGER.info("Wake input empty after stripping leading mode keywords; ignoring")
        return 0

    word_count = len(clean.split()) if clean else 0
    if word_count >= WAKE_AUTO_DICTATION_MIN_WORDS:
        LOGGER.info(
            "Wake implicit dictation by length words=%s threshold=%s",
            word_count,
            WAKE_AUTO_DICTATION_MIN_WORDS,
        )
        return handle_dictation_text(
            spoken_after_prefix,
            source="wake_dictate_implicit",
            language=language,
            language_probability=language_probability,
        )

    LOGGER.info(
        "Wake implicit command by length words=%s threshold=%s",
        word_count,
        WAKE_AUTO_DICTATION_MIN_WORDS,
    )
    return handle_command_text(
        spoken_after_prefix,
        source="wake_start",
        language=language,
        language_probability=language_probability,
    )


def handle_dictation_text(raw_text: str, source: str, language: str | None, language_probability: float | None) -> int:
    spoken = raw_text.strip()
    probability = language_probability if language_probability is not None else 0.0
    LOGGER.info(
        "Dictation language_detected=%s probability=%.3f source=%s text=%s",
        language,
        probability,
        source,
        _sanitize_transcript(spoken),
    )

    return _complete_dictation_output(spoken, source=source)


def handle_command_text(raw_text: str, source: str, language: str | None, language_probability: float | None) -> int:
    clean = normalize(raw_text)
    if source == "wake_start":
        clean = _strip_wake_prefix(clean)
    show_partial(clean)
    probability = language_probability if language_probability is not None else 0.0
    LOGGER.info(
        "Input source=%s language=%s probability=%.3f raw=%s normalized=%s",
        source,
        language,
        probability,
        _sanitize_transcript(raw_text),
        _sanitize_transcript(clean),
    )

    if not clean:
        notify("Voice", "No command detected")
        LOGGER.info("Voice hotkey end status=no_input source=%s", source)
        return 0

    argv, label = match_command(clean)
    if not argv:
        notify("Voice", f"Heard: '{clean}' (no match)")
        LOGGER.info("Voice hotkey end status=no_match source=%s heard=%s", source, _sanitize_transcript(clean))
        return 0

    ok = run_command(argv)
    if ok:
        notify("Voice", f"Heard: '{clean}' -> {label}")
        LOGGER.info(
            "Voice hotkey end status=ok source=%s heard=%s action=%s argv=%s",
            source,
            _sanitize_transcript(clean),
            label,
            argv,
        )
        return 0

    notify("Voice", f"Command failed: {label}")
    LOGGER.info(
        "Voice hotkey end status=command_failed source=%s heard=%s action=%s argv=%s",
        source,
        _sanitize_transcript(clean),
        label,
        argv,
    )
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Voice/dictation hotkey command runner")
    parser.add_argument(
        "--input",
        default="voice",
        help="Input action (use --list-actions to discover supported values)",
    )
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--wakeword-daemon", action="store_true")
    parser.add_argument(
        "--list-actions",
        action="store_true",
        help="Print available --input actions and exit",
    )
    parser.add_argument(
        "--describe-action",
        choices=sorted(ALLOWED_INPUT_MODES),
        help="Print one action description and exit",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Run --input directly without daemon RPC (debug)",
    )
    parser.add_argument(
        "--list-audio",
        action="store_true",
        help="Print current audio cards/sinks/sources and exit",
    )
    parser.add_argument(
        "--rescan-audio",
        action="store_true",
        help="Restart user audio services, then print audio inventory",
    )
    parser.add_argument(
        "--restart-audio",
        action="store_true",
        help="Alias for --rescan-audio",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset voice services (set VOICE_RESET_SCRIPT to override script path)",
    )
    return parser.parse_args()


def _handle_wakeword_toggle_input(input_mode: str) -> int | None:
    if input_mode == "wakeword-enable":
        set_wakeword_enabled(True)
        notify("Voice", "Wake word enabled")
        LOGGER.info("Voice hotkey end status=ok source=wakeword_enable enabled=true")
        return 0

    if input_mode == "wakeword-disable":
        set_wakeword_enabled(False)
        notify("Voice", "Wake word disabled")
        LOGGER.info("Voice hotkey end status=ok source=wakeword_disable enabled=false")
        return 0

    if input_mode == "wakeword-toggle":
        enabled = not read_wakeword_enabled()
        set_wakeword_enabled(enabled)
        notify("Voice", f"Wake word {'enabled' if enabled else 'disabled'}")
        LOGGER.info("Voice hotkey end status=ok source=wakeword_toggle enabled=%s", enabled)
        return 0

    if input_mode == "wakeword-status":
        enabled = read_wakeword_enabled()
        notify("Voice", f"Wake word {'enabled' if enabled else 'disabled'}")
        LOGGER.info("Voice hotkey end status=ok source=wakeword_status enabled=%s", enabled)
        return 0

    return None


def _run_wake_start(*, cancel_event: threading.Event | None = None) -> int:
    if not read_wakeword_enabled():
        LOGGER.info("Voice hotkey end status=wake_ignored source=wake_start enabled=false")
        return 0

    LOGGER.info(
        "Wake start triggered session_max=%s start_timeout_ms=%s vad_threshold=%s vad_min_speech_ms=%s vad_end_silence_ms=%s intent_end_silence_ms=%s",
        WAKE_SESSION_MAX_SECONDS,
        WAKE_START_SPEECH_TIMEOUT_MS,
        WAKE_VAD_RMS_THRESHOLD,
        WAKE_VAD_MIN_SPEECH_MS,
        WAKE_VAD_END_SILENCE_MS,
        WAKE_INTENT_VAD_END_SILENCE_MS,
    )
    _say_wake_greeting()
    wake_language = get_saved_dictation_language()

    def _wake_intent_handler(raw_text: str, source: str, language: str | None, language_probability: float | None) -> int:
        return _handle_wake_intent(
            raw_text,
            language=language,
            language_probability=language_probability,
        )

    return run_endpointed_command_session(
        language=wake_language,
        source="wake_start",
        command_handler=_wake_intent_handler,
        max_seconds=WAKE_SESSION_MAX_SECONDS,
        start_speech_timeout_ms=WAKE_START_SPEECH_TIMEOUT_MS,
        vad_rms_threshold=WAKE_VAD_RMS_THRESHOLD,
        vad_min_speech_ms=WAKE_VAD_MIN_SPEECH_MS,
        vad_end_silence_ms=WAKE_INTENT_VAD_END_SILENCE_MS,
        prompt_text="Wake heard, speak now...",
        cancel_event=cancel_event,
    )


def _run_wake_start_v2() -> int:
    transition = RUNTIME_STATE_MACHINE.transition("wake-start")
    if not transition.allowed:
        return _reject_runtime_transition(transition.action, transition.previous_state, transition.reason)
    _log_runtime_transition(transition.action, transition.previous_state, transition.next_state)

    rc = 1
    try:
        rc = _run_v2_queued_call("wake-start", lambda cancel_event: _run_wake_start(cancel_event=cancel_event))
    finally:
        action = "wake-complete" if rc == 0 else "wake-failed"
        completion = RUNTIME_STATE_MACHINE.transition(action)
        if not completion.allowed:
            LOGGER.warning(
                "Runtime transition completion rejected action=%s from=%s reason=%s",
                completion.action,
                completion.previous_state,
                completion.reason,
            )
        else:
            _log_runtime_transition(completion.action, completion.previous_state, completion.next_state)
    return rc


def _handle_hold_input(input_mode: str) -> int | None:
    if input_mode == "dictate-start":
        if RUNTIME_V2_ENABLED:
            return _run_dictate_start_v2()
        return start_press_hold_dictation()
    if input_mode == "dictate-stop":
        if RUNTIME_V2_ENABLED:
            return _run_dictate_stop_v2()
        return stop_press_hold_dictation()
    if input_mode == "command-start":
        if RUNTIME_V2_ENABLED:
            return _run_command_start_v2()
        return start_press_hold_command()
    if input_mode == "command-stop":
        if RUNTIME_V2_ENABLED:
            return _run_command_stop_v2()
        return stop_press_hold_command()
    return None


def _reject_runtime_transition(action: str, previous_state: str, reason: str | None) -> int:
    LOGGER.info(
        "Runtime transition rejected action=%s from=%s reason=%s",
        action,
        previous_state,
        reason,
    )
    notify("Voice", "Voice busy")
    return 1


def _log_runtime_transition(action: str, previous_state: str, next_state: str) -> None:
    LOGGER.info("Runtime transition action=%s from=%s to=%s", action, previous_state, next_state)


def _log_runtime_queue_snapshot(context: str) -> None:
    snapshot_fn = getattr(RUNTIME_EXECUTION_QUEUE, "snapshot", None)
    if callable(snapshot_fn):
        snapshot = snapshot_fn()
        LOGGER.info(
            "Runtime queue snapshot context=%s pending=%s running_id=%s running_name=%s running_age_ms=%s worker_alive=%s worker_restarts=%s",
            context,
            getattr(snapshot, "pending", "unknown"),
            getattr(snapshot, "running_job_id", "unknown"),
            getattr(snapshot, "running_job_name", "unknown"),
            getattr(snapshot, "running_age_ms", "unknown"),
            getattr(snapshot, "worker_alive", "unknown"),
            getattr(snapshot, "worker_restarts", "unknown"),
        )
        return

    pending_fn = getattr(RUNTIME_EXECUTION_QUEUE, "pending", None)
    pending = pending_fn() if callable(pending_fn) else "unknown"
    LOGGER.info(
        "Runtime queue snapshot context=%s pending=%s running_id=%s running_name=%s running_age_ms=%s",
        context,
        pending,
        "unknown",
        "unknown",
        "unknown",
    )


def _runtime_status_text() -> str:
    payload = _runtime_status_payload()
    return (
        "Runtime status: "
        f"pending={payload['pending']} "
        f"running={payload['running_job_name']} "
        f"age_ms={payload['running_age_ms']} "
        f"worker_alive={payload['worker_alive']} "
        f"worker_restarts={payload['worker_restarts']}"
    )


def _runtime_status_payload() -> dict[str, object]:
    runtime_state = RUNTIME_STATE_MACHINE.get_state()
    snapshot_fn = getattr(RUNTIME_EXECUTION_QUEUE, "snapshot", None)
    if callable(snapshot_fn):
        snapshot = snapshot_fn()
        return {
            "state": runtime_state,
            "pending": getattr(snapshot, "pending", "unknown"),
            "running_job_id": getattr(snapshot, "running_job_id", "unknown"),
            "running_job_name": getattr(snapshot, "running_job_name", "unknown"),
            "running_age_ms": getattr(snapshot, "running_age_ms", "unknown"),
            "worker_alive": getattr(snapshot, "worker_alive", "unknown"),
            "worker_restarts": getattr(snapshot, "worker_restarts", "unknown"),
        }

    pending_fn = getattr(RUNTIME_EXECUTION_QUEUE, "pending", None)
    pending = pending_fn() if callable(pending_fn) else "unknown"
    return {
        "state": runtime_state,
        "pending": pending,
        "running_job_id": "unknown",
        "running_job_name": "unknown",
        "running_age_ms": "unknown",
        "worker_alive": "unknown",
        "worker_restarts": "unknown",
    }


def _run_runtime_status(*, notify_user: bool = True) -> int:
    status_text = _runtime_status_text()
    if notify_user:
        notify("Voice", status_text)
    LOGGER.info("%s state=%s", status_text, _runtime_status_payload()["state"])
    return 0


def _run_v2_queued_call(job_name: str, fn: Callable[[threading.Event], int]) -> int:
    future = RUNTIME_EXECUTION_QUEUE.submit(job_name, fn)
    if future is None:
        LOGGER.warning("Runtime execution queue full job=%s", job_name)
        _log_runtime_queue_snapshot("queue_full")
        notify("Voice", "Voice busy")
        return 1

    try:
        return int(future.result())
    except CancelledError:
        LOGGER.info("Runtime queued job cancelled job=%s", job_name)
        return CANCELLED_EXIT_CODE
    except Exception as exc:
        LOGGER.exception("Runtime queued job failed job=%s: %s", job_name, exc)
        return 1


def _cancel_v2_long_running_jobs(*, source: str) -> bool:
    cancelled_any = False
    for job_name in ("command-auto", "wake-start"):
        if RUNTIME_EXECUTION_QUEUE.cancel_by_name(job_name):
            cancelled_any = True
            LOGGER.info("Runtime cancellation requested by %s job=%s", source, job_name)
    if cancelled_any:
        _log_runtime_queue_snapshot(f"cancel_requested:{source}")
    return cancelled_any


def _run_command_auto_v2() -> int:
    return _run_v2_queued_call(
        "command-auto",
        lambda cancel_event: run_endpointed_command_session(
            language=get_saved_dictation_language(),
            source="command_auto",
            command_handler=handle_command_text,
            cancel_event=cancel_event,
        ),
    )


def _run_dictate_v2() -> int:
    return _run_v2_queued_call("dictate", lambda _cancel_event: run_dictation())


def _run_voice_capture_v2() -> int:
    return _run_v2_queued_call("voice", lambda _cancel_event: _run_voice_command_capture())


def _run_dictate_start_v2() -> int:
    transition = RUNTIME_STATE_MACHINE.transition("dictate-start")
    if not transition.allowed:
        return _reject_runtime_transition(transition.action, transition.previous_state, transition.reason)
    _log_runtime_transition(transition.action, transition.previous_state, transition.next_state)

    rc = start_press_hold_dictation()
    if rc != 0:
        RUNTIME_STATE_MACHINE.transition("dictate-start-failed")
    return rc


def _run_dictate_stop_v2() -> int:
    cancelled_any = _cancel_v2_long_running_jobs(source="dictate-stop")
    if cancelled_any and not DICTATE_STATE_PATH.exists():
        return 0

    transition = RUNTIME_STATE_MACHINE.transition("dictate-stop")
    if not transition.allowed:
        return _reject_runtime_transition(transition.action, transition.previous_state, transition.reason)
    _log_runtime_transition(transition.action, transition.previous_state, transition.next_state)

    rc = 1
    try:
        rc = _run_v2_queued_call("dictate-stop", lambda _cancel_event: stop_press_hold_dictation())
    finally:
        completion = RUNTIME_STATE_MACHINE.transition("dictate-stop-complete")
        if not completion.allowed:
            LOGGER.warning(
                "Runtime transition completion rejected action=%s from=%s reason=%s",
                completion.action,
                completion.previous_state,
                completion.reason,
            )
        else:
            _log_runtime_transition(completion.action, completion.previous_state, completion.next_state)
    return rc


def _run_command_start_v2() -> int:
    transition = RUNTIME_STATE_MACHINE.transition("command-start")
    if not transition.allowed:
        return _reject_runtime_transition(transition.action, transition.previous_state, transition.reason)
    _log_runtime_transition(transition.action, transition.previous_state, transition.next_state)

    rc = start_press_hold_command()
    if rc != 0:
        RUNTIME_STATE_MACHINE.transition("command-start-failed")
    return rc


def _run_command_stop_v2() -> int:
    cancelled_any = _cancel_v2_long_running_jobs(source="command-stop")
    if cancelled_any and not COMMAND_STATE_PATH.exists():
        return 0

    transition = RUNTIME_STATE_MACHINE.transition("command-stop")
    if not transition.allowed:
        return _reject_runtime_transition(transition.action, transition.previous_state, transition.reason)
    _log_runtime_transition(transition.action, transition.previous_state, transition.next_state)

    rc = 1
    try:
        rc = _run_v2_queued_call("command-stop", lambda _cancel_event: stop_press_hold_command())
    finally:
        completion = RUNTIME_STATE_MACHINE.transition("command-stop-complete")
        if not completion.allowed:
            LOGGER.warning(
                "Runtime transition completion rejected action=%s from=%s reason=%s",
                completion.action,
                completion.previous_state,
                completion.reason,
            )
        else:
            _log_runtime_transition(completion.action, completion.previous_state, completion.next_state)
    return rc


def _run_voice_command_capture() -> int:
    with tempfile.TemporaryDirectory(prefix="voice-hotkey-") as tmpdir:
        audio_path = Path(tmpdir) / "capture.wav"
        language = get_saved_dictation_language()

        notify("Voice", f"Listening for 4 seconds ({language})...")
        if not record_clip(audio_path):
            notify("Voice", "Mic capture failed")
            LOGGER.info("Voice hotkey end status=mic_capture_failed source=voice")
            return 1

        try:
            text, detected_language, language_probability = transcribe(audio_path, language=language, mode="command")
        except Exception as exc:
            notify("Voice", f"Transcription failed: {type(exc).__name__}")
            LOGGER.exception("Transcription failed: %s", exc)
            LOGGER.info("Voice hotkey end status=transcription_failed source=voice")
            return 1

        return handle_command_text(
            text,
            source="voice",
            language=detected_language,
            language_probability=language_probability,
        )


def handle_input(input_mode: str) -> int:
    input_mode = _normalize_input_mode(input_mode)
    if input_mode not in ALLOWED_INPUT_MODES:
        LOGGER.warning("Rejected unsupported input mode: %r", input_mode)
        return 2

    wakeword_result = _handle_wakeword_toggle_input(input_mode)
    if wakeword_result is not None:
        return wakeword_result

    if input_mode == "runtime-status":
        return _run_runtime_status()

    if input_mode == "runtime-status-json":
        return _run_runtime_status(notify_user=False)

    if input_mode == "wake-start":
        if RUNTIME_V2_ENABLED:
            return _run_wake_start_v2()
        return _run_wake_start()

    if input_mode == "command-auto":
        if RUNTIME_V2_ENABLED:
            return _run_command_auto_v2()
        return run_endpointed_command_session(
            language=get_saved_dictation_language(),
            source="command_auto",
            command_handler=handle_command_text,
        )

    hold_result = _handle_hold_input(input_mode)
    if hold_result is not None:
        return hold_result

    LOGGER.info("Voice hotkey trigger start input=%s", input_mode)

    if input_mode == "dictate":
        if RUNTIME_V2_ENABLED:
            return _run_dictate_v2()
        return run_dictation()

    if RUNTIME_V2_ENABLED:
        return _run_voice_capture_v2()

    return _run_voice_command_capture()


def start_daemon(entry_script: Path | None = None) -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)

    runtime_python = str(VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable))
    script_path = entry_script if entry_script is not None else Path(sys.argv[0]).resolve()

    try:
        subprocess.Popen(
            [runtime_python, str(script_path), "--daemon"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        LOGGER.error("Could not start daemon process: %s", exc)


def request_daemon(
    input_mode: str,
    *,
    auto_start: bool = True,
    entry_script: Path | None = None,
    connect_timeout: float | None = None,
    response_timeout: float | None = None,
    retries: int | None = None,
    start_delay: float | None = None,
) -> int:
    response = request_daemon_response(
        input_mode,
        auto_start=auto_start,
        entry_script=entry_script,
        connect_timeout=connect_timeout,
        response_timeout=response_timeout,
        retries=retries,
        start_delay=start_delay,
    )
    rc_value = response.get("rc", 1)
    if isinstance(rc_value, (int, float, str)):
        return int(rc_value)
    return 1


def request_daemon_response(
    input_mode: str,
    *,
    auto_start: bool = True,
    entry_script: Path | None = None,
    connect_timeout: float | None = None,
    response_timeout: float | None = None,
    retries: int | None = None,
    start_delay: float | None = None,
) -> dict[str, object]:
    payload = json.dumps({"input": input_mode}).encode("utf-8") + b"\n"
    active_connect_timeout = DAEMON_CONNECT_TIMEOUT if connect_timeout is None else max(0.01, connect_timeout)
    active_response_timeout = DAEMON_RESPONSE_TIMEOUT if response_timeout is None else max(0.01, response_timeout)
    active_retries = DAEMON_START_RETRIES if retries is None else max(1, retries)
    active_start_delay = DAEMON_START_DELAY if start_delay is None else max(0.0, start_delay)

    for attempt in range(active_retries):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(active_connect_timeout)
                client.connect(str(SOCKET_PATH))
                client.settimeout(active_response_timeout)
                client.sendall(payload)
                data = _recv_json_line(client)
            rc_raw = data.get("rc", 1)
            rc = int(rc_raw) if isinstance(rc_raw, (int, float, str)) else 1
            if rc == 2 and input_mode in WAKEWORD_INPUT_MODES:
                LOGGER.warning(
                    "Daemon rejected input=%s with rc=2; daemon may be stale and need restart",
                    input_mode,
                )
                notify("Voice", "Voice daemon is stale, restart service")
            return data
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, json.JSONDecodeError, ValueError, OSError):
            if not auto_start:
                return {"rc": 1}
            if attempt == 0:
                start_daemon(entry_script=entry_script)
            time.sleep(active_start_delay)

    LOGGER.error("Could not reach voice-hotkey daemon after retries")
    notify("Voice", "Voice daemon unavailable")
    return {"rc": 1}


def _parse_daemon_request(conn: socket.socket) -> dict | None:
    try:
        conn.settimeout(DAEMON_CONNECT_TIMEOUT)
        return _recv_json_line(conn)
    except (socket.timeout, UnicodeDecodeError, json.JSONDecodeError, ValueError, OSError) as exc:
        LOGGER.warning("Voice daemon request parse failed: %s", exc)
        return None


def _execute_daemon_request(request: dict) -> int:
    request_id = next(DAEMON_REQUEST_IDS)
    started_at = time.time()
    input_mode = _normalize_input_mode(request.get("input", "voice"))
    admission_class = _resolve_admission_class(input_mode)
    if input_mode not in ALLOWED_INPUT_MODES:
        LOGGER.warning(
            "Rejected invalid daemon input=%r request_id=%s admission=%s",
            input_mode,
            request_id,
            admission_class,
        )
        return 2

    LOGGER.info(
        "Voice daemon request start id=%s input=%s admission=%s",
        request_id,
        input_mode,
        admission_class,
    )

    try:
        rc = handle_input(input_mode)
    except Exception as exc:
        elapsed_ms = int((time.time() - started_at) * 1000)
        LOGGER.exception(
            "Voice daemon request failed id=%s input=%s duration_ms=%s: %s",
            request_id,
            input_mode,
            elapsed_ms,
            exc,
        )
        return 1

    elapsed_ms = int((time.time() - started_at) * 1000)
    LOGGER.info(
        "Voice daemon request end id=%s input=%s admission=%s rc=%s duration_ms=%s",
        request_id,
        input_mode,
        admission_class,
        rc,
        elapsed_ms,
    )
    return rc


def _send_daemon_response(conn: socket.socket, rc: int, extra: dict[str, object] | None = None) -> None:
    try:
        payload: dict[str, object] = {"rc": rc}
        if extra:
            payload.update(extra)
        conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))
    except OSError as exc:
        LOGGER.debug("Voice daemon response send failed rc=%s err=%s", rc, exc)


def _handle_daemon_connection(conn: socket.socket) -> None:
    with conn:
        request = _parse_daemon_request(conn)
        if request is None:
            _send_daemon_response(conn, 1)
            return

        rc = _execute_daemon_request(request)
        input_mode = _normalize_input_mode(str(request.get("input", "voice")))
        if input_mode == "runtime-status-json" and rc == 0:
            _send_daemon_response(conn, rc, extra={"status": _runtime_status_payload()})
            return
        _send_daemon_response(conn, rc)


def run_daemon() -> int:
    if not validate_environment():
        return 1

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = LOCK_PATH.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        LOGGER.info("Voice hotkey daemon already running lock=%s", LOCK_PATH)
        lock_handle.close()
        return 0

    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink(missing_ok=True)

    runtime_mode = "v2" if RUNTIME_V2_ENABLED else "v1"
    LOGGER.info("Voice hotkey runtime mode=%s", runtime_mode)

    preload_models()
    threading.Thread(target=warm_model, args=(dictation_model_name(),), daemon=True).start()

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(SOCKET_PATH))
            try:
                SOCKET_PATH.chmod(0o600)
            except Exception as exc:
                LOGGER.warning("Could not chmod daemon socket: %s", exc)
            server.listen(8)
            LOGGER.info("Voice hotkey daemon listening socket=%s", SOCKET_PATH)

            if RUNTIME_V2_ENABLED:
                server.settimeout(1.0)
                LOGGER.info(
                    "Voice hotkey v2 control plane enabled max_connection_workers=%s",
                    RUNTIME_V2_CONNECTION_WORKERS,
                )
                serve_with_thread_pool(
                    server,
                    _handle_daemon_connection,
                    logger=LOGGER,
                    max_workers=RUNTIME_V2_CONNECTION_WORKERS,
                )
            else:
                while True:
                    conn, _ = server.accept()
                    _handle_daemon_connection(conn)
    finally:
        lock_handle.close()
    return 0


def main(entry_script: Path | None = None) -> int:
    args = parse_args()
    input_mode = _normalize_input_mode(args.input)

    if args.list_actions:
        print(_format_available_actions())
        return 0

    if args.describe_action:
        description = INPUT_MODE_DESCRIPTIONS[args.describe_action]
        print(f"{args.describe_action}: {description}")
        return 0

    if args.list_audio:
        return _print_audio_inventory()

    if args.rescan_audio or args.restart_audio:
        return _rescan_audio_devices()

    if args.reset:
        return _reset_services(entry_script)

    if args.wakeword_daemon:
        from .wakeword import run_wakeword_daemon

        return run_wakeword_daemon()

    if args.daemon:
        return run_daemon()

    if args.local:
        if input_mode == "runtime-status-json":
            print(json.dumps(_runtime_status_payload(), sort_keys=True))
            return _run_runtime_status(notify_user=False)
        if input_mode not in NON_AUDIO_INPUT_MODES and not validate_environment():
            return 1
        try:
            return handle_input(input_mode)
        except Exception as exc:
            LOGGER.exception("Local request handler failed input=%s: %s", input_mode, exc)
            return 1

    if input_mode == "runtime-status-json":
        response = request_daemon_response(input_mode, entry_script=entry_script)
        print(json.dumps(response.get("status", {}), sort_keys=True))
        rc_raw = response.get("rc", 1)
        return int(rc_raw) if isinstance(rc_raw, (int, float, str)) else 1

    return request_daemon(input_mode, entry_script=entry_script)
