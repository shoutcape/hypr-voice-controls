import json
import os
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path

from .audio_stream import FFmpegPCMStream
from .config import (
    AUDIO_SAMPLE_RATE_HZ,
    SESSION_FRAME_MS,
    SESSION_MAX_SECONDS,
    WAKE_SESSION_STATE_PATH,
    WAKE_PREROLL_MAX_AGE_MS,
    WAKE_PREROLL_PCM_PATH,
    VAD_END_SILENCE_MS,
    VAD_MIN_SPEECH_MS,
    VAD_RMS_THRESHOLD,
)
from .integrations import notify
from .logging_utils import LOGGER
from .state_utils import write_private_text
from .stt import transcribe
from .vad import EndpointVAD


@dataclass(frozen=True)
class _EndpointSessionConfig:
    session_max_seconds: int
    start_timeout_ms: int
    vad_rms_threshold: int
    vad_min_speech_ms: int
    vad_end_silence_ms: int


@dataclass(frozen=True)
class _EndpointCaptureResult:
    audio_bytes: bytes
    peak_rms: int
    had_preroll_speech: bool


def _write_wav(path: Path, pcm_data: bytes, sample_rate_hz: int) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate_hz)
        out.writeframes(pcm_data)


def _preroll_has_speech(pcm_data: bytes, rms_threshold: int) -> bool:
    if len(pcm_data) < 2:
        return False

    try:
        samples = memoryview(pcm_data).cast("h")
    except (TypeError, ValueError):
        return False

    if not samples:
        return False

    peak = max(abs(sample) for sample in samples)
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


def _resolve_endpoint_session_config(
    *,
    max_seconds: int | None,
    start_speech_timeout_ms: int | None,
    vad_rms_threshold: int | None,
    vad_min_speech_ms: int | None,
    vad_end_silence_ms: int | None,
) -> _EndpointSessionConfig:
    session_max_seconds = max_seconds if isinstance(max_seconds, int) and max_seconds > 0 else SESSION_MAX_SECONDS
    start_timeout_ms = start_speech_timeout_ms if isinstance(start_speech_timeout_ms, int) and start_speech_timeout_ms > 0 else 0
    active_vad_rms_threshold = vad_rms_threshold if isinstance(vad_rms_threshold, int) and vad_rms_threshold > 0 else VAD_RMS_THRESHOLD
    active_vad_min_speech_ms = vad_min_speech_ms if isinstance(vad_min_speech_ms, int) and vad_min_speech_ms > 0 else VAD_MIN_SPEECH_MS
    active_vad_end_silence_ms = vad_end_silence_ms if isinstance(vad_end_silence_ms, int) and vad_end_silence_ms > 0 else VAD_END_SILENCE_MS
    return _EndpointSessionConfig(
        session_max_seconds=session_max_seconds,
        start_timeout_ms=start_timeout_ms,
        vad_rms_threshold=active_vad_rms_threshold,
        vad_min_speech_ms=active_vad_min_speech_ms,
        vad_end_silence_ms=active_vad_end_silence_ms,
    )


def _capture_endpointed_audio(*, source: str, config: _EndpointSessionConfig, vad: EndpointVAD) -> _EndpointCaptureResult:
    started_at = time.time()
    speech_started_at: float | None = None
    last_speech_at: float | None = None
    preroll_bytes = _load_wake_preroll() if source == "wake_start" else b""
    audio_bytes = bytearray(preroll_bytes)
    peak_rms = 0
    had_preroll_speech = _preroll_has_speech(preroll_bytes, config.vad_rms_threshold)

    with FFmpegPCMStream(sample_rate_hz=AUDIO_SAMPLE_RATE_HZ, frame_ms=SESSION_FRAME_MS) as stream:
        read_timeout_ms = max(200, SESSION_FRAME_MS * 4)
        while True:
            now = time.time()
            elapsed_ms = int((now - started_at) * 1000)
            if speech_started_at is None and elapsed_ms >= config.session_max_seconds * 1000:
                LOGGER.info("Endpointed command session timed out before speech max_seconds=%s", config.session_max_seconds)
                break

            if config.start_timeout_ms > 0 and not vad.has_started and elapsed_ms >= config.start_timeout_ms:
                LOGGER.info("Endpointed command start timeout start_timeout_ms=%s", config.start_timeout_ms)
                break

            frame = stream.read_frame_with_timeout(read_timeout_ms)
            if not frame:
                if stream.is_running():
                    continue
                break

            audio_bytes.extend(frame)
            has_started, endpoint, rms = vad.update(frame)
            if rms > peak_rms:
                peak_rms = rms

            if has_started and speech_started_at is None:
                speech_started_at = now

            if rms >= config.vad_rms_threshold:
                last_speech_at = now

            if has_started and last_speech_at is not None:
                inactive_ms = int((now - last_speech_at) * 1000)
                if inactive_ms >= config.session_max_seconds * 1000:
                    LOGGER.info(
                        "Endpointed command session inactivity timeout max_seconds=%s inactive_ms=%s",
                        config.session_max_seconds,
                        inactive_ms,
                    )
                    break

            if has_started and endpoint:
                break

    return _EndpointCaptureResult(
        audio_bytes=bytes(audio_bytes),
        peak_rms=peak_rms,
        had_preroll_speech=had_preroll_speech,
    )


