import argparse
import fcntl
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

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
    WAKE_SESSION_MAX_SECONDS,
    WAKE_START_SPEECH_TIMEOUT_MS,
    WAKE_VAD_RMS_THRESHOLD,
    WAKE_VAD_MIN_SPEECH_MS,
    WAKE_VAD_END_SILENCE_MS,
    VENV_PYTHON,
    DICTATION_INJECTOR,
)
from .integrations import inject_text_into_focused_input, notify, run_command
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
)


def _sanitize_transcript(value: str) -> str:
    if LOG_TRANSCRIPTS:
        return repr(value)
    return f"<redacted len={len(value)}>"


def _strip_wake_prefix(text: str) -> str:
    trimmed = text.strip()
    for prefix in WAKE_PREFIXES:
        if trimmed.startswith(prefix):
            remainder = trimmed[len(prefix) :].lstrip(" ,.:;!?-")
            return remainder
    return trimmed


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


def start_press_hold_dictation() -> int:
    if DICTATE_STATE_PATH.exists():
        LOGGER.info("Voice hotkey source=dictate_start detected existing active state; preempting old dictation")
        stop_press_hold_dictation()

    language = get_saved_dictation_language()
    tmpdir = tempfile.mkdtemp(prefix="voice-dictate-hold-")
    audio_path = Path(tmpdir) / "capture.wav"

    cmd = [
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

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    except FileNotFoundError:
        notify("Voice", "ffmpeg not found")
        LOGGER.error("Could not start dictation recorder: ffmpeg not found")
        return 1
    state = {
        "pid": proc.pid,
        "tmpdir": tmpdir,
        "audio_path": str(audio_path),
        "pid_required_substrings": ["ffmpeg", str(audio_path)],
        "language": language,
        "started_at": time.time(),
    }
    write_private_text(DICTATE_STATE_PATH, json.dumps(state))
    notify("Voice", f"Recording... release keys to transcribe ({language})")
    LOGGER.info("Voice hotkey dictate_start pid=%s language=%s audio=%s", proc.pid, language, audio_path)
    return 0


def start_press_hold_command() -> int:
    if COMMAND_STATE_PATH.exists():
        LOGGER.info("Voice hotkey source=command_start detected existing active state; preempting old command")
        stop_press_hold_command()

    tmpdir = tempfile.mkdtemp(prefix="voice-command-hold-")
    audio_path = Path(tmpdir) / "capture.wav"
    language = get_saved_dictation_language()

    cmd = [
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

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    except FileNotFoundError:
        notify("Voice", "ffmpeg not found")
        LOGGER.error("Could not start command recorder: ffmpeg not found")
        return 1
    state = {
        "pid": proc.pid,
        "tmpdir": tmpdir,
        "audio_path": str(audio_path),
        "pid_required_substrings": ["ffmpeg", str(audio_path)],
        "language": language,
        "started_at": time.time(),
    }
    write_private_text(COMMAND_STATE_PATH, json.dumps(state))
    notify("Voice", f"Listening for command ({language})... release keys to run")
    LOGGER.info("Voice hotkey command_start pid=%s language=%s audio=%s", proc.pid, language, audio_path)
    return 0


def stop_press_hold_dictation() -> int:
    if not DICTATE_STATE_PATH.exists():
        LOGGER.info("Voice hotkey end status=no_active_dictation source=dictate_stop")
        notify("Voice", "No active dictation")
        return 0

    try:
        state = json.loads(DICTATE_STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.error("Failed to parse dictation state: %s", exc)
        DICTATE_STATE_PATH.unlink(missing_ok=True)
        return 1

    pid = int(state.get("pid", 0))
    audio_path = Path(state.get("audio_path", ""))
    tmpdir = Path(state.get("tmpdir", ""))
    language = state.get("language", get_saved_dictation_language())
    required_substrings = _state_required_substrings(state)
    started_at = state.get("started_at")

    notify("Voice", "Key released. Processing dictation...")

    if pid > 0:
        if _is_state_stale(started_at):
            LOGGER.warning(
                "Skipping stale dictation recorder stop pid=%s started_at=%s max_age=%ss",
                pid,
                started_at,
                STATE_MAX_AGE_SECONDS,
            )
        else:
            stop_recording_pid(pid, "Dictation ffmpeg", required_substrings=required_substrings)

    deadline = time.time() + 2.0
    while time.time() < deadline:
        if audio_path.exists() and audio_path.stat().st_size > 0:
            break
        time.sleep(0.05)

    try:
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            notify("Voice", "No speech captured")
            LOGGER.info("Voice hotkey end status=no_speech source=dictate_hold")
            return 0

        selected_dictation_model = dictation_model_name(language)
        if not is_model_loaded(selected_dictation_model):
            LOGGER.info("Dictation model not yet cached model=%s", selected_dictation_model)

        text, detected_language, language_probability = transcribe(audio_path, language=language, mode="dictate")
        spoken = text.strip()
        probability = language_probability if language_probability is not None else 0.0
        LOGGER.info(
            "Dictation hold language_selected=%s language_detected=%s probability=%.3f text=%s",
            language,
            detected_language,
            probability,
            _sanitize_transcript(spoken),
        )

        if not spoken:
            notify("Voice", "No speech detected")
            LOGGER.info("Voice hotkey end status=no_speech source=dictate_hold")
            return 0

        if inject_text_into_focused_input(spoken):
            notify("Voice", "Dictation pasted")
            LOGGER.info("Voice hotkey end status=ok source=dictate_hold text=%s", _sanitize_transcript(spoken))
            return 0

        notify("Voice", "Dictation paste failed")
        LOGGER.info("Voice hotkey end status=paste_failed source=dictate_hold text=%s", _sanitize_transcript(spoken))
        return 1
    finally:
        DICTATE_STATE_PATH.unlink(missing_ok=True)
        if tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


def stop_press_hold_command() -> int:
    if not COMMAND_STATE_PATH.exists():
        LOGGER.info("Voice hotkey end status=no_active_command source=command_stop")
        notify("Voice", "No active voice command")
        return 0

    try:
        state = json.loads(COMMAND_STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.error("Failed to parse command state: %s", exc)
        COMMAND_STATE_PATH.unlink(missing_ok=True)
        return 1

    pid = int(state.get("pid", 0))
    audio_path = Path(state.get("audio_path", ""))
    tmpdir = Path(state.get("tmpdir", ""))
    language = state.get("language", get_saved_dictation_language())
    required_substrings = _state_required_substrings(state)
    started_at = state.get("started_at")

    notify("Voice", "Key released. Processing command...")

    if pid > 0:
        if _is_state_stale(started_at):
            LOGGER.warning(
                "Skipping stale command recorder stop pid=%s started_at=%s max_age=%ss",
                pid,
                started_at,
                STATE_MAX_AGE_SECONDS,
            )
        else:
            stop_recording_pid(pid, "Command ffmpeg", required_substrings=required_substrings)

    deadline = time.time() + 2.0
    while time.time() < deadline:
        if audio_path.exists() and audio_path.stat().st_size > 0:
            break
        time.sleep(0.05)

    try:
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            notify("Voice", "No speech captured")
            LOGGER.info("Voice hotkey end status=no_speech source=voice_hold")
            return 0

        try:
            text, detected_language, language_probability = transcribe(audio_path, language=language, mode="command")
        except Exception as exc:
            notify("Voice", f"Transcription failed: {type(exc).__name__}")
            LOGGER.exception("Command hold transcription failed: %s", exc)
            LOGGER.info("Voice hotkey end status=transcription_failed source=voice_hold")
            return 1

        return handle_command_text(
            text,
            source="voice_hold",
            language=detected_language,
            language_probability=language_probability,
        )
    finally:
        COMMAND_STATE_PATH.unlink(missing_ok=True)
        if tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


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

        if not spoken:
            notify("Voice", "No speech detected")
            LOGGER.info("Voice hotkey end status=no_speech source=dictate")
            return 0

        if inject_text_into_focused_input(spoken):
            notify("Voice", "Dictation pasted")
            LOGGER.info("Voice hotkey end status=ok source=dictate text=%s", _sanitize_transcript(spoken))
            return 0

        notify("Voice", "Dictation paste failed")
        LOGGER.info("Voice hotkey end status=paste_failed source=dictate text=%s", _sanitize_transcript(spoken))
        return 1


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
            "Wake start triggered session_max=%s start_timeout_ms=%s vad_threshold=%s vad_min_speech_ms=%s vad_end_silence_ms=%s",
            WAKE_SESSION_MAX_SECONDS,
            WAKE_START_SPEECH_TIMEOUT_MS,
            WAKE_VAD_RMS_THRESHOLD,
            WAKE_VAD_MIN_SPEECH_MS,
            WAKE_VAD_END_SILENCE_MS,
        )
        _say_wake_greeting()
        return run_endpointed_command_session(
            language=get_saved_dictation_language(),
            source="wake_start",
            command_handler=handle_command_text,
            max_seconds=WAKE_SESSION_MAX_SECONDS,
            start_speech_timeout_ms=WAKE_START_SPEECH_TIMEOUT_MS,
            vad_rms_threshold=WAKE_VAD_RMS_THRESHOLD,
            vad_min_speech_ms=WAKE_VAD_MIN_SPEECH_MS,
            vad_end_silence_ms=WAKE_VAD_END_SILENCE_MS,
            prompt_text="Wake heard, speak command...",
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
                        request = _recv_json_line(conn)
                        input_mode = request.get("input", "voice")
                        if input_mode not in ALLOWED_INPUT_MODES:
                            LOGGER.warning("Rejected invalid daemon input=%r", input_mode)
                            rc = 2
                        else:
                            rc = handle_input(input_mode)
                    except Exception as exc:
                        LOGGER.exception("Voice daemon request failed: %s", exc)
                        rc = 1

                    try:
                        conn.sendall((json.dumps({"rc": rc}) + "\n").encode("utf-8"))
                    except Exception:
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
