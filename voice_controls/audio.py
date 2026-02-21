"""Responsibility: Build ffmpeg command lines for audio capture."""

from pathlib import Path

from .config import AUDIO_BACKEND, AUDIO_SOURCE


def build_ffmpeg_wav_capture_cmd(output_path: Path) -> list[str]:
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
