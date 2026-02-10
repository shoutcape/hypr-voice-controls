import tempfile
import time
import wave
from array import array
from pathlib import Path

from .audio_stream import FFmpegPCMStream
from .config import (
    AUDIO_SAMPLE_RATE_HZ,
    SESSION_FRAME_MS,
    SESSION_MAX_SECONDS,
    WAKE_PREROLL_MAX_AGE_MS,
    WAKE_PREROLL_PCM_PATH,
    VAD_END_SILENCE_MS,
    VAD_MIN_SPEECH_MS,
    VAD_RMS_THRESHOLD,
)
from .integrations import notify
from .logging_utils import LOGGER
from .stt import transcribe
from .vad import EndpointVAD


def _write_wav(path: Path, pcm_data: bytes, sample_rate_hz: int) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate_hz)
        out.writeframes(pcm_data)


def _preroll_has_speech(pcm_data: bytes, rms_threshold: int) -> bool:
    if not pcm_data:
        return False
    samples = array("h")
    samples.frombytes(pcm_data)
    if not samples:
        return False
    peak = 0
    for sample in samples:
        magnitude = abs(sample)
        if magnitude > peak:
            peak = magnitude
    return peak >= rms_threshold


def _load_wake_preroll() -> bytes:
    try:
        if not WAKE_PREROLL_PCM_PATH.exists():
            return b""
        age_ms = (time.time() - WAKE_PREROLL_PCM_PATH.stat().st_mtime) * 1000
        if age_ms > WAKE_PREROLL_MAX_AGE_MS:
            return b""
        data = WAKE_PREROLL_PCM_PATH.read_bytes()
        return data
    except Exception as exc:
        LOGGER.debug("Could not read wake preroll PCM: %s", exc)
        return b""


def run_endpointed_command_session(
    *,
    language: str,
    source: str,
    command_handler,
    max_seconds: int | None = None,
    start_speech_timeout_ms: int | None = None,
    vad_rms_threshold: int | None = None,
    vad_min_speech_ms: int | None = None,
    vad_end_silence_ms: int | None = None,
    prompt_text: str = "Listening...",
    stt_mode: str = "command",
) -> int:
    session_max_seconds = max_seconds if isinstance(max_seconds, int) and max_seconds > 0 else SESSION_MAX_SECONDS
    start_timeout_ms = start_speech_timeout_ms if isinstance(start_speech_timeout_ms, int) and start_speech_timeout_ms > 0 else 0
    active_vad_rms_threshold = vad_rms_threshold if isinstance(vad_rms_threshold, int) and vad_rms_threshold > 0 else VAD_RMS_THRESHOLD
    active_vad_min_speech_ms = vad_min_speech_ms if isinstance(vad_min_speech_ms, int) and vad_min_speech_ms > 0 else VAD_MIN_SPEECH_MS
    active_vad_end_silence_ms = vad_end_silence_ms if isinstance(vad_end_silence_ms, int) and vad_end_silence_ms > 0 else VAD_END_SILENCE_MS

    vad = EndpointVAD(
        frame_ms=SESSION_FRAME_MS,
        rms_threshold=active_vad_rms_threshold,
        min_speech_ms=active_vad_min_speech_ms,
        end_silence_ms=active_vad_end_silence_ms,
    )

    notify("Voice", prompt_text)

    started_at = time.time()
    preroll_bytes = _load_wake_preroll() if source == "wake_start" else b""
    audio_bytes = bytearray(preroll_bytes)
    peak_rms = 0
    had_preroll_speech = _preroll_has_speech(preroll_bytes, active_vad_rms_threshold)

    with FFmpegPCMStream(sample_rate_hz=AUDIO_SAMPLE_RATE_HZ, frame_ms=SESSION_FRAME_MS) as stream:
        while True:
            elapsed_ms = int((time.time() - started_at) * 1000)
            if elapsed_ms >= session_max_seconds * 1000:
                LOGGER.info("Endpointed command session timed out max_seconds=%s", session_max_seconds)
                break

            if start_timeout_ms > 0 and not vad.has_started and elapsed_ms >= start_timeout_ms:
                LOGGER.info("Endpointed command start timeout start_timeout_ms=%s", start_timeout_ms)
                break

            frame = stream.read_frame()
            if not frame:
                break

            audio_bytes.extend(frame)
            has_started, endpoint, rms = vad.update(frame)
            if rms > peak_rms:
                peak_rms = rms

            if has_started and endpoint:
                break

    if not vad.has_started and not had_preroll_speech:
        notify("Voice", "No speech detected")
        LOGGER.info(
            "Voice hotkey end status=no_speech source=%s peak_rms=%s vad_threshold=%s vad_min_speech_ms=%s vad_end_silence_ms=%s start_timeout_ms=%s session_max_seconds=%s",
            source,
            peak_rms,
            active_vad_rms_threshold,
            active_vad_min_speech_ms,
            active_vad_end_silence_ms,
            start_timeout_ms,
            session_max_seconds,
        )
        return 3

    with tempfile.TemporaryDirectory(prefix="voice-endpoint-") as tmpdir:
        audio_path = Path(tmpdir) / "capture.wav"
        _write_wav(audio_path, bytes(audio_bytes), AUDIO_SAMPLE_RATE_HZ)
        try:
            text, detected_language, language_probability = transcribe(audio_path, language=language, mode=stt_mode)
        except Exception as exc:
            notify("Voice", f"Transcription failed: {type(exc).__name__}")
            LOGGER.exception("Endpointed command transcription failed: %s", exc)
            LOGGER.info("Voice hotkey end status=transcription_failed source=%s", source)
            return 1

    return command_handler(
        text,
        source=source,
        language=detected_language,
        language_probability=language_probability,
    )
