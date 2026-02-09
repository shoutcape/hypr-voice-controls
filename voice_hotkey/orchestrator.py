import tempfile
import time
import wave
from pathlib import Path

from .audio_stream import FFmpegPCMStream
from .config import (
    AUDIO_SAMPLE_RATE_HZ,
    SESSION_FRAME_MS,
    SESSION_MAX_SECONDS,
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


def run_endpointed_command_session(
    *,
    language: str,
    source: str,
    command_handler,
) -> int:
    vad = EndpointVAD(
        frame_ms=SESSION_FRAME_MS,
        rms_threshold=VAD_RMS_THRESHOLD,
        min_speech_ms=VAD_MIN_SPEECH_MS,
        end_silence_ms=VAD_END_SILENCE_MS,
    )

    notify("Voice", "Listening...")

    started_at = time.time()
    audio_bytes = bytearray()
    peak_rms = 0

    with FFmpegPCMStream(sample_rate_hz=AUDIO_SAMPLE_RATE_HZ, frame_ms=SESSION_FRAME_MS) as stream:
        while True:
            if time.time() - started_at >= SESSION_MAX_SECONDS:
                LOGGER.info("Endpointed command session timed out max_seconds=%s", SESSION_MAX_SECONDS)
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

    if not vad.has_started:
        notify("Voice", "No speech detected")
        LOGGER.info("Voice hotkey end status=no_speech source=%s peak_rms=%s", source, peak_rms)
        return 0

    with tempfile.TemporaryDirectory(prefix="voice-endpoint-") as tmpdir:
        audio_path = Path(tmpdir) / "capture.wav"
        _write_wav(audio_path, bytes(audio_bytes), AUDIO_SAMPLE_RATE_HZ)
        try:
            text, detected_language, language_probability = transcribe(audio_path, language=language, mode="command")
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
