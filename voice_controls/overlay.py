import subprocess
import time

from .config import OVERLAY_ENABLED
from .logging_utils import LOGGER
from .tooling import has_tool

_LAST_OVERLAY_AT = 0.0
_MIN_INTERVAL_SECONDS = 0.25


def show_partial(text: str) -> None:
    if not OVERLAY_ENABLED:
        return
    clean = text.strip()
    if not clean:
        return

    global _LAST_OVERLAY_AT
    now = time.time()
    if now - _LAST_OVERLAY_AT < _MIN_INTERVAL_SECONDS:
        return
    _LAST_OVERLAY_AT = now

    if has_tool("hyprctl"):
        try:
            subprocess.run(
                ["hyprctl", "notify", "-1", "1200", "rgb(88ccff)", f"Voice: {clean}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1,
            )
            return
        except Exception as exc:
            LOGGER.debug("hyprctl overlay failed: %s", exc)

    if has_tool("notify-send"):
        try:
            subprocess.run(
                ["notify-send", "-a", "voice-hotkey", "Voice", clean],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1,
            )
        except Exception as exc:
            LOGGER.debug("notify-send overlay fallback failed: %s", exc)
