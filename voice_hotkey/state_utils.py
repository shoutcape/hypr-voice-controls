import json
import os
import tempfile
import time
from pathlib import Path

from .config import LANGUAGE_PATH, WAKEWORD_ENABLED_DEFAULT, WAKEWORD_STATE_PATH
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
            except OSError:
                pass
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def get_saved_dictation_language() -> str:
    try:
        value = LANGUAGE_PATH.read_text(encoding="utf-8").strip().lower()
        if value == "en":
            return value
    except FileNotFoundError:
        pass
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
