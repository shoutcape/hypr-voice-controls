import argparse
import fcntl
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from .audio import record_clip, stop_recording_pid
from .commands import match_command, normalize
from .config import (
    AUDIO_BACKEND,
    AUDIO_SOURCE,
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
    SOCKET_PATH,
    STATE_MAX_AGE_SECONDS,
    WAKEWORD_ENABLED_DEFAULT,
    WAKEWORD_STATE_PATH,
    WAKE_GREETING_ENABLED,
    WAKE_GREETING_TEXT,
    WAKE_DICTATE_SESSION_MAX_SECONDS,
    WAKE_INTENT_VAD_END_SILENCE_MS,
    WAKE_SESSION_MAX_SECONDS,
    WAKE_START_SPEECH_TIMEOUT_MS,
    WAKE_VAD_RMS_THRESHOLD,
    WAKE_VAD_MIN_SPEECH_MS,
    WAKE_VAD_END_SILENCE_MS,
    WAKE_DICTATE_VAD_END_SILENCE_MS,
    VENV_PYTHON,
    DICTATION_INJECTOR,
)
from .integrations import (
    inject_text_into_focused_input,
    notify,
    run_command,
)
from .logging_utils import LOGGER
from .overlay import show_partial
from .orchestrator import run_endpointed_command_session
from .state_utils import get_saved_dictation_language, write_private_text
from .stt import dictation_model_name, is_model_loaded, preload_models, transcribe, warm_model


ALLOWED_INPUT_MODES = {
    "voice",
    "text",
    "dictate",
    "dictate-start",
    "dictate-stop",
    "command-start",
    "command-stop",
    "command-auto",
    "wake-start",
    "wakeword-enable",
    "wakeword-disable",
    "wakeword-toggle",
    "wakeword-status",
}

