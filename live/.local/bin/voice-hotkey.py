#!/home/shoutcape/.venvs/voice/bin/python
import ctypes
import argparse
import json
import os
import socket
import signal
import sys
import logging
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path


AUDIO_SECONDS = 4
COMMAND_MODEL_NAME = "tiny"
DICTATE_MODEL_NAME = "medium"
DEVICE = "cuda"
COMPUTE_TYPE = "float16"
LOG_PATH = Path.home() / ".local" / "state" / "voice-hotkey.log"
SOCKET_PATH = Path.home() / ".local" / "state" / "voice-hotkey.sock"
DICTATE_SECONDS = 6
MAX_HOLD_SECONDS = 15
LANGUAGE_PATH = Path.home() / ".local" / "state" / "voice-hotkey-language"
DICTATE_STATE_PATH = Path.home() / ".local" / "state" / "voice-hotkey-dictate.json"
COMMAND_STATE_PATH = Path.home() / ".local" / "state" / "voice-hotkey-command.json"
DAEMON_CONNECT_TIMEOUT = 0.4
DAEMON_RESPONSE_TIMEOUT = 180
DAEMON_START_RETRIES = 40
DAEMON_START_DELAY = 0.1

COMMANDS = [
    (
        r"^((workspace )?(one|1)|(tyotila|työtila) (yksi|1|ykkonen|ykkönen))$",
        ["hyprctl", "dispatch", "workspace", "1"],
        "Workspace 1",
    ),
    (
        r"^((workspace )?(two|2)|(tyotila|työtila) (kaksi|2|kakkonen))$",
        ["hyprctl", "dispatch", "workspace", "2"],
        "Workspace 2",
    ),
    (
        r"^(volume up|((laita )?(aani|ääni)(ta)? )?kovemmalle)$",
        ["pamixer", "-i", "5"],
        "Volume up",
    ),
    (
        r"^(volume down|((laita )?(aani|ääni)(ta)? )?hiljemmalle)$",
        ["pamixer", "-d", "5"],
        "Volume down",
    ),
    (
        r"^(lock( screen)?|lukitse( naytto| näyttö)?)$",
        ["loginctl", "lock-session"],
        "Lock screen",
    ),
]


def setup_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("voice-hotkey")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger

    handler = logging.FileHandler(LOG_PATH)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


LOGGER = setup_logger()
WHISPER_MODELS = {}


def ensure_cuda_runtime_paths() -> None:
    lib_dirs = []

    try:
        import nvidia.cublas.lib  # type: ignore

        lib_dirs.append(list(nvidia.cublas.lib.__path__)[0])
    except Exception as exc:
        LOGGER.warning("Could not detect nvidia.cublas.lib path: %s", exc)

    try:
        import nvidia.cudnn.lib  # type: ignore

        lib_dirs.append(list(nvidia.cudnn.lib.__path__)[0])
    except Exception as exc:
        LOGGER.warning("Could not detect nvidia.cudnn.lib path: %s", exc)

    if not lib_dirs:
        return

    current = os.environ.get("LD_LIBRARY_PATH", "")
    current_parts = [p for p in current.split(":") if p]
    merged = []
    seen = set()

    for path in lib_dirs + current_parts:
        if path not in seen:
            seen.add(path)
            merged.append(path)

    os.environ["LD_LIBRARY_PATH"] = ":".join(merged)
    LOGGER.info("Updated LD_LIBRARY_PATH with CUDA runtime dirs: %s", lib_dirs)

    preload_candidates = [
        Path(lib_dirs[0]) / "libcublasLt.so.12" if len(lib_dirs) > 0 else None,
        Path(lib_dirs[0]) / "libcublas.so.12" if len(lib_dirs) > 0 else None,
        Path(lib_dirs[1]) / "libcudnn.so.9" if len(lib_dirs) > 1 else None,
    ]
    for lib_path in preload_candidates:
        if not lib_path or not lib_path.exists():
            continue
        try:
            ctypes.CDLL(str(lib_path), mode=ctypes.RTLD_GLOBAL)
            LOGGER.info("Preloaded CUDA runtime library: %s", lib_path)
        except Exception as exc:
            LOGGER.warning("Failed to preload CUDA runtime library %s: %s", lib_path, exc)


def notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "voice-hotkey", title, body], check=False, timeout=2)


def normalize(text: str) -> str:
    clean = re.sub(r"[^a-z0-9äöå ]+", "", text.lower())
    clean = re.sub(r"\s+", " ", clean).strip()
    clean = re.sub(r"^(ja|and|please|pliis|hei)\s+", "", clean)
    return clean


def fuzzy_allowlist_match(clean_text: str):
    words = set(clean_text.split())
    compact = clean_text.replace(" ", "")

    workspace_words = {"workspace", "työtila", "tyotila"}
    one_words = {"1", "one", "yksi", "ykkonen", "ykkönen"}
    two_words = {"2", "two", "kaksi", "kakkonen"}

    volume_words = {"volume", "ääni", "aani", "ääntä", "aanta"}
    up_words = {"up", "kovemmalle", "kovemmalla", "louder"}
    down_words = {
        "down",
        "hiljemmalle",
        "hiljemmalla",
        "hiljimmalle",
        "hiljimmälle",
        "hiljemmälle",
        "lower",
    }

    lock_words = {"lock", "lukitse", "lukit", "lukitseen"}
    screen_words = {"screen", "näyttö", "naytto", "näytön", "nayton", "näyttöön", "nayttoon"}

    if (workspace_words & words) and (one_words & words):
        return ["hyprctl", "dispatch", "workspace", "1"], "Workspace 1"

    if (workspace_words & words) and (two_words & words):
        return ["hyprctl", "dispatch", "workspace", "2"], "Workspace 2"

    # Volume intent stems from logs: hiljem-/hiljim-/hilimm- and kovem-/kuvem-
    if any(stem in clean_text for stem in {"hiljem", "hiljim", "hilimm"}) or any(
        stem in compact for stem in {"hiljem", "hiljim", "hilimm"}
    ):
        return ["pamixer", "-d", "5"], "Volume down"

    if any(stem in clean_text for stem in {"kovem", "kuvem"}) or any(
        stem in compact for stem in {"kovem", "kuvem"}
    ):
        return ["pamixer", "-i", "5"], "Volume up"

    if "lisää" in words and ("ääntä" in words or "ääni" in words or "aani" in words):
        return ["pamixer", "-i", "5"], "Volume up"

    if (volume_words & words and up_words & words) or clean_text in {"william up", "volyum up"}:
        return ["pamixer", "-i", "5"], "Volume up"

    if (volume_words & words and down_words & words) or clean_text in {
        "william down",
        "volyum down",
        "hyviemmalle",
    }:
        return ["pamixer", "-d", "5"], "Volume down"

    if (lock_words & words) and ((screen_words & words) or "lock" in words):
        return ["loginctl", "lock-session"], "Lock screen"

    return None, None


def record_clip(output_path: Path, duration_seconds: int = AUDIO_SECONDS) -> bool:
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "pulse",
        "-i",
        "default",
        "-t",
        str(duration_seconds),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(output_path),
    ]
    proc = subprocess.run(cmd, check=False, timeout=duration_seconds + 4, capture_output=True, text=True)
    if proc.returncode != 0:
        LOGGER.error("Mic capture failed rc=%s stderr=%s", proc.returncode, proc.stderr.strip())
        return False

    if not output_path.exists() or output_path.stat().st_size == 0:
        LOGGER.error("Mic capture produced empty audio file: %s", output_path)
        return False

    return True


def match_command(clean_text: str):
    for pattern, argv, label in COMMANDS:
        if re.fullmatch(pattern, clean_text):
            return argv, label
    return fuzzy_allowlist_match(clean_text)


