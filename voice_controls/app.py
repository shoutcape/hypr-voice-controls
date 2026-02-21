"""Responsibility: Orchestrate dictation hotkey sessions and daemon IPC."""

import argparse
import io
import itertools
import json
import os
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .audio import build_ffmpeg_wav_capture_cmd
from .config import (
    DAEMON_CONNECT_TIMEOUT,
    DAEMON_READY_TIMEOUT,
    DAEMON_RESPONSE_TIMEOUT,
    LOG_PATH,
    LOG_TRANSCRIPTS,
    SOCKET_PATH,
    VENV_PYTHON,
)
from .integrations import inject_text_into_focused_input, notify
from .logging_utils import LOGGER
from .stt import preload_models, transcribe


DAEMON_REQUEST_IDS = itertools.count(1)
IPC_MAX_LINE_BYTES = 128
STOP_WAIT_SIGINT_SECONDS = 1.5
STOP_WAIT_SIGTERM_SECONDS = 1.0
STOP_WAIT_SIGKILL_SECONDS = 0.5
DEPRECATED_ENV_VARS = (
    "VOICE_DAEMON_START_RETRIES",
    "VOICE_DAEMON_START_DELAY",
    "VOICE_DAEMON_MAX_REQUEST_BYTES",
    "VOICE_STATE_MAX_AGE_SECONDS",
)
RECOVERY_STATE_PATH = SOCKET_PATH.with_name("voice-hotkey-dictate-recovery.json")


@dataclass
class DictationSession:
    proc: subprocess.Popen
    tmpdir: Path
    audio_path: Path
    started_at: float


ACTIVE_SESSION: DictationSession | None = None
_DEPRECATED_ENV_WARNED = False


def _sanitize_transcript(value: str) -> str:
    if LOG_TRANSCRIPTS:
        return repr(value)
    return f"<redacted len={len(value)}>"


def _warn_deprecated_env_vars() -> None:
    global _DEPRECATED_ENV_WARNED
    if _DEPRECATED_ENV_WARNED:
        return
    for name in DEPRECATED_ENV_VARS:
        if name in os.environ:
            LOGGER.warning("Deprecated env var %s is set but ignored by current runtime", name)
    _DEPRECATED_ENV_WARNED = True


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


def _pid_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()


def _pid_matches_capture(pid: int, audio_path: Path) -> bool:
    cmdline = _pid_cmdline(pid).lower()
    if not cmdline:
        return False
    return "ffmpeg" in cmdline and str(audio_path).lower() in cmdline


def _write_recovery_state(session: DictationSession) -> None:
    payload = {
        "pid": session.proc.pid,
        "tmpdir": str(session.tmpdir),
        "audio_path": str(session.audio_path),
        "started_at": session.started_at,
    }
    RECOVERY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{RECOVERY_STATE_PATH.name}.",
        suffix=".tmp",
        dir=str(RECOVERY_STATE_PATH.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload))
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.chmod(0o600)
        os.replace(tmp_path, RECOVERY_STATE_PATH)
    finally:
        tmp_path.unlink(missing_ok=True)


def _clear_recovery_state() -> None:
    RECOVERY_STATE_PATH.unlink(missing_ok=True)


def _load_recovery_state() -> dict | None:
    if not RECOVERY_STATE_PATH.exists():
        return None
    try:
        payload = json.loads(RECOVERY_STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.warning("Could not parse recovery session state; dropping stale file: %s", exc)
        _clear_recovery_state()
        return None
    if not isinstance(payload, dict):
        _clear_recovery_state()
        return None
    return payload


def _cleanup_recovery_tmpdir(tmpdir_raw: str) -> None:
    if not tmpdir_raw:
        return
    tmpdir = Path(tmpdir_raw)
    try:
        tmpdir_resolved = tmpdir.resolve()
        base = Path(tempfile.gettempdir()).resolve()
        if tmpdir_resolved.parent != base:
            return
        if not tmpdir_resolved.name.startswith("voice-dictate-hold-"):
            return
    except OSError:
        return
    if tmpdir.exists():
        shutil.rmtree(tmpdir, ignore_errors=True)


def _stop_capture_pid(pid: int, audio_path: Path) -> None:
    if pid <= 0:
        return
    if not _pid_alive(pid):
        return
    if not _pid_matches_capture(pid, audio_path):
        LOGGER.warning("Refusing to signal recovery pid=%s due to cmdline mismatch", pid)
        return

    try:
        os.kill(pid, signal.SIGINT)
    except OSError:
        return

    deadline = time.time() + STOP_WAIT_SIGINT_SECONDS
    while time.time() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.05)

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return

    deadline = time.time() + STOP_WAIT_SIGTERM_SECONDS
    while time.time() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.05)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return


