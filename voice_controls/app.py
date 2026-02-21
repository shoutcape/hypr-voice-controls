"""Responsibility: Orchestrate dictation hotkey sessions and daemon IPC."""

import argparse  # Parse CLI flags like --input and --daemon.
import fcntl  # Use advisory file locks to keep one daemon instance.
import io  # Identify file-like objects for safe close() calls.
import itertools  # Provide monotonic request IDs via count().
import json  # Encode/decode daemon request and response payloads.
import os  # Read and extend environment variables when spawning daemon.
import shutil  # Check required/optional external tools with shutil.which.
import signal  # Install handlers for SIGTERM/SIGINT to enable clean daemon shutdown.
import socket  # Handle local UNIX socket client/server communication.
import subprocess  # Start ffmpeg capture and background daemon processes.
import sys  # Access current Python executable as runtime fallback.
import tempfile  # Create temporary directories for per-hold recordings.
import time  # Measure durations and implement retry/backoff timing.
from pathlib import Path  # Build filesystem paths safely and clearly.
from typing import Callable  # Type hint daemon input handlers.

from .audio import build_ffmpeg_wav_capture_cmd, pid_alive, stop_recording_pid  # Audio capture command and recorder lifecycle helpers.
from .config import (  # Central runtime constants and tunables.
    DAEMON_CONNECT_TIMEOUT,
    DAEMON_MAX_REQUEST_BYTES,
    DAEMON_RESPONSE_TIMEOUT,
    DAEMON_START_DELAY,
    DAEMON_START_RETRIES,
    DICTATE_STATE_PATH,
    LOCK_PATH,
    LOG_PATH,
    LOG_TRANSCRIPTS,
    SOCKET_PATH,
    STATE_MAX_AGE_SECONDS,
    VENV_PYTHON,
)
from .integrations import (  # Desktop side effects (notify and paste).
    inject_text_into_focused_input,
    notify,
)
from .logging_utils import LOGGER  # Shared file-backed logger.
from .state_utils import write_private_text  # Atomic private state file writes.
from .stt import preload_models, transcribe  # Speech-to-Text model preload and transcription.


DAEMON_REQUEST_IDS = itertools.count(1)

# Tracks live Popen objects for ffmpeg capture processes so the daemon can
# reap them after signaling, preventing zombie accumulation.
_ACTIVE_CAPTURE_PROCS: dict[int, subprocess.Popen] = {}


def _reap_capture_proc(pid: int) -> None:
    """Wait for a tracked ffmpeg Popen to exit, preventing zombie processes."""
    proc = _ACTIVE_CAPTURE_PROCS.pop(pid, None)
    if proc is None:
        return
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _sanitize_transcript(value: str) -> str:
    """Return transcript text or a redacted marker based on log policy."""
    if LOG_TRANSCRIPTS:
        return repr(value)
    return f"<redacted len={len(value)}>"


def _recv_json_line(sock: socket.socket, wall_deadline: float | None = None) -> dict:
    """Read exactly one newline-terminated JSON line from sock.

    Caller MUST call sock.settimeout() before invoking this function to
    bound individual recv() calls.  The optional ``wall_deadline`` argument
    (a ``time.time()`` value) additionally caps the total wall-clock time
    spent in this function, preventing a slow-drip sender from resetting the
    per-recv timeout indefinitely and blocking the daemon for hours.

    Only bytes up to and including the first newline count toward the
    DAEMON_MAX_REQUEST_BYTES limit, so trailing data after the newline
    (e.g. from a misbehaving client) never causes a spurious rejection.
    """
    raw = bytearray()
    total = 0
    while True:
        if wall_deadline is not None and time.time() > wall_deadline:
            raise ValueError("wall_clock_timeout")
        block = sock.recv(1024)
        if not block:
            break
        if b"\n" in block:
            # Only count/keep bytes up to and including the newline.
            idx = block.index(b"\n")
            chunk = block[: idx + 1]
            total += len(chunk)
            if total > DAEMON_MAX_REQUEST_BYTES:
                raise ValueError("request_too_large")
            raw.extend(chunk)
            break
        total += len(block)
        if total > DAEMON_MAX_REQUEST_BYTES:
            raise ValueError("request_too_large")
        raw.extend(block)

    if not raw:
        raise ValueError("empty_request")

    line = raw.split(b"\n", 1)[0].strip()
    if not line:
        raise ValueError("empty_request")
    return json.loads(line.decode("utf-8"))


def validate_environment() -> bool:
    """Verify required binaries exist and log warnings for optional tools."""
    if not shutil.which("ffmpeg"):
        LOGGER.error("Missing required tool: ffmpeg")
        notify("Voice", "Missing required tool: ffmpeg")
        return False

    for tool in ("hyprctl", "wl-copy", "notify-send"):
        if not shutil.which(tool):
            LOGGER.warning("Missing optional tool: %s", tool)

    return True


