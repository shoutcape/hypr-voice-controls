"""Responsibility: Persist sensitive state files atomically with private permissions."""

import os  # Flush/sync writes and atomically replace files.
import tempfile  # Create temp files in the target directory.
from pathlib import Path  # Type-safe path handling for state files.

from .logging_utils import LOGGER  # Shared logger for cleanup warnings.


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
