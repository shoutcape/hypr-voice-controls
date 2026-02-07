import json
import re
from pathlib import Path

from .logging_utils import LOGGER
from .models import CommandSpec

USER_COMMANDS_PATH = Path.home() / ".config" / "hypr" / "voice-commands.json"
_USER_COMMANDS_CACHE: list[CommandSpec] = []
_USER_COMMANDS_MTIME_NS: int | None = None

# Optional local fallback commands.
# Keep this empty by default so spoken commands are JSON-driven.
LOCAL_COMMANDS: list[CommandSpec] = []

# Example local commands (not active unless copied to LOCAL_COMMANDS):
# LOCAL_COMMANDS = [
#     CommandSpec(
#         r"^open obsidian$",
#         ["hyprctl", "dispatch", "exec", "uwsm-app -- obsidian"],
#         "Open Obsidian",
#     ),
# ]


def _load_user_commands() -> list[CommandSpec]:
    try:
        payload = json.loads(USER_COMMANDS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception as exc:
        LOGGER.error("Failed to read user voice commands path=%s err=%s", USER_COMMANDS_PATH, exc)
        return []

    if not isinstance(payload, list):
        LOGGER.error("Invalid user voice commands format path=%s expected=list", USER_COMMANDS_PATH)
        return []

    loaded: list[CommandSpec] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            LOGGER.warning("Skipping user voice command index=%s reason=not_object", index)
            continue

        if item.get("enabled", True) is False:
            continue

        pattern = item.get("pattern")
        argv = item.get("argv")
        label = item.get("label")

        if not isinstance(pattern, str) or not pattern.strip():
            LOGGER.warning("Skipping user voice command index=%s reason=invalid_pattern", index)
            continue
        if not isinstance(label, str) or not label.strip():
            LOGGER.warning("Skipping user voice command index=%s reason=invalid_label", index)
            continue
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and arg for arg in argv):
            LOGGER.warning("Skipping user voice command index=%s reason=invalid_argv", index)
            continue

        try:
            re.compile(pattern)
        except re.error as exc:
            LOGGER.warning("Skipping user voice command index=%s reason=bad_regex err=%s", index, exc)
            continue

        loaded.append(CommandSpec(pattern=pattern, argv=argv, label=label))

    LOGGER.info("Loaded user voice commands path=%s count=%s", USER_COMMANDS_PATH, len(loaded))
    return loaded


def get_user_commands() -> list[CommandSpec]:
    global _USER_COMMANDS_CACHE, _USER_COMMANDS_MTIME_NS

    try:
        stat = USER_COMMANDS_PATH.stat()
    except FileNotFoundError:
        if _USER_COMMANDS_MTIME_NS is not None:
            _USER_COMMANDS_CACHE = []
            _USER_COMMANDS_MTIME_NS = None
            LOGGER.info("User voice commands file removed path=%s", USER_COMMANDS_PATH)
        return []
    except Exception as exc:
        LOGGER.error("Failed to stat user voice commands path=%s err=%s", USER_COMMANDS_PATH, exc)
        return _USER_COMMANDS_CACHE

    mtime_ns = stat.st_mtime_ns
    if _USER_COMMANDS_MTIME_NS == mtime_ns:
        return _USER_COMMANDS_CACHE

    _USER_COMMANDS_CACHE = _load_user_commands()
    _USER_COMMANDS_MTIME_NS = mtime_ns
    return _USER_COMMANDS_CACHE


def normalize(text: str) -> str:
    clean = re.sub(r"[^a-z0-9äöå ]+", "", text.lower())
    clean = re.sub(r"\s+", " ", clean).strip()
    clean = re.sub(r"^(ja|and|please|pliis|hei)\s+", "", clean)
    return clean


def match_command(clean_text: str) -> tuple[list[str] | None, str | None]:
    for command in get_user_commands():
        if re.fullmatch(command.pattern, clean_text):
            return command.argv, command.label

    for command in LOCAL_COMMANDS:
        if re.fullmatch(command.pattern, clean_text):
            return command.argv, command.label

    return None, None
