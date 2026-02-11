import time
from collections import deque

from .audio_stream import FFmpegPCMStream
from .config import (
    WAKEWORD_COOLDOWN_MS,
    WAKEWORD_FRAME_MS,
    WAKEWORD_MODEL_DIR,
    WAKEWORD_MODEL_FILE,
    WAKEWORD_MIN_CONSECUTIVE,
    WAKEWORD_NO_SPEECH_REARM_MS,
    WAKEWORD_PREROLL_MS,
    WAKEWORD_THRESHOLD,
    WAKE_PREROLL_PCM_PATH,
)
from .logging_utils import LOGGER
from .state_utils import read_wakeword_enabled_cached


_WAKEWORD_ENABLED_CACHE: bool | None = None
_WAKEWORD_ENABLED_MTIME_NS: int | None = None


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
        def _restart_stream(reason: str) -> None:
            nonlocal empty_frame_streak
            LOGGER.warning("Wakeword stream %s; restarting ffmpeg capture", reason)
            stream.stop()
            time.sleep(0.05)
            stream.start()
            empty_frame_streak = 0

        read_timeout_ms = max(120, WAKEWORD_FRAME_MS * 3)
        while True:
            frame = stream.read_frame_with_timeout(read_timeout_ms)
            if not frame:
                if not stream.is_running():
                    _restart_stream("exited")
                    continue
                empty_frame_streak += 1
                if empty_frame_streak >= empty_frame_restart_threshold:
                    _restart_stream("stalled")
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
            if now < rearm_until:
                continue

            if (now - last_trigger_at) * 1000 < WAKEWORD_COOLDOWN_MS:
                continue

            if not _wakeword_enabled():
                continue

            LOGGER.info("Wakeword detected name=%s score=%.3f", score_name, score)
            streak_by_name[score_name] = 0
            try:
                WAKE_PREROLL_PCM_PATH.parent.mkdir(parents=True, exist_ok=True)
                WAKE_PREROLL_PCM_PATH.write_bytes(b"".join(ring))
            except Exception as exc:
                LOGGER.warning("Could not write wake preroll PCM: %s", exc)
            stream.stop()
            try:
                rc = request_daemon("wake-start")
            finally:
                stream.start()
            if rc == 3:
                last_trigger_at = now
                rearm_until = now + (WAKEWORD_NO_SPEECH_REARM_MS / 1000.0)
                LOGGER.info(
                    "Wakeword trigger resulted in no_speech; rearming after %sms",
                    WAKEWORD_NO_SPEECH_REARM_MS,
                )
                continue
            if rc != 0:
                LOGGER.warning("Wakeword trigger request failed rc=%s", rc)
                continue
            last_trigger_at = now