def _wait_for_captured_audio(audio_path: Path, timeout_seconds: float = 2.0) -> None:
    """Poll briefly until ffmpeg has written non-empty audio data."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            if audio_path.stat().st_size > 0:
                break
        except FileNotFoundError:
            pass
        except OSError:
            pass
        time.sleep(0.05)


# -- Press/hold session helpers ------------------------------------------------

def _start_session() -> int:
    """Start a press-and-hold dictation capture and persist session state."""
    if DICTATE_STATE_PATH.exists():
        LOGGER.info("Preempting existing dictate session")
        preempt_rc = _stop_session()
        if preempt_rc != 0:
            LOGGER.warning("Previous session cleanup returned rc=%s; starting new session anyway", preempt_rc)

    tmpdir = tempfile.mkdtemp(prefix="voice-dictate-hold-")
    audio_path = Path(tmpdir) / "capture.wav"

    try:
        proc = subprocess.Popen(
            build_ffmpeg_wav_capture_cmd(audio_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        shutil.rmtree(tmpdir, ignore_errors=True)
        notify("Voice", "ffmpeg not found")
        LOGGER.error("Could not start dictate recorder: ffmpeg not found")
        return 1

    _ACTIVE_CAPTURE_PROCS[proc.pid] = proc

    state = {
        "pid": proc.pid,
        "tmpdir": tmpdir,
        "audio_path": str(audio_path),
        "pid_required_substrings": ["ffmpeg", str(audio_path)],
        "started_at": time.time(),
    }
    try:
        write_private_text(DICTATE_STATE_PATH, json.dumps(state))
    except Exception as exc:
        LOGGER.error("Failed to write dictate state; aborting session: %s", exc)
        stop_recording_pid(proc.pid, "dictate ffmpeg", required_substrings=["ffmpeg", str(audio_path)])
        _reap_capture_proc(proc.pid)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return 1

    notify("Voice", "Recording dictate... release keys to process (en)")
    LOGGER.info("Voice hotkey dictate_start pid=%s audio=%s", proc.pid, audio_path)
    return 0


def _stop_session() -> int:
    """Stop active capture, transcribe audio, paste text, and clean up state."""
    # Use try/except instead of exists()-then-read to avoid a TOCTOU race
    # where the file is removed between the check and the read.
    try:
        state = json.loads(DICTATE_STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        LOGGER.info("Voice hotkey end status=no_active_dictate")
        notify("Voice", "No active dictate")
        return 0
    except Exception as exc:
        LOGGER.error("Failed to parse dictate state: %s", exc)
        DICTATE_STATE_PATH.unlink(missing_ok=True)
        return 1

    pid = int(state.get("pid", 0))

    # Validate paths before use: missing or non-temp paths could point to
    # arbitrary filesystem locations (e.g. Path("") resolves to CWD).
    audio_path_str = state.get("audio_path", "")
    tmpdir_str = state.get("tmpdir", "")
    if not audio_path_str or not tmpdir_str:
        LOGGER.error("Dictate state missing required path fields; aborting cleanup")
        DICTATE_STATE_PATH.unlink(missing_ok=True)
        return 1
    # Resolve both paths before comparison so symlinks and ".." components
    # cannot bypass the prefix check (e.g. "/tmp/../etc" → "/etc").
    # The trailing separator ensures "/tmp-evil" is not accepted as a child
    # of "/tmp".
    tmp_prefix = str(Path(tempfile.gettempdir()).resolve()) + "/"
    tmpdir_resolved = str(Path(tmpdir_str).resolve()) + "/"
    if not tmpdir_resolved.startswith(tmp_prefix):
        LOGGER.error("Refusing to remove non-temp dictate directory tmpdir=%s", tmpdir_str)
        DICTATE_STATE_PATH.unlink(missing_ok=True)
        return 1
    audio_path = Path(audio_path_str)
    tmpdir = Path(tmpdir_str)
    audio_path_resolved = str(audio_path.resolve())
    if not (audio_path_resolved + "/").startswith(tmpdir_resolved):
        LOGGER.error(
            "Refusing to use audio path outside dictate tmpdir audio=%s tmpdir=%s",
            audio_path,
            tmpdir,
        )
        DICTATE_STATE_PATH.unlink(missing_ok=True)
        return 1

    raw_required_substrings = state.get("pid_required_substrings")
    if isinstance(raw_required_substrings, list):
        required_substrings = [token for token in raw_required_substrings if isinstance(token, str) and token.strip()]
    else:
        required_substrings = []
    if not required_substrings:
        required_substrings = ["ffmpeg"]
    started_at = state.get("started_at")

    notify("Voice", "Key released. Processing dictate...")

    # Stop recorder if still active
    if pid > 0:
        active_recorder = pid_alive(pid)
        if active_recorder and isinstance(started_at, (int, float)):
            active_recorder = (time.time() - float(started_at)) <= STATE_MAX_AGE_SECONDS

        if active_recorder:
            stop_recording_pid(pid, "dictate ffmpeg", required_substrings=required_substrings)
        else:
            LOGGER.warning("Skipping inactive dictate recorder pid=%s max_age=%ss", pid, STATE_MAX_AGE_SECONDS)

        # Reap the child process to prevent zombie accumulation.
        _reap_capture_proc(pid)

    _wait_for_captured_audio(audio_path)

    try:
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            notify("Voice", "No speech captured")
            LOGGER.info("Voice hotkey end status=no_speech source=dictate")
            return 0

        try:
            text, detected_language, language_probability = transcribe(audio_path, language="en")
        except Exception as exc:
            notify("Voice", f"Transcription failed: {type(exc).__name__}")
            LOGGER.exception("dictate transcription failed: %s", exc)
            return 1

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
    finally:
        DICTATE_STATE_PATH.unlink(missing_ok=True)
        if tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


# -- Dictation ----------------------------------------------------------------

def start_press_hold_dictation() -> int:
    """Public handler: begin press-and-hold dictation recording."""
    return _start_session()


def stop_press_hold_dictation() -> int:
    """Public handler: finish recording and process captured speech."""
    return _stop_session()


HOLD_INPUT_HANDLERS: dict[str, Callable[[], int]] = {
    "dictate-start": start_press_hold_dictation,
    "dictate-stop": stop_press_hold_dictation,
}


# -- CLI and daemon ------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse CLI options for one-shot client mode or daemon mode."""
    parser = argparse.ArgumentParser(description="Voice dictation hotkey runner")
    parser.add_argument("--input", default="dictate-start", choices=sorted(HOLD_INPUT_HANDLERS), help="Input action")
    parser.add_argument("--daemon", action="store_true")
    return parser.parse_args()


