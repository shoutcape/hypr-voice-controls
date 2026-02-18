import ctypes
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from .asr_whispercpp import transcribe_file as transcribe_with_whisper_server
from .config import (
    ASR_BACKEND,
    COMMAND_MODEL_NAME,
    COMPUTE_TYPE_OVERRIDE,
    DEVICE_CANDIDATES,
    DICTATE_MODEL_NAME,
)
from .logging_utils import LOGGER

if TYPE_CHECKING:
    from faster_whisper import WhisperModel as FasterWhisperModel  # type: ignore[import-not-found]


WHISPER_MODELS: dict[tuple[str, str, str], "FasterWhisperModel"] = {}
WHISPER_MODELS_LOCK = threading.Lock()


def ensure_cuda_runtime_paths() -> None:
    lib_dirs = []

    try:
        import nvidia.cublas.lib  # type: ignore

        lib_dirs.append(list(nvidia.cublas.lib.__path__)[0])
    except Exception as exc:
        LOGGER.warning("Could not detect nvidia.cublas.lib path: %s", exc)

    try:
        import nvidia.cudnn.lib  # type: ignore

        lib_dirs.append(list(nvidia.cudnn.lib.__path__)[0])
    except Exception as exc:
        LOGGER.warning("Could not detect nvidia.cudnn.lib path: %s", exc)

    if not lib_dirs:
        return

    current = os.environ.get("LD_LIBRARY_PATH", "")
    current_parts = [p for p in current.split(":") if p]
    merged = []
    seen = set()

    for path in lib_dirs + current_parts:
        if path not in seen:
            seen.add(path)
            merged.append(path)

    os.environ["LD_LIBRARY_PATH"] = ":".join(merged)
    LOGGER.info("Updated LD_LIBRARY_PATH with CUDA runtime dirs: %s", lib_dirs)

    preload_candidates = [
        Path(lib_dirs[0]) / "libcublasLt.so.12" if len(lib_dirs) > 0 else None,
        Path(lib_dirs[0]) / "libcublas.so.12" if len(lib_dirs) > 0 else None,
        Path(lib_dirs[1]) / "libcudnn.so.9" if len(lib_dirs) > 1 else None,
    ]
    for lib_path in preload_candidates:
        if not lib_path or not lib_path.exists():
            continue
        try:
            ctypes.CDLL(str(lib_path), mode=ctypes.RTLD_GLOBAL)
            LOGGER.info("Preloaded CUDA runtime library: %s", lib_path)
        except Exception as exc:
            LOGGER.warning("Failed to preload CUDA runtime library %s: %s", lib_path, exc)


def compute_type_for_device(device: str) -> str:
    if COMPUTE_TYPE_OVERRIDE:
        return COMPUTE_TYPE_OVERRIDE
    if device.startswith("cuda"):
        return "float16"
    return "int8"


def command_model_name() -> str:
    return COMMAND_MODEL_NAME


def dictation_model_name() -> str:
    return DICTATE_MODEL_NAME


def get_whisper_model(model_name: str) -> "FasterWhisperModel":
    from faster_whisper import WhisperModel  # type: ignore[import-not-found]

    errors = []
    for device in DEVICE_CANDIDATES:
        compute_type = compute_type_for_device(device)
        key = (model_name, device, compute_type)
        with WHISPER_MODELS_LOCK:
            model = WHISPER_MODELS.get(key)
            if model is not None:
                return model

        try:
            if device.startswith("cuda"):
                ensure_cuda_runtime_paths()

            LOGGER.info(
                "Loading Whisper model name=%s device=%s compute_type=%s",
                model_name,
                device,
                compute_type,
            )
            model = WhisperModel(model_name, device=device, compute_type=compute_type)
            with WHISPER_MODELS_LOCK:
                WHISPER_MODELS[key] = model
            LOGGER.info("Whisper model loaded name=%s device=%s compute_type=%s", model_name, device, compute_type)
            return model
        except Exception as exc:
            errors.append(f"{device}/{compute_type}: {type(exc).__name__}: {exc}")
            LOGGER.warning(
                "Whisper model load failed name=%s device=%s compute_type=%s err=%s",
                model_name,
                device,
                compute_type,
                exc,
            )

    raise RuntimeError(f"Could not load Whisper model '{model_name}' on any device. Attempts: {'; '.join(errors)}")


def warm_model(model_name: str) -> None:
    if ASR_BACKEND == "whispercpp_server":
        return
    try:
        get_whisper_model(model_name)
    except Exception as exc:
        LOGGER.warning("Model warmup failed name=%s err=%s", model_name, exc)


def is_model_loaded(model_name: str) -> bool:
    if ASR_BACKEND == "whispercpp_server":
        return True
    with WHISPER_MODELS_LOCK:
        return any(key[0] == model_name for key in WHISPER_MODELS.keys())


def transcribe(audio_path: Path, language: str | None = None, mode: str = "command") -> tuple[str, str, float]:
    if ASR_BACKEND == "whispercpp_server":
        return transcribe_with_whisper_server(audio_path=audio_path, language=language)

    model_name = command_model_name() if mode == "command" else dictation_model_name()
    model = get_whisper_model(model_name)
    transcribe_kwargs = {
        "language": language,
        "vad_filter": True,
        "without_timestamps": True,
    }
    if mode == "command":
        transcribe_kwargs.update(
            {
                "beam_size": 1,
                "best_of": 1,
                "temperature": 0.0,
                "condition_on_previous_text": False,
            }
        )

    segments, info = model.transcribe(str(audio_path), **transcribe_kwargs)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return text, info.language, info.language_probability


def preload_models() -> None:
    if ASR_BACKEND == "whispercpp_server":
        return
    try:
        get_whisper_model(COMMAND_MODEL_NAME)
    except Exception as exc:
        LOGGER.warning("Model preload failed name=%s err=%s", COMMAND_MODEL_NAME, exc)
