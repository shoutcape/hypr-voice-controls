import os
from pathlib import Path


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


COMMAND_MODEL_NAME = os.environ.get("VOICE_COMMAND_MODEL", "large-v3-turbo")
DICTATE_MODEL_NAME = os.environ.get("VOICE_DICTATE_MODEL", "large-v3-turbo")
if "VOICE_COMMAND_MODEL" not in os.environ and "VOICE_COMMAND_MODEL_EN" in os.environ:
    COMMAND_MODEL_NAME = os.environ["VOICE_COMMAND_MODEL_EN"]
if "VOICE_DICTATE_MODEL" not in os.environ and "VOICE_DICTATE_MODEL_EN" in os.environ:
    DICTATE_MODEL_NAME = os.environ["VOICE_DICTATE_MODEL_EN"]
DEVICE_CANDIDATES = [d.strip() for d in os.environ.get("VOICE_DEVICE", "cuda,cpu").split(",") if d.strip()]
COMPUTE_TYPE_OVERRIDE = os.environ.get("VOICE_COMPUTE_TYPE")
AUDIO_BACKEND = os.environ.get("VOICE_AUDIO_BACKEND", "pulse")
AUDIO_SOURCE = os.environ.get("VOICE_AUDIO_SOURCE", "default")
LOG_PATH = Path.home() / ".local" / "state" / "voice-hotkey.log"
SOCKET_PATH = Path.home() / ".local" / "state" / "voice-hotkey.sock"
DICTATE_STATE_PATH = Path.home() / ".local" / "state" / "voice-hotkey-dictate.json"
COMMAND_STATE_PATH = Path.home() / ".local" / "state" / "voice-hotkey-command.json"
LOCK_PATH = Path.home() / ".local" / "state" / "voice-hotkey.lock"
DAEMON_CONNECT_TIMEOUT = env_float("VOICE_DAEMON_CONNECT_TIMEOUT", 0.4)
DAEMON_RESPONSE_TIMEOUT = env_int("VOICE_DAEMON_RESPONSE_TIMEOUT", 180)
DAEMON_START_RETRIES = env_int("VOICE_DAEMON_START_RETRIES", 40)
DAEMON_START_DELAY = env_float("VOICE_DAEMON_START_DELAY", 0.1)
DAEMON_MAX_REQUEST_BYTES = env_int("VOICE_DAEMON_MAX_REQUEST_BYTES", 8192)
STATE_MAX_AGE_SECONDS = env_int("VOICE_STATE_MAX_AGE_SECONDS", 900)
LOG_TRANSCRIPTS = env_bool("VOICE_LOG_TRANSCRIPTS", False)
LOG_COMMAND_OUTPUT_MAX = env_int("VOICE_LOG_COMMAND_OUTPUT_MAX", 300)
NOTIFY_TIMEOUT_MS = env_int("VOICE_NOTIFY_TIMEOUT_MS", 2200)
TTS_ENABLED = env_bool("VOICE_TTS_ENABLED", False)
TTS_COOLDOWN_MS = env_int("VOICE_TTS_COOLDOWN_MS", 900)
TTS_MAX_CHARS = env_int("VOICE_TTS_MAX_CHARS", 90)
VENV_PYTHON = Path.home() / ".venvs" / "voice" / "bin" / "python"

ASR_BACKEND = os.environ.get("VOICE_ASR_BACKEND", "faster_whisper").strip().lower()
WHISPER_SERVER_URL = os.environ.get("VOICE_WHISPER_SERVER_URL", "http://127.0.0.1:8080/inference").strip()
WHISPER_SERVER_TIMEOUT = env_int("VOICE_WHISPER_SERVER_TIMEOUT", 90)
DICTATION_INJECTOR = os.environ.get("VOICE_DICTATION_INJECTOR", "wtype").strip().lower()
DICTATION_STRICT_TEXT = env_bool("VOICE_DICTATION_STRICT_TEXT", True)
DICTATION_ALLOW_NEWLINES = env_bool("VOICE_DICTATION_ALLOW_NEWLINES", False)
OVERLAY_ENABLED = env_bool("VOICE_OVERLAY_ENABLED", True)
