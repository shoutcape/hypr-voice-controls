import time
from collections import deque
import math
import shutil
import subprocess

from .audio_stream import FFmpegPCMStream
from .config import (
    COMMAND_STATE_PATH,
    DICTATE_STATE_PATH,
    WAKEWORD_COOLDOWN_MS,
    WAKEWORD_FRAME_MS,
    WAKEWORD_MODEL_DIR,
    WAKEWORD_MODEL_FILE,
    WAKEWORD_MIN_CONSECUTIVE,
    WAKEWORD_NO_SPEECH_REARM_MS,
    WAKEWORD_PREROLL_MS,
    WAKEWORD_THRESHOLD,
    WAKE_DAEMON_RESPONSE_TIMEOUT_SECONDS,
    WAKE_PREROLL_PCM_PATH,
    WAKE_CHIME_ENABLED,
    WAKE_CHIME_FILE,
    WAKE_CHIME_VOLUME,
    WAKE_SESSION_STATE_PATH,
)
from .logging_utils import LOGGER
from .orchestrator import CANCELLED_EXIT_CODE, NO_SPEECH_EXIT_CODE
from .state_utils import is_capture_state_active, read_wakeword_enabled_cached


_WAKEWORD_ENABLED_CACHE: bool | None = None
_WAKEWORD_ENABLED_MTIME_NS: int | None = None
_LAST_ACTIVE_CAPTURE_LOG_AT = 0.0
_WAKE_TRIGGER_OUTCOME_COUNTS: dict[str, int] = {}
WAKE_DAEMON_CONNECT_TIMEOUT_SECONDS = 0.2
WAKE_DAEMON_RETRIES = 1
WAKEWORD_ERROR_REARM_MS = 1200


def _resolve_model_paths() -> list[str]:
    if WAKEWORD_MODEL_FILE and WAKEWORD_MODEL_FILE.exists():
        if WAKEWORD_MODEL_FILE.suffix.lower() != ".onnx":
            LOGGER.warning("Wakeword model file is not .onnx: %s", WAKEWORD_MODEL_FILE)
        return [str(WAKEWORD_MODEL_FILE)]

    if not WAKEWORD_MODEL_DIR.exists():
        return []

    discovered: list[str] = [str(path) for path in sorted(WAKEWORD_MODEL_DIR.glob("*.onnx"))]
    if not discovered:
        tflite_models = sorted(WAKEWORD_MODEL_DIR.glob("*.tflite"))
        if tflite_models:
            LOGGER.warning("Found only .tflite wakeword models; provide .onnx for current runtime")
    return discovered


def _wakeword_enabled() -> bool:
    global _WAKEWORD_ENABLED_CACHE, _WAKEWORD_ENABLED_MTIME_NS

    enabled, mtime_ns = read_wakeword_enabled_cached(
        _WAKEWORD_ENABLED_CACHE,
        _WAKEWORD_ENABLED_MTIME_NS,
    )
    _WAKEWORD_ENABLED_CACHE = enabled
    _WAKEWORD_ENABLED_MTIME_NS = mtime_ns
    return enabled


def _manual_capture_active(now: float) -> bool:
    return (
        is_capture_state_active(DICTATE_STATE_PATH, now=now)
        or is_capture_state_active(COMMAND_STATE_PATH, now=now)
        or is_capture_state_active(WAKE_SESSION_STATE_PATH, now=now)
    )


def _log_active_capture_skip(now: float) -> None:
    global _LAST_ACTIVE_CAPTURE_LOG_AT
    if now - _LAST_ACTIVE_CAPTURE_LOG_AT < 2.0:
        return
    _LAST_ACTIVE_CAPTURE_LOG_AT = now
    LOGGER.info("Wakeword trigger skipped while manual capture is active")


def _restart_wakeword_stream(stream: FFmpegPCMStream, reason: str) -> None:
    LOGGER.warning("Wakeword stream %s; restarting ffmpeg capture", reason)
    stream.stop()
    time.sleep(0.05)
    stream.start()


def _write_wake_preroll(ring: deque[bytes]) -> None:
    try:
        WAKE_PREROLL_PCM_PATH.parent.mkdir(parents=True, exist_ok=True)
        WAKE_PREROLL_PCM_PATH.write_bytes(b"".join(ring))
    except Exception as exc:
        LOGGER.warning("Could not write wake preroll PCM: %s", exc)


