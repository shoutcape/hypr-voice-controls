import os
from pathlib import Path

from .config import LANGUAGE_PATH
from .logging_utils import LOGGER


def write_private_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
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


def set_saved_dictation_language(language: str) -> None:
    write_private_text(LANGUAGE_PATH, language)


def toggle_saved_dictation_language() -> str:
    set_saved_dictation_language("en")
    return "en"