def _recv_line(sock: socket.socket, max_bytes: int = IPC_MAX_LINE_BYTES) -> str:
    raw = bytearray()
    while True:
        block = sock.recv(1024)
        if not block:
            break
        if b"\n" in block:
            idx = block.index(b"\n")
            raw.extend(block[: idx + 1])
            break
        raw.extend(block)
        if len(raw) > max_bytes:
            raise ValueError("request_too_large")

    if not raw:
        raise ValueError("empty_request")
    if len(raw) > max_bytes:
        raise ValueError("request_too_large")

    line = raw.split(b"\n", 1)[0].decode("utf-8").strip()
    if not line:
        raise ValueError("empty_request")
    return line


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


def _stop_capture_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return

    try:
        proc.send_signal(signal.SIGINT)
    except ProcessLookupError:
        return
    except OSError as exc:
        LOGGER.warning("Could not stop recorder with SIGINT: %s", exc)
    else:
        try:
            proc.wait(timeout=STOP_WAIT_SIGINT_SECONDS)
            return
        except subprocess.TimeoutExpired:
            LOGGER.warning("Recorder still alive after SIGINT; escalating to SIGTERM pid=%s", proc.pid)

    try:
        proc.terminate()
    except ProcessLookupError:
        return
    except OSError as exc:
        LOGGER.warning("Could not stop recorder with SIGTERM: %s", exc)
    else:
        try:
            proc.wait(timeout=STOP_WAIT_SIGTERM_SECONDS)
            return
        except subprocess.TimeoutExpired:
            LOGGER.warning("Recorder still alive after SIGTERM; escalating to SIGKILL pid=%s", proc.pid)

    try:
        proc.kill()
    except ProcessLookupError:
        return
    except OSError as exc:
        LOGGER.error("Could not SIGKILL recorder pid=%s: %s", proc.pid, exc)
        return

    try:
        proc.wait(timeout=STOP_WAIT_SIGKILL_SECONDS)
    except subprocess.TimeoutExpired:
        LOGGER.error("Recorder still alive after SIGKILL pid=%s", proc.pid)


def _process_captured_audio(audio_path: Path) -> int:
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


# -- Press/hold session helpers ------------------------------------------------