def _play_wake_chime() -> None:
    if not WAKE_CHIME_ENABLED:
        return

    cmd: list[str] | None = None

    volume = max(0.0, min(1.0, WAKE_CHIME_VOLUME))

    if WAKE_CHIME_FILE:
        if shutil.which("paplay"):
            pulse_volume = str(max(0, min(65536, int(65536 * volume))))
            cmd = ["paplay", f"--volume={pulse_volume}", WAKE_CHIME_FILE]
        elif shutil.which("pw-play"):
            cmd = ["pw-play", f"--volume={volume:.3f}", WAKE_CHIME_FILE]
        else:
            LOGGER.warning("Wake chime file configured but no paplay/pw-play available")
            return
    elif shutil.which("canberra-gtk-play"):
        db = "-60.0" if volume <= 0.0 else f"{max(-60.0, min(0.0, 20.0 * math.log10(volume))):.1f}"
        cmd = ["canberra-gtk-play", "-i", "bell", "-d", "voice-hotkey", "-V", db]

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
    except Exception as exc:
        LOGGER.debug("Wake chime playback failed cmd=%s err=%s", cmd, exc)


def _should_trigger_wake(*, now: float, last_trigger_at: float, rearm_until: float) -> bool:
    if now < rearm_until:
        return False
    if (now - last_trigger_at) * 1000 < WAKEWORD_COOLDOWN_MS:
        return False
    if not _wakeword_enabled():
        return False
    return True


def _handle_wake_trigger(
    *,
    stream: FFmpegPCMStream,
    ring: deque[bytes],
    request_daemon,
) -> int:
    _write_wake_preroll(ring)
    stream.stop()
    try:
        rc = request_daemon(
            "wake-start",
            auto_start=False,
            connect_timeout=WAKE_DAEMON_CONNECT_TIMEOUT_SECONDS,
            response_timeout=WAKE_DAEMON_RESPONSE_TIMEOUT_SECONDS,
            retries=WAKE_DAEMON_RETRIES,
        )
    finally:
        stream.start()
    return rc


def _apply_wake_trigger_result(*, now: float, rc: int, last_trigger_at: float, rearm_until: float) -> tuple[float, float]:
    reason = _classify_wake_trigger_result(rc)

    if rc == NO_SPEECH_EXIT_CODE:
        last_trigger_at = now
        rearm_until = now + (WAKEWORD_NO_SPEECH_REARM_MS / 1000.0)
        _record_wake_trigger_outcome(reason=reason, rc=rc, rearm_ms=WAKEWORD_NO_SPEECH_REARM_MS)
        LOGGER.info(
            "Wakeword trigger resulted in no_speech; rearming after %sms",
            WAKEWORD_NO_SPEECH_REARM_MS,
        )
        return last_trigger_at, rearm_until

    if rc == CANCELLED_EXIT_CODE:
        rearm_until = now + (WAKEWORD_ERROR_REARM_MS / 1000.0)
        _record_wake_trigger_outcome(reason=reason, rc=rc, rearm_ms=WAKEWORD_ERROR_REARM_MS)
        LOGGER.info("Wakeword trigger cancelled; rearming after %sms", WAKEWORD_ERROR_REARM_MS)
        return last_trigger_at, rearm_until

    if rc != 0:
        rearm_until = now + (WAKEWORD_ERROR_REARM_MS / 1000.0)
        _record_wake_trigger_outcome(reason=reason, rc=rc, rearm_ms=WAKEWORD_ERROR_REARM_MS)
        LOGGER.warning("Wakeword trigger request failed rc=%s rearm_ms=%s", rc, WAKEWORD_ERROR_REARM_MS)
        return last_trigger_at, rearm_until

    last_trigger_at = now
    _record_wake_trigger_outcome(reason=reason, rc=rc, rearm_ms=0)
    return last_trigger_at, rearm_until


