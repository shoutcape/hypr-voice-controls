import argparse
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
    DAEMON_RESPONSE_TIMEOUT,
    DAEMON_START_DELAY,
    DAEMON_START_RETRIES,
    DICTATE_SECONDS,
    DICTATE_STATE_PATH,
    MAX_HOLD_SECONDS,
    SOCKET_PATH,
    VENV_PYTHON,
)
from .integrations import inject_text_into_focused_input, notify, run_command
from .logging_utils import LOGGER
from .state_utils import get_saved_dictation_language, toggle_saved_dictation_language, write_private_text
from .stt import dictation_model_name, is_model_loaded, preload_models, transcribe, warm_model


def validate_environment() -> bool:
    required_tools = ["ffmpeg"]
    missing_required = [tool for tool in required_tools if not shutil.which(tool)]
    if missing_required:
        LOGGER.error("Missing required tools: %s", ", ".join(missing_required))
        notify("Voice", f"Missing required tools: {', '.join(missing_required)}")
        return False

    optional_tools = ["hyprctl", "wl-copy", "notify-send", "zenity"]
    missing_optional = [tool for tool in optional_tools if not shutil.which(tool)]
    if missing_optional:
        LOGGER.warning("Missing optional tools: %s", ", ".join(missing_optional))

    return True


