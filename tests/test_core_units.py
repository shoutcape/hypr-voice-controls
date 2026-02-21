"""Responsibility: Unit tests for config, audio, integrations, stt, and app helpers."""

import os
import socket
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class ConfigEnvIntTests(unittest.TestCase):
    def test_returns_default_when_var_missing(self) -> None:
        from voice_controls.config import env_int

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("_VOICE_TEST_INT", None)
            self.assertEqual(env_int("_VOICE_TEST_INT", 42), 42)

    def test_returns_parsed_value(self) -> None:
        from voice_controls.config import env_int

        with patch.dict(os.environ, {"_VOICE_TEST_INT": "7"}):
            self.assertEqual(env_int("_VOICE_TEST_INT", 0), 7)

    def test_returns_default_on_invalid_value(self) -> None:
        from voice_controls.config import env_int

        with patch.dict(os.environ, {"_VOICE_TEST_INT": "notanint"}):
            self.assertEqual(env_int("_VOICE_TEST_INT", 99), 99)


class ConfigEnvFloatTests(unittest.TestCase):
    def test_returns_default_when_var_missing(self) -> None:
        from voice_controls.config import env_float

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("_VOICE_TEST_FLOAT", None)
            self.assertAlmostEqual(env_float("_VOICE_TEST_FLOAT", 1.5), 1.5)

    def test_returns_parsed_value(self) -> None:
        from voice_controls.config import env_float

        with patch.dict(os.environ, {"_VOICE_TEST_FLOAT": "3.14"}):
            self.assertAlmostEqual(env_float("_VOICE_TEST_FLOAT", 0.0), 3.14)


class ConfigEnvBoolTests(unittest.TestCase):
    def test_truthy_values(self) -> None:
        from voice_controls.config import env_bool

        for value in ("1", "true", "True", "yes", "on"):
            with patch.dict(os.environ, {"_VOICE_TEST_BOOL": value}):
                self.assertTrue(env_bool("_VOICE_TEST_BOOL", False))

    def test_falsy_values(self) -> None:
        from voice_controls.config import env_bool

        for value in ("0", "false", "False", "no", "off"):
            with patch.dict(os.environ, {"_VOICE_TEST_BOOL": value}):
                self.assertFalse(env_bool("_VOICE_TEST_BOOL", True))


class BuildFfmpegCommandTests(unittest.TestCase):
    def test_command_starts_with_ffmpeg(self) -> None:
        from voice_controls.audio import build_ffmpeg_wav_capture_cmd

        cmd = build_ffmpeg_wav_capture_cmd(Path("/tmp/test.wav"))
        self.assertEqual(cmd[0], "ffmpeg")

    def test_command_includes_output_path(self) -> None:
        from voice_controls.audio import build_ffmpeg_wav_capture_cmd

        output = Path("/tmp/capture.wav")
        cmd = build_ffmpeg_wav_capture_cmd(output)
        self.assertIn(str(output), cmd)

    def test_command_sets_mono_and_16khz(self) -> None:
        from voice_controls.audio import build_ffmpeg_wav_capture_cmd

        cmd = build_ffmpeg_wav_capture_cmd(Path("/tmp/out.wav"))
        self.assertIn("-ac", cmd)
        self.assertIn("-ar", cmd)
        self.assertEqual(cmd[cmd.index("-ac") + 1], "1")
        self.assertEqual(cmd[cmd.index("-ar") + 1], "16000")


class SanitizeDictationTextTests(unittest.TestCase):
    def _sanitize(self, text: str, allow_newlines: bool = False) -> str:
        import voice_controls.integrations as integrations_mod

        original = integrations_mod.DICTATION_ALLOW_NEWLINES
        integrations_mod.DICTATION_ALLOW_NEWLINES = allow_newlines
        try:
            return integrations_mod._sanitize_dictation_text(text)
        finally:
            integrations_mod.DICTATION_ALLOW_NEWLINES = original

    def test_plain_ascii_passthrough(self) -> None:
        self.assertEqual(self._sanitize("hello world"), "hello world")

    def test_newline_default_and_opt_in(self) -> None:
        self.assertEqual(self._sanitize("hello\nworld"), "hello world")
        self.assertEqual(self._sanitize("hello\nworld", allow_newlines=True), "hello\nworld")

    def test_control_and_formatting_chars_sanitized(self) -> None:
        self.assertEqual(self._sanitize("hello\x01world"), "hello world")
        self.assertEqual(self._sanitize("hello\u202eworld"), "helloworld")


