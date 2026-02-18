import subprocess
import time
import unicodedata
import logging

from .config import (
    DICTATION_ALLOW_NEWLINES,
    DICTATION_INJECTOR,
    DICTATION_STRICT_TEXT,
    LOG_COMMAND_OUTPUT_MAX,
    NOTIFY_TIMEOUT_MS,
    TTS_COOLDOWN_MS,
    TTS_ENABLED,
    TTS_MAX_CHARS,
)
from .logging_utils import LOGGER
from .tooling import has_tool


_LAST_TTS_AT = 0.0
_LAST_TTS_TEXT = ""


def _truncate(value: str) -> str:
    if len(value) <= LOG_COMMAND_OUTPUT_MAX:
        return value
    return f"{value[:LOG_COMMAND_OUTPUT_MAX]}..."


def _notify_color(body: str) -> str:
    normalized = body.lower()
    error_signals = ("failed", "missing", "error", "unavailable", "no speech")
    success_signals = ("enabled", "disabled", "pasted", " -> ")
    if any(token in normalized for token in error_signals):
        return "rgb(ff6b6b)"
    if any(token in normalized for token in success_signals):
        return "rgb(87d37c)"
    return "rgb(88ccff)"


def speak_text(text: str) -> bool:
    clean = " ".join(text.split()).strip()
    if not clean:
        return False

    cmd: list[str] | None = None
    if has_tool("spd-say"):
        cmd = ["spd-say", clean]
    elif has_tool("espeak"):
        cmd = ["espeak", clean]

    if not cmd:
        return False

    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError as exc:
        LOGGER.debug("TTS speak failed cmd=%s err=%s", cmd, exc)
        return False


def _speak_feedback(text: str) -> None:
    if not TTS_ENABLED:
        return

    clean = " ".join(text.split()).strip()
    if not clean:
        return

    capped = clean[:TTS_MAX_CHARS]
    global _LAST_TTS_AT, _LAST_TTS_TEXT
    now = time.time()
    if capped == _LAST_TTS_TEXT and now - _LAST_TTS_AT < (TTS_COOLDOWN_MS / 1000.0):
        return

    if not speak_text(capped):
        return
    _LAST_TTS_AT = now
    _LAST_TTS_TEXT = capped


def notify(title: str, body: str) -> None:
    clean_title = " ".join(title.split()).strip() or "Voice"
    clean_body = " ".join(body.split()).strip()
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
            _speak_feedback(clean_body)
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
            _speak_feedback(clean_body)
        except Exception as exc:
            LOGGER.debug("notify-send failed: %s", exc)


def inject_text_into_focused_input(text: str) -> bool:
    safe_text = _sanitize_dictation_text(text)
    if not safe_text:
        LOGGER.warning("Dictation text empty after sanitization; skipping injection")
        return False

    debug_enabled = LOGGER.isEnabledFor(logging.DEBUG)

    if debug_enabled:
        raw_stats = _dictation_debug_stats(text)
        safe_stats = _dictation_debug_stats(safe_text)
        LOGGER.debug(
            "Dictation inject debug raw_len=%s safe_len=%s raw_qmarks=%s safe_qmarks=%s raw_tabs=%s safe_tabs=%s raw_newlines=%s safe_newlines=%s raw_controls=%s safe_controls=%s injector=%s",
            raw_stats["len"],
            safe_stats["len"],
            raw_stats["qmarks"],
            safe_stats["qmarks"],
            raw_stats["tabs"],
            safe_stats["tabs"],
            raw_stats["newlines"],
            safe_stats["newlines"],
            raw_stats["controls"],
            safe_stats["controls"],
            DICTATION_INJECTOR,
        )

    if DICTATION_INJECTOR == "wtype":
        if not has_tool("wtype"):
            LOGGER.warning("wtype not found; falling back to wl-copy + hyprctl paste path")
            return _inject_text_via_clipboard(safe_text)
        try:
            timeout = min(20, max(3, int(len(safe_text) / 80) + 2))
            proc = subprocess.run(["wtype", safe_text], check=False, capture_output=True, text=True, timeout=timeout)
        except Exception as exc:
            LOGGER.error("wtype injection failed: %s", exc)
            return _inject_text_via_clipboard(safe_text)

        if proc.returncode == 0:
            LOGGER.info("Dictation inject path=wtype result=ok text_len=%s qmarks=%s", len(safe_text), safe_text.count("?"))
            return True

        LOGGER.error(
            "wtype injection failed rc=%s stdout=%s stderr=%s",
            proc.returncode,
            _truncate(proc.stdout.strip()),
            _truncate(proc.stderr.strip()),
        )
        return _inject_text_via_clipboard(safe_text)

    return _inject_text_via_clipboard(safe_text)


def _sanitize_dictation_text(text: str) -> str:
    sanitized = text
    # Remove bidi/control formatting characters that can cause confusing edits.
    sanitized = "".join(ch for ch in sanitized if unicodedata.category(ch) != "Cf")

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

    if DICTATION_STRICT_TEXT:
        sanitized = sanitized.strip()

    return sanitized


def _dictation_debug_stats(text: str) -> dict[str, int]:
    controls = 0
    for ch in text:
        code = ord(ch)
        if (code < 32 and ch not in ("\t", "\n", "\r")) or code == 127:
            controls += 1
    return {
        "len": len(text),
        "qmarks": text.count("?"),
        "tabs": text.count("\t"),
        "newlines": text.count("\n") + text.count("\r"),
        "controls": controls,
    }


def _inject_text_via_clipboard(text: str) -> bool:
    if not has_tool("wl-copy"):
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

    cmd = ["hyprctl", "dispatch", "sendshortcut", "CTRL SHIFT,V,"]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=3)
    except Exception as exc:
        LOGGER.error("Paste attempt failed cmd=%s err=%s", cmd, exc)
        return False

    LOGGER.info(
        "Paste attempt cmd=%s rc=%s stdout=%s stderr=%s",
        cmd,
        proc.returncode,
        _truncate(proc.stdout.strip()),
        _truncate(proc.stderr.strip()),
    )
    if proc.returncode == 0:
        LOGGER.info("Dictation inject path=clipboard result=ok text_len=%s qmarks=%s", len(text), text.count("?"))
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
