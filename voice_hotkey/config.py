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


AUDIO_SECONDS = env_int("VOICE_AUDIO_SECONDS", 4)
COMMAND_MODEL_NAME = os.environ.get("VOICE_COMMAND_MODEL", "tiny")
DICTATE_MODEL_NAME = os.environ.get("VOICE_DICTATE_MODEL", "medium")
DEVICE_CANDIDATES = [d.strip() for d in os.environ.get("VOICE_DEVICE", "cuda,cpu").split(",") if d.strip()]
COMPUTE_TYPE_OVERRIDE = os.environ.get("VOICE_COMPUTE_TYPE")
AUDIO_BACKEND = os.environ.get("VOICE_AUDIO_BACKEND", "pulse")
AUDIO_SOURCE = os.environ.get("VOICE_AUDIO_SOURCE", "default")
LOG_PATH = Path.home() / ".local" / "state" / "voice-hotkey.log"
SOCKET_PATH = Path.home() / ".local" / "state" / "voice-hotkey.sock"
DICTATE_SECONDS = env_int("VOICE_DICTATE_SECONDS", 6)
MAX_HOLD_SECONDS = env_int("VOICE_MAX_HOLD_SECONDS", 15)
LANGUAGE_PATH = Path.home() / ".local" / "state" / "voice-hotkey-language"
DICTATE_STATE_PATH = Path.home() / ".local" / "state" / "voice-hotkey-dictate.json"
COMMAND_STATE_PATH = Path.home() / ".local" / "state" / "voice-hotkey-command.json"
DAEMON_CONNECT_TIMEOUT = env_float("VOICE_DAEMON_CONNECT_TIMEOUT", 0.4)
DAEMON_RESPONSE_TIMEOUT = env_int("VOICE_DAEMON_RESPONSE_TIMEOUT", 180)
DAEMON_START_RETRIES = env_int("VOICE_DAEMON_START_RETRIES", 40)
DAEMON_START_DELAY = env_float("VOICE_DAEMON_START_DELAY", 0.1)
VENV_PYTHON = Path.home() / ".venvs" / "voice" / "bin" / "python"
