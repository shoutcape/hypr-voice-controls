"""Responsibility: Centralize environment-driven runtime configuration constants."""

import os  # Read environment variables for runtime configuration.
from pathlib import Path  # Construct standard state/log/socket file paths.


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

# Use XDG_RUNTIME_DIR for runtime files (socket) when available.
# This directory is typically a user-private tmpfs (/run/user/<uid>) that is
# auto-cleaned on logout and guaranteed to have 0700 permissions. Persistent
# files (logs) stay in ~/.local/state/ so they survive reboots.
_runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", Path.home() / ".local" / "state"))
_state_dir = Path.home() / ".local" / "state"

LOG_PATH = _state_dir / "voice-hotkey.log"
SOCKET_PATH = _runtime_dir / "voice-hotkey.sock"
DAEMON_CONNECT_TIMEOUT = env_float("VOICE_DAEMON_CONNECT_TIMEOUT", 0.4)
DAEMON_RESPONSE_TIMEOUT = env_int("VOICE_DAEMON_RESPONSE_TIMEOUT", 180)
DAEMON_READY_TIMEOUT = env_float("VOICE_DAEMON_READY_TIMEOUT", 60.0)
LOG_TRANSCRIPTS = env_bool("VOICE_LOG_TRANSCRIPTS", False)
NOTIFY_TIMEOUT_MS = env_int("VOICE_NOTIFY_TIMEOUT_MS", 2200)

VENV_PYTHON = Path.home() / ".venvs" / "voice" / "bin" / "python"

DICTATION_ALLOW_NEWLINES = env_bool("VOICE_DICTATION_ALLOW_NEWLINES", False)
# Hyprctl sendshortcut argument used to trigger paste into the focused window.
# Override if your application uses a different paste binding (e.g. "CTRL,V,").
PASTE_SHORTCUT = os.environ.get("VOICE_PASTE_SHORTCUT", "CTRL SHIFT,V,")