def _start_session() -> int:
    """Start a press-and-hold dictation capture in daemon memory."""
    global ACTIVE_SESSION

    if ACTIVE_SESSION is not None:
        LOGGER.info("Preempting existing dictate session")
        preempt_rc = _stop_session()
        if preempt_rc != 0:
            LOGGER.warning("Previous session cleanup returned rc=%s; starting new session anyway", preempt_rc)

    tmpdir = Path(tempfile.mkdtemp(prefix="voice-dictate-hold-"))
    audio_path = tmpdir / "capture.wav"

    try:
        proc = subprocess.Popen(
            build_ffmpeg_wav_capture_cmd(audio_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        shutil.rmtree(str(tmpdir), ignore_errors=True)
        notify("Voice", "ffmpeg not found")
        LOGGER.error("Could not start dictate recorder: ffmpeg not found")
        return 1

    ACTIVE_SESSION = DictationSession(proc=proc, tmpdir=tmpdir, audio_path=audio_path, started_at=time.time())
    try:
        _write_recovery_state(ACTIVE_SESSION)
    except Exception as exc:
        LOGGER.error("Could not persist recovery session state; aborting recording: %s", exc)
        _stop_capture_process(proc)
        ACTIVE_SESSION = None
        shutil.rmtree(tmpdir, ignore_errors=True)
        notify("Voice", "Dictation start failed")
        return 1

    notify("Voice", "Recording dictate... release keys to process (en)")
    LOGGER.info("Voice hotkey dictate_start pid=%s audio=%s", proc.pid, audio_path)
    return 0


def _stop_session() -> int:
    """Stop active capture, transcribe audio, paste text, and clean up state."""
    global ACTIVE_SESSION

    session = ACTIVE_SESSION
    recovered = _load_recovery_state() if session is None else None
    if session is None and recovered is None:
        LOGGER.info("Voice hotkey end status=no_active_dictate")
        notify("Voice", "No active dictate")
        return 0

    if session is not None:
        ACTIVE_SESSION = None
        audio_path = session.audio_path
        tmpdir = session.tmpdir
        notify("Voice", "Key released. Processing dictate...")
        _stop_capture_process(session.proc)
    else:
        recovery_payload = recovered
        if recovery_payload is None:
            LOGGER.info("Voice hotkey end status=no_active_dictate")
            notify("Voice", "No active dictate")
            return 0
        try:
            audio_path_raw = str(recovery_payload.get("audio_path", ""))
            tmpdir_raw = str(recovery_payload.get("tmpdir", ""))
            pid = int(recovery_payload.get("pid", 0))
        except (TypeError, ValueError):
            _clear_recovery_state()
            LOGGER.info("Voice hotkey end status=no_active_dictate")
            notify("Voice", "No active dictate")
            return 0
        if not audio_path_raw or not tmpdir_raw:
            _clear_recovery_state()
            LOGGER.info("Voice hotkey end status=no_active_dictate")
            notify("Voice", "No active dictate")
            return 0
        audio_path = Path(audio_path_raw)
        tmpdir = Path(tmpdir_raw)
        notify("Voice", "Recovered previous session. Processing dictate...")
        _stop_capture_pid(pid, audio_path)

    try:
        return _process_captured_audio(audio_path)
    finally:
        _clear_recovery_state()
        _cleanup_recovery_tmpdir(str(tmpdir))


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
    """Spawn the daemon as a detached background process."""
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    runtime_python = str(VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable))
    repo_root = str(Path(__file__).resolve().parents[1])
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = repo_root if not current_pythonpath else f"{repo_root}:{current_pythonpath}"
    stderr_log_handle: io.TextIOBase | None = None
    stderr_target: int | io.TextIOBase

    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        stderr_log_handle = LOG_PATH.open("a")
        stderr_target = stderr_log_handle
    except OSError as exc:
        LOGGER.warning("Could not open log file for daemon stderr; using DEVNULL: %s", exc)
        stderr_target = subprocess.DEVNULL

    try:
        proc = subprocess.Popen(
            [runtime_python, "-m", "voice_controls", "--daemon"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=stderr_target,
            text=True,
            start_new_session=True,
            env=env,
        )
        return proc
    except Exception as exc:
        LOGGER.error("Could not start daemon process: %s", exc)
        return None
    finally:
        if stderr_log_handle is not None:
            stderr_log_handle.close()


def _wait_for_daemon_ready(proc: subprocess.Popen) -> bool:
    if proc.stdout is None:
        return False

    ready, _, _ = select.select([proc.stdout], [], [], DAEMON_READY_TIMEOUT)
    if not ready:
        LOGGER.error("Voice daemon did not report READY within timeout=%ss", DAEMON_READY_TIMEOUT)
        try:
            proc.terminate()
        except OSError:
            pass
        return False

    line = proc.stdout.readline()
    if line.strip() != "READY":
        LOGGER.error("Voice daemon sent unexpected startup marker: %r", line.strip())
        return False
    return True


def _parse_rc_line(line: str) -> int:
    stripped = line.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
            rc_value = payload.get("rc", 1)
        except Exception:
            rc_value = 1
        try:
            return int(rc_value)
        except (TypeError, ValueError):
            LOGGER.warning("Invalid rc value from daemon JSON payload: %r", rc_value)
            return 1

    if stripped in {"0", "1", "2"}:
        return int(stripped)
    try:
        return int(stripped)
    except (TypeError, ValueError):
        LOGGER.warning("Invalid rc value from daemon: %r", stripped)
        return 1


def _send_daemon_request(input_mode: str) -> int:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(DAEMON_CONNECT_TIMEOUT)
        client.connect(str(SOCKET_PATH))
        client.settimeout(DAEMON_RESPONSE_TIMEOUT)
        client.sendall(f"{input_mode}\n".encode("utf-8"))
        response_line = _recv_line(client)
    return _parse_rc_line(response_line)


def request_daemon(input_mode: str) -> int:
    """Send one action request to the daemon, auto-starting it if needed."""
    _warn_deprecated_env_vars()
    try:
        return _send_daemon_request(input_mode)
    except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError, ValueError):
        daemon_proc = start_daemon()
        if daemon_proc is None:
            LOGGER.error("Could not start voice-hotkey daemon process")
            notify("Voice", "Voice daemon unavailable")
            return 1
        if not _wait_for_daemon_ready(daemon_proc):
            LOGGER.error("Voice daemon failed startup handshake")
            notify("Voice", "Voice daemon unavailable")
            return 1
        try:
            return _send_daemon_request(input_mode)
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError, ValueError) as exc:
            LOGGER.error("Could not reach voice-hotkey daemon after startup: %s", exc)
            notify("Voice", "Voice daemon unavailable")
            return 1


