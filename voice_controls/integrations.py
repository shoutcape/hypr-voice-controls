"""Responsibility: Integrate with desktop tools for notify and dictation paste."""

import shutil  # Standard-library shell utilities; shutil.which checks if a command exists in PATH.
import subprocess  # Run desktop integration commands (hyprctl, wl-copy, notify-send).
import unicodedata  # Inspect Unicode categories while sanitizing dictated text.
from functools import lru_cache  # Cache function results (Least Recently Used strategy).

from .config import (  # Runtime settings controlling notify behavior and dictation sanitization.
    DICTATION_ALLOW_NEWLINES,
    NOTIFY_TIMEOUT_MS,
)
from .logging_utils import LOGGER  # Shared logger for integration successes/failures.

NOTIFY_ERROR_SIGNALS = ("failed", "missing", "error", "unavailable", "no speech")
NOTIFY_SUCCESS_SIGNALS = ("pasted",)


@lru_cache(maxsize=None)
def has_tool(tool: str) -> bool:
    # LRU means Least Recently Used; this memoizes tool checks to avoid repeated shutil.which calls.
    return shutil.which(tool) is not None


def _notify_color(body: str) -> str:
    normalized = body.lower()
    if any(token in normalized for token in NOTIFY_ERROR_SIGNALS):
        return "rgb(ff6b6b)"
    if any(token in normalized for token in NOTIFY_SUCCESS_SIGNALS):
        return "rgb(87d37c)"
    return "rgb(88ccff)"


def notify(title: str, body: str) -> None:
    clean_title = " ".join(title.split()) or "Voice"
    clean_body = " ".join(body.split())
    if not clean_body:
        return

    if has_tool("hyprctl"):
        try:
            subprocess.run(
                ["hyprctl", "notify", "-1", str(NOTIFY_TIMEOUT_MS), _notify_color(clean_body), f"{clean_title}: {clean_body}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            return
        except Exception as exc:
            LOGGER.debug("hyprctl notify failed: %s", exc)

    if has_tool("notify-send"):
        try:
            subprocess.run(
                [
                    "notify-send",
                    "-a",
                    "voice-hotkey",
                    "-u",
                    "low",
                    "-t",
                    str(NOTIFY_TIMEOUT_MS),
                    "-h",
                    "string:x-canonical-private-synchronous:voice-hotkey",
                    clean_title,
                    clean_body,
                ],
                check=False,
                timeout=2,
            )
        except Exception as exc:
            LOGGER.debug("notify-send failed: %s", exc)


def inject_text_into_focused_input(text: str) -> bool:
    safe_text = _sanitize_dictation_text(text)
    if not safe_text:
        LOGGER.warning("Dictation text empty after sanitization; skipping injection")
        return False

    return _inject_text_via_clipboard(safe_text)


def _sanitize_dictation_text(text: str) -> str:
    if text.isascii():
        sanitized = text
    else:
        # Remove bidi/control formatting characters that can cause confusing edits.
        sanitized = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")

    # Normalize CRLF/CR to LF first.
    sanitized = sanitized.replace("\r\n", "\n").replace("\r", "\n")

    out_chars: list[str] = []
    for ch in sanitized:
        code = ord(ch)
        if ch == "\n":
            out_chars.append("\n" if DICTATION_ALLOW_NEWLINES else " ")
            continue
        if ch == "\t":
            out_chars.append(" ")
            continue
        if code < 32 or code == 127:
            out_chars.append(" ")
            continue
        out_chars.append(ch)

    sanitized = "".join(out_chars)
    if DICTATION_ALLOW_NEWLINES:
        sanitized = "\n".join(" ".join(line.split()) for line in sanitized.split("\n"))
    else:
        sanitized = " ".join(sanitized.split())

    return sanitized.strip()


def _inject_text_via_clipboard(text: str) -> bool:
    if not has_tool("wl-copy"):
        LOGGER.error("Cannot inject text: wl-copy not found")
        return False
    if not has_tool("hyprctl"):
        LOGGER.error("Cannot inject text: hyprctl not found")
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

    cmd = ["hyprctl", "dispatch", "sendshortcut", "CTRL SHIFT,V,"]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=3)
    except Exception as exc:
        LOGGER.error("Paste attempt failed cmd=%s err=%s", cmd, exc)
        return False

    if proc.returncode == 0:
        LOGGER.info("Paste attempt cmd=%s rc=0", cmd)
        LOGGER.info("Dictation inject path=clipboard result=ok text_len=%s qmarks=%s", len(text), text.count("?"))
        return True

    LOGGER.info(
        "Paste attempt failed cmd=%s rc=%s stdout=%s stderr=%s",
        cmd,
        proc.returncode,
        proc.stdout.strip(),
        proc.stderr.strip(),
    )
    return False
