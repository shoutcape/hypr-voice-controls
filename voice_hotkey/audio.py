import os
import signal
import subprocess
import time
from pathlib import Path

from .config import AUDIO_BACKEND, AUDIO_SECONDS, AUDIO_SOURCE
from .logging_utils import LOGGER


def build_ffmpeg_wav_capture_cmd(output_path: Path, *, duration_seconds: int | None = None) -> list[str]:
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        AUDIO_BACKEND,
        "-i",
        AUDIO_SOURCE,
    ]
    if duration_seconds is not None:
        cmd.extend(["-t", str(duration_seconds)])
    cmd.extend(
        [
            "-ac",
            "1",
            "-ar",
            "16000",
            str(output_path),
        ]
    )
    return cmd


def record_clip(output_path: Path, duration_seconds: int = AUDIO_SECONDS) -> bool:
    cmd = build_ffmpeg_wav_capture_cmd(output_path, duration_seconds=duration_seconds)
    try:
        proc = subprocess.run(cmd, check=False, timeout=duration_seconds + 4, capture_output=True, text=True)
    except Exception as exc:
        LOGGER.error("Mic capture process failed: %s", exc)
        return False

    if proc.returncode != 0:
        LOGGER.error("Mic capture failed rc=%s stderr=%s", proc.returncode, proc.stderr.strip())
        return False

    if not output_path.exists() or output_path.stat().st_size == 0:
        LOGGER.error("Mic capture produced empty audio file: %s", output_path)
        return False

    return True


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False

    proc_path = Path(f"/proc/{pid}")
    if not proc_path.exists():
        return False

    stat_path = proc_path / "stat"
    try:
        stat_raw = stat_path.read_text(encoding="utf-8", errors="ignore")
        if ") " in stat_raw:
            state = stat_raw.split(") ", 1)[1][:1]
            if state == "Z":
                return False
    except (OSError, ValueError, IndexError) as exc:
        LOGGER.debug("Could not parse process stat pid=%s path=%s err=%s", pid, stat_path, exc)

    return True


def wait_for_pid_exit(pid: int, timeout_seconds: float) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_alive(pid)


def pid_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        if not raw:
            return ""
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def pid_cmdline_contains(pid: int, required_substrings: list[str] | None = None) -> bool:
    if not required_substrings:
        return True

    cmdline = pid_cmdline(pid)
    if not cmdline:
        return False
    lowered = cmdline.lower()
    return all(token.lower() in lowered for token in required_substrings)


def stop_recording_pid(pid: int, label: str, required_substrings: list[str] | None = None) -> None:
    if not pid_alive(pid):
        LOGGER.info("%s process already exited pid=%s", label, pid)
        return

    if not pid_cmdline_contains(pid, required_substrings):
        LOGGER.warning(
            "%s process identity mismatch; refusing to signal pid=%s required=%s cmdline=%r",
            label,
            pid,
            required_substrings,
            pid_cmdline(pid),
        )
        return

    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        LOGGER.info("%s process disappeared before SIGINT pid=%s", label, pid)
        return
    except Exception as exc:
        LOGGER.warning("Could not signal %s pid=%s err=%s", label, pid, exc)
        return

    if wait_for_pid_exit(pid, 1.5):
        LOGGER.info("%s process exited after SIGINT pid=%s", label, pid)
        return

    LOGGER.warning("%s process still alive after SIGINT; sending SIGTERM pid=%s", label, pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        LOGGER.info("%s process disappeared before SIGTERM pid=%s", label, pid)
        return
    except Exception as exc:
        LOGGER.warning("Could not SIGTERM %s pid=%s err=%s", label, pid, exc)
        return

    if wait_for_pid_exit(pid, 1.0):
        LOGGER.info("%s process exited after SIGTERM pid=%s", label, pid)
        return

    LOGGER.error("%s process still alive; sending SIGKILL pid=%s", label, pid)
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception as exc:
        LOGGER.error("Could not SIGKILL %s pid=%s err=%s", label, pid, exc)

    wait_for_pid_exit(pid, 0.5)