def get_whisper_model(model_name: str):
    global WHISPER_MODELS
    model = WHISPER_MODELS.get(model_name)
    if model is None:
        ensure_cuda_runtime_paths()
        from faster_whisper import WhisperModel

        LOGGER.info("Loading Whisper model name=%s device=%s compute_type=%s", model_name, DEVICE, COMPUTE_TYPE)
        model = WhisperModel(model_name, device=DEVICE, compute_type=COMPUTE_TYPE)
        WHISPER_MODELS[model_name] = model
        LOGGER.info("Whisper model loaded name=%s", model_name)
    return model


def warm_model(model_name: str) -> None:
    try:
        get_whisper_model(model_name)
    except Exception as exc:
        LOGGER.warning("Model warmup failed name=%s err=%s", model_name, exc)


def transcribe(audio_path: Path, language: str | None = None, mode: str = "command") -> tuple[str, str, float]:
    model_name = COMMAND_MODEL_NAME if mode == "command" else DICTATE_MODEL_NAME
    model = get_whisper_model(model_name)
    transcribe_kwargs = {
        "language": language,
        "vad_filter": True,
    }
    if mode == "command":
        transcribe_kwargs.update(
            {
                "beam_size": 1,
                "best_of": 1,
                "temperature": 0.0,
                "condition_on_previous_text": False,
            }
        )

    segments, info = model.transcribe(str(audio_path), **transcribe_kwargs)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return text, info.language, info.language_probability


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


def get_saved_dictation_language() -> str:
    try:
        value = LANGUAGE_PATH.read_text(encoding="utf-8").strip().lower()
        if value in {"fi", "en"}:
            return value
    except FileNotFoundError:
        pass
    except Exception as exc:
        LOGGER.warning("Could not read language file: %s", exc)
    return "fi"


def set_saved_dictation_language(language: str) -> None:
    LANGUAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LANGUAGE_PATH.write_text(language, encoding="utf-8")


def toggle_saved_dictation_language() -> str:
    current = get_saved_dictation_language()
    toggled = "en" if current == "fi" else "fi"
    set_saved_dictation_language(toggled)
    return toggled


def inject_text_into_focused_input(text: str) -> bool:
    if not shutil.which("wl-copy"):
        LOGGER.error("Cannot inject text: wl-copy not found")
        return False

    try:
        copy_proc = subprocess.run(
            ["wl-copy"],
            input=text,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except Exception as exc:
        LOGGER.error("Clipboard write failed: %s", exc)
        return False

    if copy_proc.returncode != 0:
        LOGGER.error("Clipboard write failed rc=%s", copy_proc.returncode)
        return False

    time.sleep(0.08)
    attempts = [
        ["hyprctl", "dispatch", "sendshortcut", "CTRL SHIFT,V,"],
        ["hyprctl", "dispatch", "sendshortcut", "SHIFT,Insert,"],
        ["hyprctl", "dispatch", "sendshortcut", "CTRL,V,"],
    ]

    for cmd in attempts:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=3)
        LOGGER.info(
            "Paste attempt cmd=%s rc=%s stdout=%s stderr=%s",
            cmd,
            proc.returncode,
            proc.stdout.strip(),
            proc.stderr.strip(),
        )
        if proc.returncode == 0:
            return True

    return False


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False

    proc_path = Path(f"/proc/{pid}")
    if not proc_path.exists():
        return False

    stat_path = proc_path / "stat"
    try:
        stat_raw = stat_path.read_text(encoding="utf-8", errors="ignore")
        if ") " in stat_raw:
            state = stat_raw.split(") ", 1)[1][:1]
            if state == "Z":
                return False
    except Exception:
        pass

    return True


def wait_for_pid_exit(pid: int, timeout_seconds: float) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_alive(pid)


