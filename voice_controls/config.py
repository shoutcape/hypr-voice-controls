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


MODEL_NAME = os.environ.get("VOICE_MODEL", "large-v3-turbo")
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

VENV_PYTHON = Path.home() / ".venvs" / "voice" / "bin" / "python"

DICTATION_ALLOW_NEWLINES = env_bool("VOICE_DICTATION_ALLOW_NEWLINES", False)