def _execute_daemon_request(request: object) -> int:
    """Validate daemon request and run the mapped input handler."""
    request_id = next(DAEMON_REQUEST_IDS)
    started_at = time.time()
    if not isinstance(request, str):
        LOGGER.warning(
            "Rejected daemon request with invalid request type=%s request_id=%s",
            type(request).__name__,
            request_id,
        )
        return 2
    input_mode = request
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


def _decode_request_line(line: str) -> tuple[object, bool]:
    stripped = line.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except Exception:
            return None, True
        if isinstance(payload, dict):
            return payload.get("input"), True
        return None, True
    return stripped, False


def _handle_daemon_connection(conn: socket.socket) -> None:
    """Process a single client socket: decode request, execute, return rc."""
    with conn:
        try:
            conn.settimeout(DAEMON_CONNECT_TIMEOUT)
            request = _recv_line(conn)
        except (socket.timeout, UnicodeDecodeError, ValueError, OSError) as exc:
            LOGGER.warning("Voice daemon request parse failed: %s", exc)
            request = None
            wants_json = False
        else:
            request, wants_json = _decode_request_line(request)

        rc = _execute_daemon_request(request) if request is not None else 1
        try:
            if wants_json:
                conn.sendall((json.dumps({"rc": rc}) + "\n").encode("utf-8"))
            else:
                conn.sendall(f"{rc}\n".encode("utf-8"))
        except OSError as exc:
            LOGGER.debug("Voice daemon response send failed rc=%s err=%s", rc, exc)


def _socket_has_live_daemon() -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(DAEMON_CONNECT_TIMEOUT)
            probe.connect(str(SOCKET_PATH))
        return True
    except ConnectionRefusedError:
        return False
    except (FileNotFoundError, OSError):
        return False


def run_daemon() -> int:
    """Run single-instance UNIX-socket daemon loop for hotkey actions."""
    global ACTIVE_SESSION

    if not validate_environment():
        return 1

    _warn_deprecated_env_vars()

    _shutdown = False
    server: socket.socket | None = None

    def _request_shutdown(signum: int, frame: object) -> None:
        nonlocal _shutdown
        _shutdown = True
        LOGGER.info("Voice hotkey daemon received signal %s; shutting down", signum)
        session = ACTIVE_SESSION
        if session is not None:
            try:
                _stop_capture_process(session.proc)
            except Exception as exc:
                LOGGER.warning("Failed stopping active recorder during shutdown: %s", exc)
        if server is not None:
            try:
                server.close()
            except OSError:
                pass

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        if _socket_has_live_daemon():
            LOGGER.info("Voice hotkey daemon already running socket=%s", SOCKET_PATH)
            return 0
        SOCKET_PATH.unlink(missing_ok=True)

    LOGGER.info("Voice hotkey daemon starting socket=%s pid=%s", SOCKET_PATH, os.getpid())

    bound = False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server_socket:
            server = server_socket
            # Set a restrictive umask before bind() so the socket is created
            old_umask = os.umask(0o177)
            try:
                server_socket.bind(str(SOCKET_PATH))
                bound = True
            finally:
                os.umask(old_umask)
            try:
                SOCKET_PATH.chmod(0o600)
            except Exception as exc:
                LOGGER.warning("Could not chmod daemon socket: %s", exc)
            server_socket.listen(8)

            try:
                preload_models()
            except Exception as exc:
                notify("Voice", f"Model preload failed: {type(exc).__name__}")
                LOGGER.exception("Model preload failed; daemon exiting: %s", exc)
                return 1

            print("READY", flush=True)
            LOGGER.info("Voice hotkey daemon listening socket=%s pid=%s", SOCKET_PATH, os.getpid())

            while not _shutdown:
                try:
                    conn, _ = server_socket.accept()
                except OSError:
                    if _shutdown:
                        break
                    continue
                _handle_daemon_connection(conn)

            LOGGER.info("Voice hotkey daemon exiting cleanly")
    finally:
        if bound:
            SOCKET_PATH.unlink(missing_ok=True)
    return 0


def main() -> int:
    """Program entrypoint: dispatch to daemon server or client request path."""
    args = parse_args()

    if args.daemon:
        return run_daemon()

    return request_daemon(args.input)