def stop_recording_pid(pid: int, label: str) -> None:
    if not pid_alive(pid):
        LOGGER.info("%s process already exited pid=%s", label, pid)
        return

    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        LOGGER.info("%s process disappeared before SIGINT pid=%s", label, pid)
        return
    except Exception as exc:
        LOGGER.warning("Could not signal %s pid=%s err=%s", label, pid, exc)
        return

    if wait_for_pid_exit(pid, 1.5):
        LOGGER.info("%s process exited after SIGINT pid=%s", label, pid)
        return

    LOGGER.warning("%s process still alive after SIGINT; sending SIGTERM pid=%s", label, pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        LOGGER.info("%s process disappeared before SIGTERM pid=%s", label, pid)
        return
    except Exception as exc:
        LOGGER.warning("Could not SIGTERM %s pid=%s err=%s", label, pid, exc)
        return

    if wait_for_pid_exit(pid, 1.0):
        LOGGER.info("%s process exited after SIGTERM pid=%s", label, pid)
        return

    LOGGER.error("%s process still alive; sending SIGKILL pid=%s", label, pid)
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception as exc:
        LOGGER.error("Could not SIGKILL %s pid=%s err=%s", label, pid, exc)

    wait_for_pid_exit(pid, 0.5)


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
        "pulse",
        "-i",
        "default",
        "-t",
        str(MAX_HOLD_SECONDS),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(audio_path),
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    state = {
        "pid": proc.pid,
        "tmpdir": tmpdir,
        "audio_path": str(audio_path),
        "language": language,
        "started_at": time.time(),
    }
    DICTATE_STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
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
        "pulse",
        "-i",
        "default",
        "-t",
        str(MAX_HOLD_SECONDS),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(audio_path),
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    state = {
        "pid": proc.pid,
        "tmpdir": tmpdir,
        "audio_path": str(audio_path),
        "language": language,
        "started_at": time.time(),
    }
    COMMAND_STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
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

    DICTATE_STATE_PATH.unlink(missing_ok=True)

    try:
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            notify("Voice", "No speech captured")
            LOGGER.info("Voice hotkey end status=no_speech source=dictate_hold")
            return 0

        if DICTATE_MODEL_NAME not in WHISPER_MODELS:
            notify("Voice", "Warming dictation model...")

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
        if tmpdir.exists():
            for child in tmpdir.iterdir():
                child.unlink(missing_ok=True)
            tmpdir.rmdir()


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

    COMMAND_STATE_PATH.unlink(missing_ok=True)

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
        if tmpdir.exists():
            for child in tmpdir.iterdir():
                child.unlink(missing_ok=True)
            tmpdir.rmdir()


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


def run_command(argv) -> bool:
    proc = subprocess.run(argv, check=False, timeout=8, capture_output=True, text=True)
    if proc.returncode != 0:
        LOGGER.error(
            "Command failed rc=%s argv=%s stdout=%s stderr=%s",
            proc.returncode,
            argv,
            proc.stdout.strip(),
            proc.stderr.strip(),
        )
    return proc.returncode == 0


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


def start_daemon() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink(missing_ok=True)

    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--daemon"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def request_daemon(input_mode: str, *, auto_start: bool = True) -> int:
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
                start_daemon()
            time.sleep(DAEMON_START_DELAY)

    LOGGER.error("Could not reach voice-hotkey daemon after retries")
    notify("Voice", "Voice daemon unavailable")
    return 1


def run_daemon() -> int:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink(missing_ok=True)

    try:
        get_whisper_model(COMMAND_MODEL_NAME)
    except Exception as exc:
        LOGGER.warning("Command model preload failed: %s", exc)

    threading.Thread(target=warm_model, args=(DICTATE_MODEL_NAME,), daemon=True).start()

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(SOCKET_PATH))
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


def main() -> int:
    args = parse_args()

    if args.daemon:
        return run_daemon()

    return request_daemon(args.input)


if __name__ == "__main__":
    raise SystemExit(main())
