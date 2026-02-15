import json
import os
import tempfile
import time
from pathlib import Path

from .audio import pid_alive, pid_cmdline_contains
from .config import LANGUAGE_PATH, STATE_MAX_AGE_SECONDS, WAKEWORD_ENABLED_DEFAULT, WAKEWORD_STATE_PATH
from .logging_utils import LOGGER


def write_private_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError as exc:
                LOGGER.debug("Could not remove temp state file path=%s err=%s", tmp_path, exc)


def state_required_substrings(state: dict) -> list[str]:
    raw = state.get("pid_required_substrings")
    if isinstance(raw, list):
        tokens = [token for token in raw if isinstance(token, str) and token.strip()]
        if tokens:
            return tokens
    return ["ffmpeg"]


def is_state_stale(started_at: float | int | None, *, now: float | None = None) -> bool:
    if not isinstance(started_at, (int, float)):
        return False
    active_now = time.time() if now is None else now
    return (active_now - float(started_at)) > STATE_MAX_AGE_SECONDS


def is_capture_state_active_payload(state: dict, *, now: float | None = None) -> bool:
    pid = state.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False

    active_now = time.time() if now is None else now
    if is_state_stale(state.get("started_at"), now=active_now):
        return False

    if not pid_alive(pid):
        return False

    required_substrings = state_required_substrings(state)
    if not pid_cmdline_contains(pid, required_substrings=required_substrings):
        return False

    return True


def is_capture_state_active(state_path: Path, *, now: float | None = None) -> bool:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        LOGGER.warning("Could not read capture state path=%s err=%s", state_path, exc)
        return False

    return is_capture_state_active_payload(state, now=now)


def get_saved_dictation_language() -> str:
    # v1 is intentionally English-only; keep file access for forward compatibility.
    try:
        LANGUAGE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        LOGGER.debug("Language file not found path=%s; defaulting to en", LANGUAGE_PATH)
    except Exception as exc:
        LOGGER.warning("Could not read language file: %s", exc)
    return "en"


def read_wakeword_enabled(default: bool = WAKEWORD_ENABLED_DEFAULT) -> bool:
    try:
        payload = json.loads(WAKEWORD_STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        LOGGER.warning("Could not read wakeword state: %s", exc)
        return default

    enabled = payload.get("enabled")
    if isinstance(enabled, bool):
        return enabled
    return default


def read_wakeword_enabled_cached(
    cached_enabled: bool | None,
    cached_mtime_ns: int | None,
    default: bool = WAKEWORD_ENABLED_DEFAULT,
) -> tuple[bool, int | None]:
    try:
        stat = WAKEWORD_STATE_PATH.stat()
    except FileNotFoundError:
        return default, None
    except OSError as exc:
        LOGGER.warning("Could not stat wakeword state: %s", exc)
        if cached_enabled is not None:
            return cached_enabled, cached_mtime_ns
        return default, cached_mtime_ns

    mtime_ns = stat.st_mtime_ns
    if cached_enabled is not None and cached_mtime_ns == mtime_ns:
        return cached_enabled, cached_mtime_ns
    return read_wakeword_enabled(default=default), mtime_ns


def set_wakeword_enabled(enabled: bool) -> None:
    state = {
        "enabled": enabled,
        "updated_at": time.time(),
    }
    write_private_text(WAKEWORD_STATE_PATH, json.dumps(state))