def _classify_wake_trigger_result(rc: int) -> str:
    if rc == 0:
        return "ok"
    if rc == NO_SPEECH_EXIT_CODE:
        return "no_speech"
    if rc == CANCELLED_EXIT_CODE:
        return "cancelled"
    if rc == 2:
        return "stale_daemon"
    if rc == 1:
        return "busy_or_error"
    return f"rc_{rc}"


def _record_wake_trigger_outcome(*, reason: str, rc: int, rearm_ms: int) -> None:
    count = _WAKE_TRIGGER_OUTCOME_COUNTS.get(reason, 0) + 1
    _WAKE_TRIGGER_OUTCOME_COUNTS[reason] = count
    LOGGER.info(
        "Wakeword trigger outcome reason=%s rc=%s rearm_ms=%s count=%s",
        reason,
        rc,
        rearm_ms,
        count,
    )


def run_wakeword_daemon() -> int:
    try:
        import numpy as np
        from openwakeword.model import Model
    except Exception as exc:
        LOGGER.error("Wakeword daemon dependencies missing: %s", exc)
        return 1

    model_paths = _resolve_model_paths()
    if not model_paths:
        LOGGER.error("Wakeword daemon could not find model files in %s", WAKEWORD_MODEL_DIR)
        return 1

    LOGGER.info(
        "Wakeword daemon starting model_paths=%s threshold=%.3f min_consecutive=%s cooldown_ms=%s frame_ms=%s",
        model_paths,
        WAKEWORD_THRESHOLD,
        WAKEWORD_MIN_CONSECUTIVE,
        WAKEWORD_COOLDOWN_MS,
        WAKEWORD_FRAME_MS,
    )
    try:
        model = Model(wakeword_model_paths=model_paths)
    except TypeError:
        model = Model(wakeword_models=model_paths)
    last_trigger_at = 0.0
    rearm_until = 0.0
    streak_by_name: dict[str, int] = {}
    preroll_frames = max(1, WAKEWORD_PREROLL_MS // max(1, WAKEWORD_FRAME_MS))
    ring: deque[bytes] = deque(maxlen=preroll_frames)
    empty_frame_streak = 0
    empty_frame_restart_threshold = max(8, 2000 // max(1, WAKEWORD_FRAME_MS))

    from .app import request_daemon

    with FFmpegPCMStream(sample_rate_hz=16000, frame_ms=WAKEWORD_FRAME_MS) as stream:
        read_timeout_ms = max(120, WAKEWORD_FRAME_MS * 3)
        while True:
            frame = stream.read_frame_with_timeout(read_timeout_ms)
            if not frame:
                if not stream.is_running():
                    _restart_wakeword_stream(stream, "exited")
                    empty_frame_streak = 0
                    continue
                empty_frame_streak += 1
                if empty_frame_streak >= empty_frame_restart_threshold:
                    _restart_wakeword_stream(stream, "stalled")
                    empty_frame_streak = 0
                time.sleep(0.02)
                continue
            empty_frame_streak = 0
            ring.append(frame)

            try:
                scores = model.predict(np.frombuffer(frame, dtype=np.int16))
            except Exception as exc:
                LOGGER.warning("Wakeword model predict failed: %s", exc)
                continue
            if not scores:
                continue

            score_name, score = max(scores.items(), key=lambda item: item[1])
            if score < WAKEWORD_THRESHOLD:
                streak_by_name[score_name] = 0
                continue

            streak = streak_by_name.get(score_name, 0) + 1
            streak_by_name[score_name] = streak
            if streak < WAKEWORD_MIN_CONSECUTIVE:
                continue

            now = time.time()
            if _manual_capture_active(now):
                streak_by_name[score_name] = 0
                ring.clear()
                _log_active_capture_skip(now)
                continue

            if not _should_trigger_wake(now=now, last_trigger_at=last_trigger_at, rearm_until=rearm_until):
                continue

            LOGGER.info("Wakeword detected name=%s score=%.3f", score_name, score)
            _play_wake_chime()
            streak_by_name[score_name] = 0
            rc = _handle_wake_trigger(
                stream=stream,
                ring=ring,
                request_daemon=request_daemon,
            )
            last_trigger_at, rearm_until = _apply_wake_trigger_result(
                now=now,
                rc=rc,
                last_trigger_at=last_trigger_at,
                rearm_until=rearm_until,
            )
