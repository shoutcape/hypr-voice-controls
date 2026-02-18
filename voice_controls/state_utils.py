import json
import os
import tempfile
import time
from pathlib import Path

from .audio import pid_alive, pid_cmdline_contains
from .config import STATE_MAX_AGE_SECONDS
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
    # v1 is intentionally English-only.
    return "en"
