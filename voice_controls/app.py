import argparse
import fcntl
import itertools
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .audio import build_ffmpeg_wav_capture_cmd, stop_recording_pid
from .commands import match_command, normalize
from .config import (
    COMMAND_STATE_PATH,
    DAEMON_CONNECT_TIMEOUT,
    DAEMON_MAX_REQUEST_BYTES,
    DAEMON_RESPONSE_TIMEOUT,
    DAEMON_START_DELAY,
    DAEMON_START_RETRIES,
    DICTATE_STATE_PATH,
    LOCK_PATH,
    LOG_TRANSCRIPTS,
    SOCKET_PATH,
    STATE_MAX_AGE_SECONDS,
    VENV_PYTHON,
    DICTATION_INJECTOR,
)
from .integrations import (
    inject_text_into_focused_input,
    notify,
    run_command,
)
from .logging_utils import LOGGER
from .state_utils import (
    get_saved_dictation_language,
    is_capture_state_active_payload,
    state_required_substrings,
    write_private_text,
)
from .stt import dictation_model_name, is_model_loaded, preload_models, transcribe, warm_model


ALLOWED_INPUT_MODES = {"dictate-start", "dictate-stop", "command-start", "command-stop"}

DAEMON_REQUEST_IDS = itertools.count(1)


def _sanitize_transcript(value: str) -> str:
    if LOG_TRANSCRIPTS:
        return repr(value)
    return f"<redacted len={len(value)}>"


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


HOLD_INPUT_HANDLERS: dict[str, Callable[[], int]] = {
    "dictate-start": start_press_hold_dictation,
    "dictate-stop": stop_press_hold_dictation,
    "command-start": start_press_hold_command,
    "command-stop": stop_press_hold_command,
}


def handle_command_text(raw_text: str, source: str, language: str | None, language_probability: float | None) -> int:
    clean = normalize(raw_text)
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
    parser.add_argument("--input", default="command-start", help="Input action")
    parser.add_argument("--daemon", action="store_true")
    return parser.parse_args()


def handle_input(input_mode: str) -> int:
    if input_mode not in ALLOWED_INPUT_MODES:
        LOGGER.warning("Rejected unsupported input mode: %r", input_mode)
        return 2
    return HOLD_INPUT_HANDLERS[input_mode]()


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


def request_daemon(input_mode: str, *, entry_script: Path | None = None) -> int:
    payload = json.dumps({"input": input_mode}).encode("utf-8") + b"\n"

    for attempt in range(DAEMON_START_RETRIES):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(DAEMON_CONNECT_TIMEOUT)
                client.connect(str(SOCKET_PATH))
                client.settimeout(DAEMON_RESPONSE_TIMEOUT)
                client.sendall(payload)
                data = _recv_json_line(client)
            rc_value = data.get("rc", 1)
            return int(rc_value) if isinstance(rc_value, (int, float, str)) else 1
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, json.JSONDecodeError, ValueError, OSError):
            if attempt == 0:
                start_daemon(entry_script=entry_script)
            if attempt < DAEMON_START_RETRIES - 1:
                time.sleep(DAEMON_START_DELAY)

    LOGGER.error("Could not reach voice-hotkey daemon after retries")
    notify("Voice", "Voice daemon unavailable")
    return 1


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
    input_mode = request.get("input", "command-start")
    if input_mode not in ALLOWED_INPUT_MODES:
        LOGGER.warning(
            "Rejected invalid daemon input=%r request_id=%s",
            input_mode,
            request_id,
        )
        return 2

    LOGGER.info(
        "Voice daemon request start id=%s input=%s",
        request_id,
        input_mode,
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
        "Voice daemon request end id=%s input=%s rc=%s duration_ms=%s",
        request_id,
        input_mode,
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

    LOGGER.info("Voice hotkey runtime mode=hold-only")

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

            while True:
                conn, _ = server.accept()
                _handle_daemon_connection(conn)
    finally:
        lock_handle.close()
    return 0


def main(entry_script: Path | None = None) -> int:
    args = parse_args()

    if args.daemon:
        return run_daemon()

    return request_daemon(args.input, entry_script=entry_script)
