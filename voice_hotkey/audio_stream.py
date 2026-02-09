import subprocess

from .config import AUDIO_BACKEND, AUDIO_SOURCE
from .logging_utils import LOGGER


class FFmpegPCMStream:
    def __init__(self, *, sample_rate_hz: int, frame_ms: int) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.frame_ms = frame_ms
        self.frame_bytes = int(sample_rate_hz * frame_ms / 1000) * 2
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self._proc is not None:
            return

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            AUDIO_BACKEND,
            "-i",
            AUDIO_SOURCE,
            "-ac",
            "1",
            "-ar",
            str(self.sample_rate_hz),
            "-f",
            "s16le",
            "-",
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def read_frame(self) -> bytes:
        if self._proc is None or self._proc.stdout is None:
            return b""
        data = self._proc.stdout.read(self.frame_bytes)
        return data or b""

    def stop(self) -> None:
        if self._proc is None:
            return

        try:
            self._proc.terminate()
            self._proc.wait(timeout=1.5)
        except Exception:
            try:
                self._proc.kill()
            except Exception as exc:
                LOGGER.debug("Could not force-stop ffmpeg stream process: %s", exc)
        finally:
            self._proc = None

    def __enter__(self) -> "FFmpegPCMStream":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
