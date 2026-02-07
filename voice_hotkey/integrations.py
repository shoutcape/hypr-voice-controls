import shutil
import subprocess
import time

from .logging_utils import LOGGER


def notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "voice-hotkey", title, body], check=False, timeout=2)


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
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=3)
        LOGGER.info(
            "Paste attempt cmd=%s rc=%s stdout=%s stderr=%s",
            cmd,
            proc.returncode,
            proc.stdout.strip(),
            proc.stderr.strip(),
        )
        if proc.returncode == 0:
            return True

    return False


def run_command(argv: list[str]) -> bool:
    proc = subprocess.run(argv, check=False, timeout=8, capture_output=True, text=True)
    if proc.returncode != 0:
        LOGGER.error(
            "Command failed rc=%s argv=%s stdout=%s stderr=%s",
            proc.returncode,
            argv,
            proc.stdout.strip(),
            proc.stderr.strip(),
        )
    return proc.returncode == 0
