import shutil
import subprocess
import time

from .config import LOG_COMMAND_OUTPUT_MAX
from .logging_utils import LOGGER


def _truncate(value: str) -> str:
    if len(value) <= LOG_COMMAND_OUTPUT_MAX:
        return value
    return f"{value[:LOG_COMMAND_OUTPUT_MAX]}..."


def notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", "-a", "voice-hotkey", title, body], check=False, timeout=2)
        except Exception as exc:
            LOGGER.debug("notify-send failed: %s", exc)


def inject_text_into_focused_input(text: str) -> bool:
    if not shutil.which("wl-copy"):
        LOGGER.error("Cannot inject text: wl-copy not found")
        return False

    try:
        copy_proc = subprocess.run(
            ["wl-copy"],
            input=text,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except Exception as exc:
        LOGGER.error("Clipboard write failed: %s", exc)
        return False

    if copy_proc.returncode != 0:
        LOGGER.error("Clipboard write failed rc=%s", copy_proc.returncode)
        return False

    time.sleep(0.08)
    attempts = [
        ["hyprctl", "dispatch", "sendshortcut", "CTRL SHIFT,V,"],
        ["hyprctl", "dispatch", "sendshortcut", "SHIFT,Insert,"],
        ["hyprctl", "dispatch", "sendshortcut", "CTRL,V,"],
    ]

    for cmd in attempts:
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=3)
        except Exception as exc:
            LOGGER.error("Paste attempt failed cmd=%s err=%s", cmd, exc)
            continue
        LOGGER.info(
            "Paste attempt cmd=%s rc=%s stdout=%s stderr=%s",
            cmd,
            proc.returncode,
            _truncate(proc.stdout.strip()),
            _truncate(proc.stderr.strip()),
        )
        if proc.returncode == 0:
            return True

    return False


def run_command(argv: list[str]) -> bool:
    try:
        proc = subprocess.run(argv, check=False, timeout=8, capture_output=True, text=True)
    except Exception as exc:
        LOGGER.error("Command execution failed argv=%s err=%s", argv, exc)
        return False

    if proc.returncode != 0:
        LOGGER.error(
            "Command failed rc=%s argv=%s stdout=%s stderr=%s",
            proc.returncode,
            argv,
            _truncate(proc.stdout.strip()),
            _truncate(proc.stderr.strip()),
        )
    return proc.returncode == 0
