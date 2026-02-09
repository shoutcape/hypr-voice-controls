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


AUDIO_SECONDS = env_int("VOICE_AUDIO_SECONDS", 4)
COMMAND_MODEL_NAME = os.environ.get("VOICE_COMMAND_MODEL", "small")
DICTATE_MODEL_NAME = os.environ.get("VOICE_DICTATE_MODEL", "medium")
COMMAND_MODEL_NAME_EN = os.environ.get("VOICE_COMMAND_MODEL_EN", "small.en")
COMMAND_MODEL_NAME_FI = os.environ.get("VOICE_COMMAND_MODEL_FI", COMMAND_MODEL_NAME)
DICTATE_MODEL_NAME_EN = os.environ.get("VOICE_DICTATE_MODEL_EN", "medium.en")
DICTATE_MODEL_NAME_FI = os.environ.get("VOICE_DICTATE_MODEL_FI", DICTATE_MODEL_NAME)
DEVICE_CANDIDATES = [d.strip() for d in os.environ.get("VOICE_DEVICE", "cuda,cpu").split(",") if d.strip()]
COMPUTE_TYPE_OVERRIDE = os.environ.get("VOICE_COMPUTE_TYPE")
AUDIO_BACKEND = os.environ.get("VOICE_AUDIO_BACKEND", "pulse")
AUDIO_SOURCE = os.environ.get("VOICE_AUDIO_SOURCE", "default")
LOG_PATH = Path.home() / ".local" / "state" / "voice-hotkey.log"
SOCKET_PATH = Path.home() / ".local" / "state" / "voice-hotkey.sock"
DICTATE_SECONDS = env_int("VOICE_DICTATE_SECONDS", 6)
LANGUAGE_PATH = Path.home() / ".local" / "state" / "voice-hotkey-language"
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
VENV_PYTHON = Path.home() / ".venvs" / "voice" / "bin" / "python"

ASR_BACKEND = os.environ.get("VOICE_ASR_BACKEND", "faster_whisper").strip().lower()
WHISPER_SERVER_URL = os.environ.get("VOICE_WHISPER_SERVER_URL", "http://127.0.0.1:8080/inference").strip()
WHISPER_SERVER_TIMEOUT = env_int("VOICE_WHISPER_SERVER_TIMEOUT", 90)
DICTATION_INJECTOR = os.environ.get("VOICE_DICTATION_INJECTOR", "wtype").strip().lower()
OVERLAY_ENABLED = env_bool("VOICE_OVERLAY_ENABLED", True)
WAKEWORD_ENABLED_DEFAULT = env_bool("VOICE_WAKEWORD_ENABLED", False)
WAKEWORD_STATE_PATH = Path.home() / ".local" / "state" / "voice-hotkey-wakeword.json"
WAKEWORD_MODEL_DIR = Path(os.path.expanduser(os.environ.get("VOICE_WAKEWORD_MODEL_PATH", "~/.config/hypr-voice-controls/wakeword/")))
WAKE_GREETING_ENABLED = env_bool("VOICE_WAKE_GREETING_ENABLED", True)
WAKE_GREETING_TEXT = os.environ.get("VOICE_WAKE_GREETING_TEXT", "hello").strip()
