import shutil
import subprocess
import time
import json

from .config import (
    DICTATION_INJECTOR,
    LOG_COMMAND_OUTPUT_MAX,
    NOTIFY_TIMEOUT_MS,
    TTS_COOLDOWN_MS,
    TTS_ENABLED,
    TTS_MAX_CHARS,
)
from .logging_utils import LOGGER


_LAST_TTS_AT = 0.0
_LAST_TTS_TEXT = ""


def _run_pactl(args: list[str], *, timeout: int = 3) -> subprocess.CompletedProcess[str] | None:
    if not shutil.which("pactl"):
        return None

    try:
        return subprocess.run(
            ["pactl", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        LOGGER.debug("pactl command failed args=%s err=%s", args, exc)
        return None


def _list_discord_source_outputs() -> list[tuple[int, bool]]:
    proc = _run_pactl(["-f", "json", "list", "source-outputs"], timeout=4)
    if proc is None or proc.returncode != 0:
        return []

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        LOGGER.debug("Could not parse pactl JSON source-outputs payload")
        return []

    if not isinstance(payload, list):
        return []

    matches: list[tuple[int, bool]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        properties = item.get("properties")
        if not isinstance(properties, dict):
            continue
        media_class = str(properties.get("media.class", ""))
        if media_class != "Stream/Input/Audio":
            continue

        identifiers = [
            str(properties.get("pipewire.access.portal.app_id", "")),
            str(properties.get("application.process.binary", "")),
            str(properties.get("application.name", "")),
        ]
        if not any("discord" in value.lower() for value in identifiers):
            continue

        output_id = item.get("index")
        is_muted = bool(item.get("mute", False))
        if isinstance(output_id, int):
            matches.append((output_id, is_muted))

    return matches


def mute_discord_mic_streams_for_dictation() -> list[int]:
    muted_ids: list[int] = []
    for output_id, is_muted in _list_discord_source_outputs():
        if is_muted:
            continue
        proc = _run_pactl(["set-source-output-mute", str(output_id), "1"])
        if proc is None:
            continue
        if proc.returncode == 0:
            muted_ids.append(output_id)
        else:
            LOGGER.debug(
                "Failed to mute Discord source-output id=%s rc=%s stderr=%s",
                output_id,
                proc.returncode,
                _truncate(proc.stderr.strip()),
            )

    if muted_ids:
        LOGGER.info("Muted Discord source-outputs for dictation ids=%s", muted_ids)
    return muted_ids


def restore_discord_mic_streams_after_dictation(muted_ids: list[int]) -> None:
    for output_id in muted_ids:
        proc = _run_pactl(["set-source-output-mute", str(output_id), "0"])
        if proc is None:
            continue
        if proc.returncode != 0:
            LOGGER.debug(
                "Failed to unmute Discord source-output id=%s rc=%s stderr=%s",
                output_id,
                proc.returncode,
                _truncate(proc.stderr.strip()),
            )

    if muted_ids:
        LOGGER.info("Restored Discord source-outputs after dictation ids=%s", muted_ids)


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

    cmd: list[str] | None = None
    if shutil.which("spd-say"):
        cmd = ["spd-say", capped]
    elif shutil.which("espeak"):
        cmd = ["espeak", capped]

    if not cmd:
        return

    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _LAST_TTS_AT = now
        _LAST_TTS_TEXT = capped
    except Exception as exc:
        LOGGER.debug("TTS feedback failed cmd=%s err=%s", cmd, exc)


def notify(title: str, body: str) -> None:
    clean_title = " ".join(title.split()).strip() or "Voice"
    clean_body = " ".join(body.split()).strip()
    if not clean_body:
        return

    if shutil.which("hyprctl"):
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

    if shutil.which("notify-send"):
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
    if DICTATION_INJECTOR == "wtype":
        if not shutil.which("wtype"):
            LOGGER.warning("wtype not found; falling back to wl-copy + hyprctl paste path")
            return _inject_text_via_clipboard(text)
        try:
            timeout = min(20, max(3, int(len(text) / 80) + 2))
            proc = subprocess.run(["wtype", text], check=False, capture_output=True, text=True, timeout=timeout)
        except Exception as exc:
            LOGGER.error("wtype injection failed: %s", exc)
            return _inject_text_via_clipboard(text)

        if proc.returncode == 0:
            return True

        LOGGER.error(
            "wtype injection failed rc=%s stdout=%s stderr=%s",
            proc.returncode,
            _truncate(proc.stdout.strip()),
            _truncate(proc.stderr.strip()),
        )
        return _inject_text_via_clipboard(text)

    return _inject_text_via_clipboard(text)


def _inject_text_via_clipboard(text: str) -> bool:
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