def _handle_no_speech_result(*, source: str, config: _EndpointSessionConfig, peak_rms: int) -> int:
    notify("Voice", "No speech detected")
    LOGGER.info(
        "Voice hotkey end status=no_speech source=%s peak_rms=%s vad_threshold=%s vad_min_speech_ms=%s vad_end_silence_ms=%s start_timeout_ms=%s session_max_seconds=%s",
        source,
        peak_rms,
        config.vad_rms_threshold,
        config.vad_min_speech_ms,
        config.vad_end_silence_ms,
        config.start_timeout_ms,
        config.session_max_seconds,
    )
    return 3


def _transcribe_and_dispatch(
    *,
    audio_bytes: bytes,
    language: str,
    source: str,
    stt_mode: str,
    command_handler,
) -> int:
    transcribe_started_at = time.time()
    with tempfile.TemporaryDirectory(prefix="voice-endpoint-") as tmpdir:
        audio_path = Path(tmpdir) / "capture.wav"
        _write_wav(audio_path, audio_bytes, AUDIO_SAMPLE_RATE_HZ)
        try:
            text, detected_language, language_probability = transcribe(audio_path, language=language, mode=stt_mode)
        except Exception as exc:
            notify("Voice", f"Transcription failed: {type(exc).__name__}")
            LOGGER.exception("Endpointed command transcription failed: %s", exc)
            LOGGER.info("Voice hotkey end status=transcription_failed source=%s", source)
            return 1

    transcribe_elapsed_ms = int((time.time() - transcribe_started_at) * 1000)
    LOGGER.info(
        "Endpointed transcription complete source=%s mode=%s duration_ms=%s audio_seconds=%.2f",
        source,
        stt_mode,
        transcribe_elapsed_ms,
        len(audio_bytes) / float(AUDIO_SAMPLE_RATE_HZ * 2),
    )

    return command_handler(
        text,
        source=source,
        language=detected_language,
        language_probability=language_probability,
    )


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
    config = _resolve_endpoint_session_config(
        max_seconds=max_seconds,
        start_speech_timeout_ms=start_speech_timeout_ms,
        vad_rms_threshold=vad_rms_threshold,
        vad_min_speech_ms=vad_min_speech_ms,
        vad_end_silence_ms=vad_end_silence_ms,
    )

    vad = EndpointVAD(
        frame_ms=SESSION_FRAME_MS,
        rms_threshold=config.vad_rms_threshold,
        min_speech_ms=config.vad_min_speech_ms,
        end_silence_ms=config.vad_end_silence_ms,
    )

    notify("Voice", prompt_text)

    wrote_wake_state = False
    if source == "wake_start":
        try:
            state = {
                "pid": os.getpid(),
                "pid_required_substrings": ["voice-hotkey.py", "--daemon"],
                "started_at": time.time(),
            }
            write_private_text(WAKE_SESSION_STATE_PATH, json.dumps(state))
            wrote_wake_state = True
        except Exception as exc:
            LOGGER.debug("Could not write wake session state: %s", exc)

    try:
        capture_started_at = time.time()
        capture_result = _capture_endpointed_audio(source=source, config=config, vad=vad)
        capture_elapsed_ms = int((time.time() - capture_started_at) * 1000)
        LOGGER.info(
            "Endpointed capture complete source=%s duration_ms=%s audio_seconds=%.2f speech_started=%s peak_rms=%s",
            source,
            capture_elapsed_ms,
            len(capture_result.audio_bytes) / float(AUDIO_SAMPLE_RATE_HZ * 2),
            vad.has_started,
            capture_result.peak_rms,
        )

        if not vad.has_started and not capture_result.had_preroll_speech:
            return _handle_no_speech_result(
                source=source,
                config=config,
                peak_rms=capture_result.peak_rms,
            )

        return _transcribe_and_dispatch(
            audio_bytes=capture_result.audio_bytes,
            language=language,
            source=source,
            stt_mode=stt_mode,
            command_handler=command_handler,
        )
    finally:
        if wrote_wake_state:
            try:
                WAKE_SESSION_STATE_PATH.unlink(missing_ok=True)
            except Exception as exc:
                LOGGER.debug("Could not clear wake session state: %s", exc)
