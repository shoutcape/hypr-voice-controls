import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from .logging_utils import LOGGER


@dataclass(frozen=True)
class CommandSpec:
    argv: list[str]
    label: str

USER_COMMANDS_PATH = Path.home() / ".config" / "hypr" / "voice-commands.json"
_USER_COMPILED_CACHE: tuple[tuple[re.Pattern[str], CommandSpec], ...] = ()
_USER_COMMANDS_MTIME_NS: int | None = None
_USER_COMMANDS_LOCK = threading.RLock()
MAX_COMMAND_PATTERN_LENGTH = 300
MAX_NORMALIZED_INPUT_LENGTH = 160
NORMALIZE_NON_ALNUM_SPACE_RE = re.compile(r"[^a-z0-9 ]+")
NORMALIZE_SPACE_RE = re.compile(r"\s+")
NORMALIZE_PREFIX_RE = re.compile(r"^(and|please)\s+")


def _load_user_commands() -> tuple[tuple[re.Pattern[str], CommandSpec], ...]:
    try:
        payload = json.loads(USER_COMMANDS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ()
    except Exception as exc:
        LOGGER.error("Failed to read user voice commands path=%s err=%s", USER_COMMANDS_PATH, exc)
        return ()

    if not isinstance(payload, list):
        LOGGER.error("Invalid user voice commands format path=%s expected=list", USER_COMMANDS_PATH)
        return ()

    compiled_loaded: list[tuple[re.Pattern[str], CommandSpec]] = []
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
        if len(pattern) > MAX_COMMAND_PATTERN_LENGTH:
            LOGGER.warning(
                "Skipping user voice command index=%s reason=pattern_too_long limit=%s",
                index,
                MAX_COMMAND_PATTERN_LENGTH,
            )
            continue
        if not isinstance(label, str) or not label.strip():
            LOGGER.warning("Skipping user voice command index=%s reason=invalid_label", index)
            continue
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and arg for arg in argv):
            LOGGER.warning("Skipping user voice command index=%s reason=invalid_argv", index)
            continue

        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            LOGGER.warning("Skipping user voice command index=%s reason=bad_regex err=%s", index, exc)
            continue

        compiled_loaded.append((compiled, CommandSpec(argv=argv, label=label)))

    LOGGER.info("Loaded user voice commands path=%s count=%s", USER_COMMANDS_PATH, len(compiled_loaded))
    return tuple(compiled_loaded)


def _ensure_user_commands_cache() -> None:
    global _USER_COMPILED_CACHE, _USER_COMMANDS_MTIME_NS

    with _USER_COMMANDS_LOCK:
        try:
            stat = USER_COMMANDS_PATH.stat()
        except FileNotFoundError:
            if _USER_COMMANDS_MTIME_NS is not None:
                _USER_COMPILED_CACHE = ()
                _USER_COMMANDS_MTIME_NS = None
                LOGGER.info("User voice commands file removed path=%s", USER_COMMANDS_PATH)
            return
        except Exception as exc:
            LOGGER.error("Failed to stat user voice commands path=%s err=%s", USER_COMMANDS_PATH, exc)
            return

        mtime_ns = stat.st_mtime_ns
        if _USER_COMMANDS_MTIME_NS == mtime_ns:
            return

        _USER_COMPILED_CACHE = _load_user_commands()
        _USER_COMMANDS_MTIME_NS = mtime_ns


def get_user_compiled_commands() -> tuple[tuple[re.Pattern[str], CommandSpec], ...]:
    _ensure_user_commands_cache()
    with _USER_COMMANDS_LOCK:
        return _USER_COMPILED_CACHE


def normalize(text: str) -> str:
    clean = NORMALIZE_NON_ALNUM_SPACE_RE.sub("", text.lower())
    clean = NORMALIZE_SPACE_RE.sub(" ", clean).strip()
    clean = NORMALIZE_PREFIX_RE.sub("", clean)
    return clean


def match_command(clean_text: str) -> tuple[list[str] | None, str | None]:
    if len(clean_text) > MAX_NORMALIZED_INPUT_LENGTH:
        LOGGER.warning("Skipping command match due to input length=%s", len(clean_text))
        return None, None

    for compiled, command in get_user_compiled_commands():
        if compiled.fullmatch(clean_text):
            return command.argv, command.label

    return None, None