def start_daemon() -> subprocess.Popen | None:
    """Spawn the daemon as a detached background process.

    Returns the Popen object so the caller can detect an immediate crash via
    Popen.poll(); returns None if the process could not be spawned at all.
    Since start_new_session=True is used, the child is adopted by init on
    detach and will not become a zombie of this process.
    """
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    runtime_python = str(VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable))
    repo_root = str(Path(__file__).resolve().parents[1])
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = repo_root if not current_pythonpath else f"{repo_root}:{current_pythonpath}"

    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        stderr_target = LOG_PATH.open("a")
    except OSError as exc:
        LOGGER.warning("Could not open log file for daemon stderr; using DEVNULL: %s", exc)
        stderr_target = subprocess.DEVNULL

    try:
        proc = subprocess.Popen(
            [runtime_python, "-m", "voice_controls", "--daemon"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr_target,
            start_new_session=True,
            env=env,
        )
        return proc
    except Exception as exc:
        LOGGER.error("Could not start daemon process: %s", exc)
        return None
    finally:
        if isinstance(stderr_target, io.IOBase):
            stderr_target.close()


def request_daemon(input_mode: str) -> int:
    """Send one action request to the daemon, auto-starting it if needed."""
    payload = json.dumps({"input": input_mode}).encode("utf-8") + b"\n"

    daemon_proc: subprocess.Popen | None = None

    for attempt in range(DAEMON_START_RETRIES):
        # If the daemon process we spawned has already exited, fail fast
        # rather than retrying until the retry limit is exhausted.
        if daemon_proc is not None and daemon_proc.poll() is not None:
            LOGGER.error(
                "Voice daemon process exited immediately rc=%s; not retrying",
                daemon_proc.returncode,
            )
            notify("Voice", "Voice daemon unavailable")
            return 1

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(DAEMON_CONNECT_TIMEOUT)
                client.connect(str(SOCKET_PATH))
                client.settimeout(DAEMON_RESPONSE_TIMEOUT)
                client.sendall(payload)
                data = _recv_json_line(client)
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, json.JSONDecodeError, ValueError, OSError):
            if attempt == 0:
                daemon_proc = start_daemon()
                if daemon_proc is None:
                    LOGGER.error("Could not start voice-hotkey daemon process")
                    notify("Voice", "Voice daemon unavailable")
                    return 1
                # Give the daemon a longer head-start on the first retry so it
                # has time to import and preload models before we poll again.
                time.sleep(DAEMON_START_DELAY * 5)
            elif attempt < DAEMON_START_RETRIES - 1:
                time.sleep(DAEMON_START_DELAY)
            continue

        # A response was received: parse rc and return immediately without
        # retrying. A bad rc value is the daemon's problem, not a transient
        # connection failure, so retrying would re-execute the request.
        rc_value = data.get("rc", 1)
        if isinstance(rc_value, (int, float)):
            return int(rc_value)
        try:
            return int(rc_value)
        except (ValueError, TypeError):
            LOGGER.warning("Invalid rc value from daemon: %r", rc_value)
            return 1

    LOGGER.error("Could not reach voice-hotkey daemon after retries")
    notify("Voice", "Voice daemon unavailable")
    return 1