def choose_dictation_language() -> str | None:
    if not shutil.which("zenity"):
        LOGGER.error("Text input failed: zenity not found")
        notify("Voice", "Text input unavailable: zenity missing")
        return None

    proc = subprocess.run(
        [
            "zenity",
            "--question",
            "--title=Voice Command",
            "--text=Choose dictation language for voice input:",
            "--ok-label=Finnish",
            "--extra-button=English",
            "--cancel-label=Cancel",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    selected = proc.stdout.strip().lower()
    if selected == "english":
        return "en"

    if proc.returncode != 0:
        LOGGER.info("Dictation language selection canceled rc=%s stdout=%r", proc.returncode, proc.stdout.strip())
        return None

    return "fi"


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
        "-t",
        str(MAX_HOLD_SECONDS),
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
        "-t",
        str(MAX_HOLD_SECONDS),
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

    notify("Voice", "Key released. Processing dictation...")

    if pid > 0:
        stop_recording_pid(pid, "Dictation ffmpeg")

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

        if not is_model_loaded(dictation_model_name()):
            LOGGER.info("Dictation model not yet cached model=%s", dictation_model_name())

        text, detected_language, language_probability = transcribe(audio_path, language=language, mode="dictate")
        spoken = text.strip()
        probability = language_probability if language_probability is not None else 0.0
        LOGGER.info(
            "Dictation hold language_selected=%s language_detected=%s probability=%.3f text=%r",
            language,
            detected_language,
            probability,
            spoken,
        )

        if not spoken:
            notify("Voice", "No speech detected")
            LOGGER.info("Voice hotkey end status=no_speech source=dictate_hold")
            return 0

        if inject_text_into_focused_input(spoken):
            notify("Voice", "Dictation pasted")
            LOGGER.info("Voice hotkey end status=ok source=dictate_hold text=%r", spoken)
            return 0

        notify("Voice", "Dictation paste failed")
        LOGGER.info("Voice hotkey end status=paste_failed source=dictate_hold text=%r", spoken)
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

    notify("Voice", "Key released. Processing command...")

    if pid > 0:
        stop_recording_pid(pid, "Command ffmpeg")

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
    selected_language = choose_dictation_language()
    if selected_language is None:
        LOGGER.info("Voice hotkey end status=dictation_language_canceled source=dictate")
        return 0

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
            "Dictation language_selected=%s language_detected=%s probability=%.3f text=%r",
            selected_language,
            detected_language,
            probability,
            spoken,
        )

        if not spoken:
            notify("Voice", "No speech detected")
            LOGGER.info("Voice hotkey end status=no_speech source=dictate")
            return 0

        if inject_text_into_focused_input(spoken):
            notify("Voice", "Dictation pasted")
            LOGGER.info("Voice hotkey end status=ok source=dictate text=%r", spoken)
            return 0

        notify("Voice", "Dictation paste failed")
        LOGGER.info("Voice hotkey end status=paste_failed source=dictate text=%r", spoken)
        return 1


def handle_command_text(raw_text: str, source: str, language: str | None, language_probability: float | None) -> int:
    clean = normalize(raw_text)
    probability = language_probability if language_probability is not None else 0.0
    LOGGER.info(
        "Input source=%s language=%s probability=%.3f raw=%r normalized=%r",
        source,
        language,
        probability,
        raw_text,
        clean,
    )

    if not clean:
        notify("Voice", "No command detected")
        LOGGER.info("Voice hotkey end status=no_input source=%s", source)
        return 0

    argv, label = match_command(clean)
    if not argv:
        notify("Voice", f"Heard: '{clean}' (no match)")
        LOGGER.info("Voice hotkey end status=no_match source=%s heard=%r", source, clean)
        return 0

    ok = run_command(argv)
    if ok:
        notify("Voice", f"Heard: '{clean}' -> {label}")
        LOGGER.info(
            "Voice hotkey end status=ok source=%s heard=%r action=%s argv=%s",
            source,
            clean,
            label,
            argv,
        )
        return 0

    notify("Voice", f"Command failed: {label}")
    LOGGER.info(
        "Voice hotkey end status=command_failed source=%s heard=%r action=%s argv=%s",
        source,
        clean,
        label,
        argv,
    )
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Voice/text hotkey command runner")
    parser.add_argument(
        "--input",
        choices=[
            "voice",
            "text",
            "dictate",
            "dictate-start",
            "dictate-stop",
            "dictate-language",
            "command-start",
            "command-stop",
        ],
        default="voice",
    )
    parser.add_argument("--daemon", action="store_true")
    return parser.parse_args()


def handle_input(input_mode: str) -> int:
    if input_mode == "dictate-language":
        selected = toggle_saved_dictation_language()
        label = "English" if selected == "en" else "Finnish"
        notify("Voice", f"Dictation language: {label}")
        LOGGER.info("Voice hotkey end status=ok source=dictate_language toggled=%s", selected)
        return 0

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
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink(missing_ok=True)

    runtime_python = str(VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable))
    script_path = entry_script if entry_script is not None else Path(sys.argv[0]).resolve()

    subprocess.Popen(
        [runtime_python, str(script_path), "--daemon"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def request_daemon(input_mode: str, *, auto_start: bool = True, entry_script: Path | None = None) -> int:
    payload = json.dumps({"input": input_mode}).encode("utf-8") + b"\n"

    for attempt in range(DAEMON_START_RETRIES):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(DAEMON_CONNECT_TIMEOUT)
                client.connect(str(SOCKET_PATH))
                client.settimeout(DAEMON_RESPONSE_TIMEOUT)
                client.sendall(payload)
                response = client.recv(4096)
            if not response:
                return 1
            data = json.loads(response.decode("utf-8"))
            return int(data.get("rc", 1))
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, json.JSONDecodeError):
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

    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink(missing_ok=True)

    preload_models()
    threading.Thread(target=warm_model, args=(dictation_model_name(),), daemon=True).start()

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
                    raw = conn.recv(4096)
                    if not raw:
                        continue
                    request = json.loads(raw.decode("utf-8"))
                    input_mode = request.get("input", "voice")
                    rc = handle_input(input_mode)
                except Exception as exc:
                    LOGGER.exception("Voice daemon request failed: %s", exc)
                    rc = 1

                try:
                    conn.sendall(json.dumps({"rc": rc}).encode("utf-8"))
                except Exception:
                    pass


def main(entry_script: Path | None = None) -> int:
    args = parse_args()

    if args.daemon:
        return run_daemon()

    return request_daemon(args.input, entry_script=entry_script)
