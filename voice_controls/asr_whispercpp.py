import json
import mimetypes
import uuid
from pathlib import Path
from urllib import error, request

from .config import WHISPER_SERVER_TIMEOUT, WHISPER_SERVER_URL


def _multipart_form_data(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----voicehotkey-{uuid.uuid4().hex}"
    lines: list[bytes] = []

    for key, value in fields.items():
        lines.append(f"--{boundary}\r\n".encode("utf-8"))
        lines.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        lines.append(value.encode("utf-8"))
        lines.append(b"\r\n")

    filename = file_path.name
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    lines.append(f"--{boundary}\r\n".encode("utf-8"))
    lines.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    lines.append(file_path.read_bytes())
    lines.append(b"\r\n")
    lines.append(f"--{boundary}--\r\n".encode("utf-8"))

    return b"".join(lines), boundary


def transcribe_file(audio_path: Path, language: str | None = None) -> tuple[str, str, float]:
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        return "", language or "", 0.0

    fields = {
        "temperature": "0.0",
        "temperature_inc": "0.2",
        "response_format": "json",
    }
    if language:
        fields["language"] = language

    payload, boundary = _multipart_form_data(fields, "file", audio_path)
    req = request.Request(
        WHISPER_SERVER_URL,
        data=payload,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=WHISPER_SERVER_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        raise RuntimeError(f"whisper_server_http_{exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"whisper_server_unreachable: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip(), language or "", 0.0

    text = str(parsed.get("text", "")).strip()
    detected_language = str(parsed.get("language", language or ""))
    language_probability = parsed.get("language_probability")
    try:
        probability = float(language_probability) if language_probability is not None else 0.0
    except (TypeError, ValueError):
        probability = 0.0

    return text, detected_language, probability
