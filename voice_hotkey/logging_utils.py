import logging
import os

from .config import LOG_PATH


def setup_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("voice-hotkey")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger

    handler = logging.FileHandler(LOG_PATH)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    try:
        os.chmod(LOG_PATH, 0o600)
    except Exception:
        pass
    return logger


LOGGER = setup_logger()
