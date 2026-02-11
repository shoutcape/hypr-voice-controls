import os
import tempfile
from pathlib import Path

from .config import LANGUAGE_PATH
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
