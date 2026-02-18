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
    MODEL_NAME,
    DICTATE_STATE_PATH,
    LOCK_PATH,
    LOG_TRANSCRIPTS,
    SOCKET_PATH,
    STATE_MAX_AGE_SECONDS,
    VENV_PYTHON,
)
from .integrations import (
    inject_text_into_focused_input,
    notify,
    run_command,
)
from .logging_utils import LOGGER
from .state_utils import (
    is_capture_state_active_payload,
    state_required_substrings,
    write_private_text,
)
from .stt import preload_models, transcribe, warm_model


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
    if not shutil.which("ffmpeg"):
        LOGGER.error("Missing required tool: ffmpeg")
        notify("Voice", "Missing required tool: ffmpeg")
        return False

    for tool in ("hyprctl", "wl-copy", "notify-send"):
        if not shutil.which(tool):
            LOGGER.warning("Missing optional tool: %s", tool)

    return True


def _wait_for_captured_audio(audio_path: Path, timeout_seconds: float = 2.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if audio_path.exists() and audio_path.stat().st_size > 0:
            break
        time.sleep(0.05)


# -- Press/hold session helpers ------------------------------------------------

def _start_session(state_path: Path, preempt_fn: Callable[[], int], mode: str) -> int:
    if state_path.exists():
        LOGGER.info("Preempting existing %s session", mode)
        preempt_fn()

    tmpdir = tempfile.mkdtemp(prefix=f"voice-{mode}-hold-")
    audio_path = Path(tmpdir) / "capture.wav"

    try:
        proc = subprocess.Popen(
            build_ffmpeg_wav_capture_cmd(audio_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        notify("Voice", "ffmpeg not found")
        LOGGER.error("Could not start %s recorder: ffmpeg not found", mode)
        return 1

    state = {
        "pid": proc.pid,
        "tmpdir": tmpdir,
        "audio_path": str(audio_path),
        "pid_required_substrings": ["ffmpeg", str(audio_path)],
        "started_at": time.time(),
    }
    write_private_text(state_path, json.dumps(state))
    notify("Voice", f"Recording {mode}... release keys to process (en)")
    LOGGER.info("Voice hotkey %s_start pid=%s audio=%s", mode, proc.pid, audio_path)
    return 0


def _stop_session(
    state_path: Path,
    mode: str,
    on_transcription: Callable[[str, str | None, float | None], int],
) -> int:
    if not state_path.exists():
        LOGGER.info("Voice hotkey end status=no_active_%s", mode)
        notify("Voice", f"No active {mode}")
        return 0

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.error("Failed to parse %s state: %s", mode, exc)
        state_path.unlink(missing_ok=True)
        return 1

    pid = int(state.get("pid", 0))
    audio_path = Path(state.get("audio_path", ""))
    tmpdir = Path(state.get("tmpdir", ""))
    required_substrings = state_required_substrings(state)
    started_at = state.get("started_at")

    notify("Voice", f"Key released. Processing {mode}...")

    # Stop recorder if still active
    if pid > 0:
        capture_state = {"pid": pid, "started_at": started_at, "pid_required_substrings": required_substrings}
        if is_capture_state_active_payload(capture_state):
            stop_recording_pid(pid, f"{mode} ffmpeg", required_substrings=required_substrings)
        else:
            LOGGER.warning("Skipping inactive %s recorder pid=%s max_age=%ss", mode, pid, STATE_MAX_AGE_SECONDS)

    _wait_for_captured_audio(audio_path)

    try:
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            notify("Voice", "No speech captured")
            LOGGER.info("Voice hotkey end status=no_speech source=%s", mode)
            return 0

        try:
            text, detected_language, language_probability = transcribe(audio_path, language="en", mode=mode)
        except Exception as exc:
            notify("Voice", f"Transcription failed: {type(exc).__name__}")
            LOGGER.exception("%s transcription failed: %s", mode, exc)
            return 1

        return on_transcription(text, detected_language, language_probability)
    finally:
        state_path.unlink(missing_ok=True)
        if tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


# -- Dictation ----------------------------------------------------------------

def start_press_hold_dictation() -> int:
    return _start_session(DICTATE_STATE_PATH, stop_press_hold_dictation, "dictate")


def stop_press_hold_dictation() -> int:
    def _on_transcription(text: str, detected_language: str | None, language_probability: float | None) -> int:
        spoken = text.strip()
        probability = language_probability if language_probability is not None else 0.0
        LOGGER.info(
            "Dictation hold language_detected=%s probability=%.3f text=%s",
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

    return _stop_session(DICTATE_STATE_PATH, "dictate", _on_transcription)


# -- Command -------------------------------------------------------------------

def start_press_hold_command() -> int:
    return _start_session(COMMAND_STATE_PATH, stop_press_hold_command, "command")


def stop_press_hold_command() -> int:
    return _stop_session(COMMAND_STATE_PATH, "command", handle_command_text)


HOLD_INPUT_HANDLERS: dict[str, Callable[[], int]] = {
    "dictate-start": start_press_hold_dictation,
    "dictate-stop": stop_press_hold_dictation,
    "command-start": start_press_hold_command,
    "command-stop": stop_press_hold_command,
}


def handle_command_text(raw_text: str, language: str | None, language_probability: float | None) -> int:
    clean = normalize(raw_text)
    probability = language_probability if language_probability is not None else 0.0
    LOGGER.info(
        "Input language=%s probability=%.3f raw=%s normalized=%s",
        language,
        probability,
        _sanitize_transcript(raw_text),
        _sanitize_transcript(clean),
    )

    if not clean:
        notify("Voice", "No command detected")
        LOGGER.info("Voice hotkey end status=no_input source=command")
        return 0

    argv, label = match_command(clean)
    if not argv:
        notify("Voice", f"Heard: '{clean}' (no match)")
        LOGGER.info("Voice hotkey end status=no_match heard=%s", _sanitize_transcript(clean))
        return 0

    ok = run_command(argv)
    if ok:
        notify("Voice", f"Heard: '{clean}' -> {label}")
        LOGGER.info("Voice hotkey end status=ok heard=%s action=%s", _sanitize_transcript(clean), label)
        return 0

    notify("Voice", f"Command failed: {label}")
    LOGGER.info("Voice hotkey end status=command_failed heard=%s action=%s", _sanitize_transcript(clean), label)
    return 1


# -- CLI and daemon ------------------------------------------------------------

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


def _execute_daemon_request(request: dict) -> int:
    request_id = next(DAEMON_REQUEST_IDS)
    started_at = time.time()
    input_mode = request.get("input", "command-start")
    if input_mode not in ALLOWED_INPUT_MODES:
        LOGGER.warning("Rejected invalid daemon input=%r request_id=%s", input_mode, request_id)
        return 2

    LOGGER.info("Voice daemon request start id=%s input=%s", request_id, input_mode)

    try:
        rc = handle_input(input_mode)
    except Exception as exc:
        elapsed_ms = int((time.time() - started_at) * 1000)
        LOGGER.exception("Voice daemon request failed id=%s input=%s duration_ms=%s: %s", request_id, input_mode, elapsed_ms, exc)
        return 1

    elapsed_ms = int((time.time() - started_at) * 1000)
    LOGGER.info("Voice daemon request end id=%s input=%s rc=%s duration_ms=%s", request_id, input_mode, rc, elapsed_ms)
    return rc


def _handle_daemon_connection(conn: socket.socket) -> None:
    with conn:
        try:
            conn.settimeout(DAEMON_CONNECT_TIMEOUT)
            request = _recv_json_line(conn)
        except (socket.timeout, UnicodeDecodeError, json.JSONDecodeError, ValueError, OSError) as exc:
            LOGGER.warning("Voice daemon request parse failed: %s", exc)
            request = None

        rc = _execute_daemon_request(request) if request is not None else 1
        try:
            conn.sendall((json.dumps({"rc": rc}) + "\n").encode("utf-8"))
        except OSError as exc:
            LOGGER.debug("Voice daemon response send failed rc=%s err=%s", rc, exc)


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
    threading.Thread(target=warm_model, args=(MODEL_NAME,), daemon=True).start()

    LOGGER.info("Voice hotkey daemon listening socket=%s", SOCKET_PATH)

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(SOCKET_PATH))
            try:
                SOCKET_PATH.chmod(0o600)
            except Exception as exc:
                LOGGER.warning("Could not chmod daemon socket: %s", exc)
            server.listen(8)

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