WAKEWORD_INPUT_MODES = {
    "wake-start",
    "wakeword-enable",
    "wakeword-disable",
    "wakeword-toggle",
    "wakeword-status",
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


def _is_state_stale(started_at: float | int | None) -> bool:
    if not isinstance(started_at, (int, float)):
        return False
    return (time.time() - float(started_at)) > STATE_MAX_AGE_SECONDS


def _state_required_substrings(state: dict) -> list[str]:
    raw = state.get("pid_required_substrings")
    if isinstance(raw, list):
        tokens = [token for token in raw if isinstance(token, str) and token.strip()]
        if tokens:
            return tokens
    return ["ffmpeg"]


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


def _read_wakeword_enabled() -> bool:
    try:
        payload = json.loads(WAKEWORD_STATE_PATH.read_text(encoding="utf-8"))
        enabled = payload.get("enabled")
        if isinstance(enabled, bool):
            return enabled
    except FileNotFoundError:
        pass
    except Exception as exc:
        LOGGER.warning("Could not read wakeword state: %s", exc)
    return WAKEWORD_ENABLED_DEFAULT


def _set_wakeword_enabled(enabled: bool) -> None:
    state = {
        "enabled": enabled,
        "updated_at": time.time(),
    }
    write_private_text(WAKEWORD_STATE_PATH, json.dumps(state))


def _say_wake_greeting() -> None:
    if not WAKE_GREETING_ENABLED or not WAKE_GREETING_TEXT:
        return

    if shutil.which("spd-say"):
        try:
            subprocess.Popen(
                ["spd-say", WAKE_GREETING_TEXT],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        except Exception as exc:
            LOGGER.warning("Wake greeting via spd-say failed: %s", exc)

    if shutil.which("espeak"):
        try:
            subprocess.Popen(
                ["espeak", WAKE_GREETING_TEXT],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        except Exception as exc:
            LOGGER.warning("Wake greeting via espeak failed: %s", exc)


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


def _build_ffmpeg_capture_cmd(audio_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        AUDIO_BACKEND,
        "-i",
        AUDIO_SOURCE,
        "-ac",
        "1",
        "-ar",
        "16000",
        str(audio_path),
    ]


def _wait_for_captured_audio(audio_path: Path, timeout_seconds: float = 2.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if audio_path.exists() and audio_path.stat().st_size > 0:
            break
        time.sleep(0.05)


def _start_press_hold_session(
    *,
    state_path: Path,
    tmp_prefix: str,
    source_key: str,
    preempt_label: str,
    preempt_fn: Callable[[], int],
    notify_text: str,
    start_log_key: str,
) -> int:
    if state_path.exists():
        LOGGER.info("Voice hotkey source=%s detected existing active state; preempting old %s", source_key, preempt_label)
        preempt_fn()

    language = get_saved_dictation_language()
    tmpdir = tempfile.mkdtemp(prefix=tmp_prefix)
    audio_path = Path(tmpdir) / "capture.wav"

    try:
        proc = subprocess.Popen(
            _build_ffmpeg_capture_cmd(audio_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError:
        notify("Voice", "ffmpeg not found")
        LOGGER.error("Could not start %s recorder: ffmpeg not found", preempt_label)
        return 1

    state = {
        "pid": proc.pid,
        "tmpdir": tmpdir,
        "audio_path": str(audio_path),
        "pid_required_substrings": ["ffmpeg", str(audio_path)],
        "language": language,
        "started_at": time.time(),
    }
    write_private_text(state_path, json.dumps(state))
    notify("Voice", notify_text.format(language=language))
    LOGGER.info("Voice hotkey %s pid=%s language=%s audio=%s", start_log_key, proc.pid, language, audio_path)
    return 0


def _stop_press_hold_session(
    *,
    state_path: Path,
    no_active_source: str,
    no_active_trigger_source: str,
    no_active_notify: str,
    state_label: str,
    processing_notify: str,
    stale_label: str,
    stop_label: str,
    no_speech_source: str,
    transcribe_mode: str,
    transcribe_failure_label: str,
    transcribe_failure_source: str,
    on_transcription: Callable[[str, str | None, float | None, str], int],
) -> int:
    if not state_path.exists():
        LOGGER.info("Voice hotkey end status=%s source=%s", no_active_source, no_active_trigger_source)
        notify("Voice", no_active_notify)
        return 0

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.error("Failed to parse %s state: %s", state_label, exc)
        state_path.unlink(missing_ok=True)
        return 1

    pid = int(state.get("pid", 0))
    audio_path = Path(state.get("audio_path", ""))
    tmpdir = Path(state.get("tmpdir", ""))
    language = state.get("language", get_saved_dictation_language())
    required_substrings = _state_required_substrings(state)
    started_at = state.get("started_at")

    notify("Voice", processing_notify)

    if pid > 0:
        if _is_state_stale(started_at):
            LOGGER.warning(
                "Skipping stale %s recorder stop pid=%s started_at=%s max_age=%ss",
                stale_label,
                pid,
                started_at,
                STATE_MAX_AGE_SECONDS,
            )
        else:
            stop_recording_pid(pid, stop_label, required_substrings=required_substrings)

    _wait_for_captured_audio(audio_path)

    try:
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
    finally:
        state_path.unlink(missing_ok=True)
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
        state_path=DICTATE_STATE_PATH,
        tmp_prefix="voice-dictate-hold-",
        source_key="dictate_start",
        preempt_label="dictation",
        preempt_fn=stop_press_hold_dictation,
        notify_text="Recording... release keys to transcribe ({language})",
        start_log_key="dictate_start",
    )


def start_press_hold_command() -> int:
    return _start_press_hold_session(
        state_path=COMMAND_STATE_PATH,
        tmp_prefix="voice-command-hold-",
        source_key="command_start",
        preempt_label="command",
        preempt_fn=stop_press_hold_command,
        notify_text="Listening for command ({language})... release keys to run",
        start_log_key="command_start",
    )


def stop_press_hold_dictation() -> int:
    def _on_dictation_transcription(
        text: str,
        detected_language: str | None,
        language_probability: float | None,
        selected_language: str,
    ) -> int:
        selected_dictation_model = dictation_model_name(selected_language)
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


def _parse_wake_intent(raw_text: str) -> tuple[str | None, str]:
    spoken = _strip_wake_prefix(raw_text, preserve_case=True)
    lower_spoken = spoken.lower()

    lowered_tokens = lower_spoken.split()
    spoken_tokens = spoken.split()
    for index, token in enumerate(lowered_tokens):
        keyword = re.sub(r"[^a-z0-9]+", "", token)
        if keyword in WAKE_INTENT_DICTATE_KEYWORDS:
            remainder = " ".join(spoken_tokens[index + 1 :]).strip()
            return "dictate", remainder
        if keyword in WAKE_INTENT_COMMAND_KEYWORDS:
            remainder = " ".join(spoken_tokens[index + 1 :]).strip()
            return "command", remainder
    return None, ""


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


def run_wake_followup_session(intent: str, language: str) -> int:
    if intent == "dictate":
        notify("Voice", "Wake mode: dictation")
        return run_endpointed_command_session(
            language=language,
            source="wake_dictate",
            command_handler=handle_dictation_text,
            max_seconds=WAKE_DICTATE_SESSION_MAX_SECONDS,
            start_speech_timeout_ms=WAKE_START_SPEECH_TIMEOUT_MS,
            vad_rms_threshold=WAKE_VAD_RMS_THRESHOLD,
            vad_min_speech_ms=WAKE_VAD_MIN_SPEECH_MS,
            vad_end_silence_ms=WAKE_DICTATE_VAD_END_SILENCE_MS,
            prompt_text="Wake heard, speak dictation...",
            stt_mode="dictate",
        )

    notify("Voice", "Wake mode: command")
    return run_endpointed_command_session(
        language=language,
        source="wake_command",
        command_handler=handle_command_text,
        max_seconds=WAKE_SESSION_MAX_SECONDS,
        start_speech_timeout_ms=WAKE_START_SPEECH_TIMEOUT_MS,
        vad_rms_threshold=WAKE_VAD_RMS_THRESHOLD,
        vad_min_speech_ms=WAKE_VAD_MIN_SPEECH_MS,
        vad_end_silence_ms=WAKE_VAD_END_SILENCE_MS,
        prompt_text="Wake heard, speak command...",
    )


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
    parser = argparse.ArgumentParser(description="Voice/text hotkey command runner")
    parser.add_argument(
        "--input",
        choices=sorted(ALLOWED_INPUT_MODES),
        default="voice",
    )
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--wakeword-daemon", action="store_true")
    return parser.parse_args()


def handle_input(input_mode: str) -> int:
    if input_mode not in ALLOWED_INPUT_MODES:
        LOGGER.warning("Rejected unsupported input mode: %r", input_mode)
        return 2

    if input_mode == "wakeword-enable":
        _set_wakeword_enabled(True)
        notify("Voice", "Wake word enabled")
        LOGGER.info("Voice hotkey end status=ok source=wakeword_enable enabled=true")
        return 0

    if input_mode == "wakeword-disable":
        _set_wakeword_enabled(False)
        notify("Voice", "Wake word disabled")
        LOGGER.info("Voice hotkey end status=ok source=wakeword_disable enabled=false")
        return 0

    if input_mode == "wakeword-toggle":
        enabled = not _read_wakeword_enabled()
        _set_wakeword_enabled(enabled)
        notify("Voice", f"Wake word {'enabled' if enabled else 'disabled'}")
        LOGGER.info("Voice hotkey end status=ok source=wakeword_toggle enabled=%s", enabled)
        return 0

    if input_mode == "wakeword-status":
        enabled = _read_wakeword_enabled()
        notify("Voice", f"Wake word {'enabled' if enabled else 'disabled'}")
        LOGGER.info("Voice hotkey end status=ok source=wakeword_status enabled=%s", enabled)
        return 0

    if input_mode == "wake-start":
        if not _read_wakeword_enabled():
            LOGGER.info("Voice hotkey end status=wake_ignored source=wake_start enabled=false")
            return 0
        LOGGER.info(
            "Wake start triggered session_max=%s start_timeout_ms=%s vad_threshold=%s vad_min_speech_ms=%s vad_end_silence_ms=%s intent_end_silence_ms=%s dictate_end_silence_ms=%s",
            WAKE_SESSION_MAX_SECONDS,
            WAKE_START_SPEECH_TIMEOUT_MS,
            WAKE_VAD_RMS_THRESHOLD,
            WAKE_VAD_MIN_SPEECH_MS,
            WAKE_VAD_END_SILENCE_MS,
            WAKE_INTENT_VAD_END_SILENCE_MS,
            WAKE_DICTATE_VAD_END_SILENCE_MS,
        )
        _say_wake_greeting()
        wake_language = get_saved_dictation_language()

        def _wake_intent_handler(raw_text: str, source: str, language: str | None, language_probability: float | None) -> int:
            clean = normalize(raw_text)
            clean = _strip_wake_prefix(clean)
            intent, remainder = _parse_wake_intent(raw_text)
            probability = language_probability if language_probability is not None else 0.0
            LOGGER.info(
                "Wake intent language=%s probability=%.3f raw=%s normalized=%s intent=%s remainder=%s",
                language,
                probability,
                _sanitize_transcript(raw_text),
                _sanitize_transcript(clean),
                intent,
                _sanitize_transcript(remainder),
            )

            if intent == "command" and remainder:
                LOGGER.info("Wake intent command with inline payload; skipping follow-up capture")
                return handle_command_text(
                    remainder,
                    source="wake_command_inline",
                    language=language,
                    language_probability=language_probability,
                )

            if intent == "dictate" and remainder:
                LOGGER.info("Wake intent dictation with inline payload; skipping follow-up capture")
                return handle_dictation_text(
                    remainder,
                    source="wake_dictate_inline",
                    language=language,
                    language_probability=language_probability,
                )

            if not intent:
                spoken_after_prefix = _strip_wake_prefix(raw_text, preserve_case=True)
                normalized_after_prefix = normalize(spoken_after_prefix)
                word_count = len(normalized_after_prefix.split()) if normalized_after_prefix else 0
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

            selected_language = language or wake_language
            return run_wake_followup_session(intent, selected_language)

        return run_endpointed_command_session(
            language=wake_language,
            source="wake_start",
            command_handler=_wake_intent_handler,
            max_seconds=WAKE_SESSION_MAX_SECONDS,
            start_speech_timeout_ms=WAKE_START_SPEECH_TIMEOUT_MS,
            vad_rms_threshold=WAKE_VAD_RMS_THRESHOLD,
            vad_min_speech_ms=WAKE_VAD_MIN_SPEECH_MS,
            vad_end_silence_ms=WAKE_INTENT_VAD_END_SILENCE_MS,
            prompt_text="Wake heard, say command or dictate...",
        )

    if input_mode == "command-auto":
        return run_endpointed_command_session(
            language=get_saved_dictation_language(),
            source="command_auto",
            command_handler=handle_command_text,
        )

    if input_mode == "dictate-start":
        return start_press_hold_dictation()

    if input_mode == "dictate-stop":
        return stop_press_hold_dictation()

    if input_mode == "command-start":
        return start_press_hold_command()

    if input_mode == "command-stop":
        return stop_press_hold_command()

    LOGGER.info("Voice hotkey trigger start input=%s", input_mode)

    if input_mode in {"text", "dictate"}:
        return run_dictation()

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


def request_daemon(input_mode: str, *, auto_start: bool = True, entry_script: Path | None = None) -> int:
    payload = json.dumps({"input": input_mode}).encode("utf-8") + b"\n"

    for attempt in range(DAEMON_START_RETRIES):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(DAEMON_CONNECT_TIMEOUT)
                client.connect(str(SOCKET_PATH))
                client.settimeout(DAEMON_RESPONSE_TIMEOUT)
                client.sendall(payload)
                data = _recv_json_line(client)
            rc = int(data.get("rc", 1))
            if rc == 2 and input_mode in WAKEWORD_INPUT_MODES:
                LOGGER.warning(
                    "Daemon rejected input=%s with rc=2; daemon may be stale and need restart",
                    input_mode,
                )
                notify("Voice", "Voice daemon is stale, restart service")
            return rc
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, json.JSONDecodeError, ValueError, OSError):
            if not auto_start:
                return 1
            if attempt == 0:
                start_daemon(entry_script=entry_script)
            time.sleep(DAEMON_START_DELAY)

    LOGGER.error("Could not reach voice-hotkey daemon after retries")
    notify("Voice", "Voice daemon unavailable")
    return 1


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

    preload_models()
    startup_language = get_saved_dictation_language()
    threading.Thread(target=warm_model, args=(dictation_model_name(startup_language),), daemon=True).start()

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(SOCKET_PATH))
            try:
                SOCKET_PATH.chmod(0o600)
            except Exception as exc:
                LOGGER.warning("Could not chmod daemon socket: %s", exc)
            server.listen(8)
            LOGGER.info("Voice hotkey daemon listening socket=%s", SOCKET_PATH)

            while True:
                conn, _ = server.accept()
                with conn:
                    rc = 1
                    try:
                        conn.settimeout(DAEMON_CONNECT_TIMEOUT)
                        request = _recv_json_line(conn)
                    except (socket.timeout, UnicodeDecodeError, json.JSONDecodeError, ValueError, OSError) as exc:
                        LOGGER.warning("Voice daemon request parse failed: %s", exc)
                        rc = 1
                    else:
                        input_mode = request.get("input", "voice")
                        if input_mode not in ALLOWED_INPUT_MODES:
                            LOGGER.warning("Rejected invalid daemon input=%r", input_mode)
                            rc = 2
                        else:
                            try:
                                rc = handle_input(input_mode)
                            except Exception as exc:
                                LOGGER.exception("Voice daemon request handler failed input=%s: %s", input_mode, exc)
                                rc = 1

                    try:
                        conn.sendall((json.dumps({"rc": rc}) + "\n").encode("utf-8"))
                    except OSError:
                        pass
    finally:
        lock_handle.close()


def main(entry_script: Path | None = None) -> int:
    args = parse_args()

    if args.wakeword_daemon:
        from .wakeword import run_wakeword_daemon

        return run_wakeword_daemon()

    if args.daemon:
        return run_daemon()

    return request_daemon(args.input, entry_script=entry_script)