class ComputeTypeForDeviceTests(unittest.TestCase):
    def _compute_type(self, device: str, override: str | None = None) -> str:
        import voice_controls.stt as stt_mod

        original = stt_mod.COMPUTE_TYPE_OVERRIDE
        stt_mod.COMPUTE_TYPE_OVERRIDE = override
        try:
            return stt_mod.compute_type_for_device(device)
        finally:
            stt_mod.COMPUTE_TYPE_OVERRIDE = original

    def test_cuda_and_cpu_defaults(self) -> None:
        self.assertEqual(self._compute_type("cuda"), "float16")
        self.assertEqual(self._compute_type("cpu"), "int8")

    def test_override_takes_precedence(self) -> None:
        self.assertEqual(self._compute_type("cuda", override="int8"), "int8")


class RecvLineTests(unittest.TestCase):
    def _make_pair(self) -> tuple[socket.socket, socket.socket]:
        return socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)

    def _recv(self, data: bytes) -> str:
        from voice_controls.app import _recv_line

        server, client = self._make_pair()
        try:
            client.sendall(data)
            client.shutdown(socket.SHUT_WR)
            server.settimeout(1.0)
            return _recv_line(server)
        finally:
            server.close()
            client.close()

    def test_parses_valid_line(self) -> None:
        self.assertEqual(self._recv(b"dictate-start\n"), "dictate-start")

    def test_whitespace_only_line_raises(self) -> None:
        from voice_controls.app import _recv_line

        server, client = self._make_pair()
        try:
            client.sendall(b"   \n")
            client.shutdown(socket.SHUT_WR)
            server.settimeout(1.0)
            with self.assertRaises(ValueError):
                _recv_line(server)
        finally:
            server.close()
            client.close()

    def test_raises_on_request_too_large(self) -> None:
        from voice_controls.app import _recv_line

        server, client = self._make_pair()
        try:
            client.sendall(b"x" * 200 + b"\n")
            client.shutdown(socket.SHUT_WR)
            server.settimeout(1.0)
            with self.assertRaises(ValueError):
                _recv_line(server, max_bytes=10)
        finally:
            server.close()
            client.close()


class RequestDaemonFastFailTests(unittest.TestCase):
    def test_returns_immediately_when_daemon_spawn_fails(self) -> None:
        from voice_controls import app as app_mod

        with patch("voice_controls.app._send_daemon_request", side_effect=FileNotFoundError()), patch(
            "voice_controls.app.start_daemon", return_value=None
        ) as mock_start_daemon, patch("voice_controls.app.notify") as mock_notify:
            rc = app_mod.request_daemon("dictate-start")

        self.assertEqual(rc, 1)
        mock_start_daemon.assert_called_once_with()
        mock_notify.assert_called_once_with("Voice", "Voice daemon unavailable")

    def test_returns_1_when_ready_handshake_fails(self) -> None:
        from voice_controls import app as app_mod

        fake_proc = Mock()
        with patch("voice_controls.app._send_daemon_request", side_effect=FileNotFoundError()), patch(
            "voice_controls.app.start_daemon", return_value=fake_proc
        ), patch("voice_controls.app._wait_for_daemon_ready", return_value=False), patch("voice_controls.app.notify") as mock_notify:
            rc = app_mod.request_daemon("dictate-start")

        self.assertEqual(rc, 1)
        mock_notify.assert_called_once_with("Voice", "Voice daemon unavailable")


class IpcCompatibilityTests(unittest.TestCase):
    def test_parse_rc_line_accepts_json_payload(self) -> None:
        from voice_controls.app import _parse_rc_line

        self.assertEqual(_parse_rc_line('{"rc": 2}'), 2)

    def test_decode_request_line_accepts_json_payload(self) -> None:
        from voice_controls.app import _decode_request_line

        request, wants_json = _decode_request_line('{"input": "dictate-start"}')
        self.assertEqual(request, "dictate-start")
        self.assertTrue(wants_json)


class StopCaptureProcessTests(unittest.TestCase):
    def test_escalates_to_kill_when_wait_timeouts(self) -> None:
        import subprocess
        from voice_controls.app import _stop_capture_process

        proc = Mock(spec=subprocess.Popen)
        proc.pid = 12345
        proc.poll.return_value = None
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1),
            subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1),
            None,
        ]

        _stop_capture_process(proc)

        proc.send_signal.assert_called_once()
        proc.terminate.assert_called_once_with()
        proc.kill.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