def _execute_daemon_request(request: dict) -> int:
    """Validate daemon request payload and run the mapped input handler."""
    request_id = next(DAEMON_REQUEST_IDS)
    started_at = time.time()
    input_mode = request.get("input")
    if not isinstance(input_mode, str):
        LOGGER.warning(
            "Rejected daemon request with invalid input type=%s request_id=%s",
            type(input_mode).__name__,
            request_id,
        )
        return 2
    handler = HOLD_INPUT_HANDLERS.get(input_mode)
    if handler is None:
        LOGGER.warning("Rejected invalid daemon input=%r request_id=%s", input_mode, request_id)
        return 2

    LOGGER.info("Voice daemon request start id=%s input=%s", request_id, input_mode)

    try:
        rc = handler()
    except Exception as exc:
        elapsed_ms = int((time.time() - started_at) * 1000)
        LOGGER.exception("Voice daemon request failed id=%s input=%s duration_ms=%s: %s", request_id, input_mode, elapsed_ms, exc)
        return 1

    elapsed_ms = int((time.time() - started_at) * 1000)
    LOGGER.info("Voice daemon request end id=%s input=%s rc=%s duration_ms=%s", request_id, input_mode, rc, elapsed_ms)
    return rc


def _handle_daemon_connection(conn: socket.socket) -> None:
    """Process a single client socket: decode request, execute, return rc."""
    with conn:
        try:
            conn.settimeout(DAEMON_CONNECT_TIMEOUT)
            request = _recv_json_line(conn, wall_deadline=time.time() + DAEMON_CONNECT_TIMEOUT)
        except (socket.timeout, UnicodeDecodeError, json.JSONDecodeError, ValueError, OSError) as exc:
            LOGGER.warning("Voice daemon request parse failed: %s", exc)
            request = None

        rc = _execute_daemon_request(request) if request is not None else 1
        try:
            conn.sendall((json.dumps({"rc": rc}) + "\n").encode("utf-8"))
        except OSError as exc:
            LOGGER.debug("Voice daemon response send failed rc=%s err=%s", rc, exc)


def run_daemon() -> int:
    """Run single-instance UNIX-socket daemon loop for hotkey actions."""
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

    # Install signal handlers so SIGTERM (systemd stop) and SIGINT (Ctrl-C)
    # cause a clean exit rather than an unhandled exception with a traceback.
    _shutdown = False

    def _request_shutdown(signum: int, frame: object) -> None:
        nonlocal _shutdown
        _shutdown = True
        LOGGER.info("Voice hotkey daemon received signal %s; shutting down", signum)

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink(missing_ok=True)

    try:
        preload_models()
    except Exception as exc:
        # Notify the user immediately so they know transcription will fail,
        # rather than getting a silent error on every hotkey press. The daemon
        # continues running so it can still accept requests (the model may load
        # successfully on first transcription if the preload failure was transient).
        notify("Voice", f"Model preload failed: {type(exc).__name__}")

    LOGGER.info("Voice hotkey daemon listening socket=%s pid=%s", SOCKET_PATH, os.getpid())

    bound = False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            # Set a restrictive umask before bind() so the socket is created
            # with 0o600 permissions, eliminating the TOCTOU window between
            # bind() and the chmod() below where any local user could connect.
            old_umask = os.umask(0o177)
            try:
                server.bind(str(SOCKET_PATH))
                bound = True
            finally:
                os.umask(old_umask)
            try:
                SOCKET_PATH.chmod(0o600)
            except Exception as exc:
                LOGGER.warning("Could not chmod daemon socket: %s", exc)
            server.listen(8)
            # Allow accept() to be interrupted periodically so signal handlers
            # can check _shutdown and exit cleanly without waiting indefinitely.
            server.settimeout(1.0)

            # The accept loop is intentionally single-threaded: requests are
            # serialised to avoid concurrent ffmpeg captures and to prevent
            # parallel Whisper inference. Clients queue in the kernel backlog
            # (size 8) and are served in order. This means a slow transcription
            # blocks subsequent hotkey presses until it completes.
            while not _shutdown:
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    continue
                _handle_daemon_connection(conn)

            LOGGER.info("Voice hotkey daemon exiting cleanly")
    finally:
        if bound:
            SOCKET_PATH.unlink(missing_ok=True)
        lock_handle.close()
    return 0


def main() -> int:
    """Program entrypoint: dispatch to daemon server or client request path."""
    args = parse_args()

    if args.daemon:
        return run_daemon()

    return request_daemon(args.input)
