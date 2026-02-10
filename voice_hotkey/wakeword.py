import json
import time
from collections import deque
from pathlib import Path

from .audio_stream import FFmpegPCMStream
from .config import (
    WAKEWORD_COOLDOWN_MS,
    WAKEWORD_ENABLED_DEFAULT,
    WAKEWORD_FRAME_MS,
    WAKEWORD_MODEL_DIR,
    WAKEWORD_MODEL_FILE,
    WAKEWORD_MIN_CONSECUTIVE,
    WAKEWORD_NO_SPEECH_REARM_MS,
    WAKEWORD_PREROLL_MS,
    WAKEWORD_STATE_PATH,
    WAKEWORD_THRESHOLD,
    WAKE_PREROLL_PCM_PATH,
)
from .logging_utils import LOGGER


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
    try:
        payload = json.loads(WAKEWORD_STATE_PATH.read_text(encoding="utf-8"))
        enabled = payload.get("enabled")
        if isinstance(enabled, bool):
            return enabled
    except FileNotFoundError:
        pass
    except Exception as exc:
        LOGGER.warning("Could not read wakeword state in daemon: %s", exc)
    return WAKEWORD_ENABLED_DEFAULT


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

    from .app import request_daemon

    with FFmpegPCMStream(sample_rate_hz=16000, frame_ms=WAKEWORD_FRAME_MS) as stream:
        while True:
            frame = stream.read_frame()
            if not frame:
                time.sleep(0.02)
                continue
            ring.append(frame)

            scores = model.predict(np.frombuffer(frame, dtype=np.int16))
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
