"""Responsibility: Unit tests for config, audio, integrations, stt, and state_utils modules."""

import os
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, mock_open, patch


# ---------------------------------------------------------------------------
# config module: env_int, env_float, env_bool
# ---------------------------------------------------------------------------

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

    def test_returns_default_on_empty_string(self) -> None:
        from voice_controls.config import env_int
        with patch.dict(os.environ, {"_VOICE_TEST_INT": ""}):
            self.assertEqual(env_int("_VOICE_TEST_INT", 5), 5)


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

    def test_returns_default_on_invalid_value(self) -> None:
        from voice_controls.config import env_float
        with patch.dict(os.environ, {"_VOICE_TEST_FLOAT": "bad"}):
            self.assertAlmostEqual(env_float("_VOICE_TEST_FLOAT", 2.0), 2.0)


class ConfigEnvBoolTests(unittest.TestCase):
    def test_returns_default_when_var_missing(self) -> None:
        from voice_controls.config import env_bool
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("_VOICE_TEST_BOOL", None)
            self.assertFalse(env_bool("_VOICE_TEST_BOOL", False))
            self.assertTrue(env_bool("_VOICE_TEST_BOOL", True))

    def test_truthy_values(self) -> None:
        from voice_controls.config import env_bool
        for value in ("1", "true", "True", "TRUE", "yes", "YES", "on", "ON"):
            with patch.dict(os.environ, {"_VOICE_TEST_BOOL": value}):
                self.assertTrue(env_bool("_VOICE_TEST_BOOL", False), msg=f"Expected True for {value!r}")

    def test_falsy_values(self) -> None:
        from voice_controls.config import env_bool
        for value in ("0", "false", "False", "FALSE", "no", "NO", "off", "OFF"):
            with patch.dict(os.environ, {"_VOICE_TEST_BOOL": value}):
                self.assertFalse(env_bool("_VOICE_TEST_BOOL", True), msg=f"Expected False for {value!r}")

    def test_returns_default_on_invalid_value(self) -> None:
        from voice_controls.config import env_bool
        with patch.dict(os.environ, {"_VOICE_TEST_BOOL": "maybe"}):
            self.assertFalse(env_bool("_VOICE_TEST_BOOL", False))
            self.assertTrue(env_bool("_VOICE_TEST_BOOL", True))


# ---------------------------------------------------------------------------
# audio module: build_ffmpeg_wav_capture_cmd, pid_alive
# ---------------------------------------------------------------------------

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

    def test_command_sets_mono_channel(self) -> None:
        from voice_controls.audio import build_ffmpeg_wav_capture_cmd
        cmd = build_ffmpeg_wav_capture_cmd(Path("/tmp/out.wav"))
        # -ac 1 sets mono
        self.assertIn("-ac", cmd)
        ac_index = cmd.index("-ac")
        self.assertEqual(cmd[ac_index + 1], "1")

    def test_command_sets_16khz_sample_rate(self) -> None:
        from voice_controls.audio import build_ffmpeg_wav_capture_cmd
        cmd = build_ffmpeg_wav_capture_cmd(Path("/tmp/out.wav"))
        self.assertIn("-ar", cmd)
        ar_index = cmd.index("-ar")
        self.assertEqual(cmd[ar_index + 1], "16000")

    def test_command_overwrite_flag_present(self) -> None:
        from voice_controls.audio import build_ffmpeg_wav_capture_cmd
        cmd = build_ffmpeg_wav_capture_cmd(Path("/tmp/out.wav"))
        self.assertIn("-y", cmd)

    def test_command_uses_configured_audio_backend(self) -> None:
        import voice_controls.audio as audio_mod
        from voice_controls.audio import build_ffmpeg_wav_capture_cmd
        with patch.object(audio_mod, "AUDIO_BACKEND", "alsa"):
            cmd = build_ffmpeg_wav_capture_cmd(Path("/tmp/out.wav"))
            self.assertIn("alsa", cmd)


class PidAliveTests(unittest.TestCase):
    def test_returns_false_for_zero_pid(self) -> None:
        from voice_controls.audio import pid_alive
        self.assertFalse(pid_alive(0))

    def test_returns_false_for_negative_pid(self) -> None:
        from voice_controls.audio import pid_alive
        self.assertFalse(pid_alive(-1))

    def test_returns_true_for_own_pid(self) -> None:
        from voice_controls.audio import pid_alive
        self.assertTrue(pid_alive(os.getpid()))

    def test_returns_false_for_nonexistent_pid(self) -> None:
        from voice_controls.audio import pid_alive
        # PID 2**22 is extremely unlikely to exist; /proc/<pid>/stat won't
        self.assertFalse(pid_alive(2 ** 22))

    def test_returns_false_for_zombie_process(self) -> None:
        """A process in Z state should report as not alive."""
        from voice_controls.audio import pid_alive
        fake_stat = "123 (ffmpeg) Z 1 123 123 0 -1 4194304"
        with patch("builtins.open", mock_open(read_data=fake_stat)):
            with patch("pathlib.Path.read_text", return_value=fake_stat):
                self.assertFalse(pid_alive(123))


# ---------------------------------------------------------------------------
# integrations module: _sanitize_dictation_text
# ---------------------------------------------------------------------------

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

    def test_strips_leading_trailing_whitespace(self) -> None:
        self.assertEqual(self._sanitize("  hello  "), "hello")

    def test_collapses_internal_whitespace(self) -> None:
        self.assertEqual(self._sanitize("hello   world"), "hello world")

    def test_newline_replaced_with_space_by_default(self) -> None:
        self.assertEqual(self._sanitize("hello\nworld"), "hello world")

    def test_newline_preserved_when_allowed(self) -> None:
        result = self._sanitize("hello\nworld", allow_newlines=True)
        self.assertEqual(result, "hello\nworld")

    def test_crlf_normalized_to_space(self) -> None:
        self.assertEqual(self._sanitize("hello\r\nworld"), "hello world")

    def test_tab_replaced_with_space(self) -> None:
        self.assertEqual(self._sanitize("hello\tworld"), "hello world")

    def test_control_chars_below_32_replaced(self) -> None:
        # chr(1) through chr(31) should become spaces
        result = self._sanitize("hello\x01world")
        self.assertEqual(result, "hello world")

    def test_del_char_replaced(self) -> None:
        result = self._sanitize("hello\x7fworld")
        self.assertEqual(result, "hello world")

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(self._sanitize(""), "")

    def test_only_whitespace_returns_empty(self) -> None:
        self.assertEqual(self._sanitize("   "), "")

    def test_unicode_bidi_override_stripped(self) -> None:
        # U+202E is RIGHT-TO-LEFT OVERRIDE, category Cf
        bidi = "\u202e"
        result = self._sanitize(f"hello{bidi}world")
        self.assertEqual(result, "helloworld")

    def test_regular_unicode_preserved(self) -> None:
        result = self._sanitize("héllo wörld")
        self.assertEqual(result, "héllo wörld")

    def test_multiple_newlines_when_allowed(self) -> None:
        result = self._sanitize("line1\nline2\nline3", allow_newlines=True)
        self.assertEqual(result, "line1\nline2\nline3")


# ---------------------------------------------------------------------------
# stt module: compute_type_for_device
# ---------------------------------------------------------------------------

class ComputeTypeForDeviceTests(unittest.TestCase):
    def _compute_type(self, device: str, override: str | None = None) -> str:
        import voice_controls.stt as stt_mod
        original = stt_mod.COMPUTE_TYPE_OVERRIDE
        stt_mod.COMPUTE_TYPE_OVERRIDE = override
        try:
            return stt_mod.compute_type_for_device(device)
        finally:
            stt_mod.COMPUTE_TYPE_OVERRIDE = original

    def test_cuda_device_returns_float16(self) -> None:
        self.assertEqual(self._compute_type("cuda"), "float16")

    def test_cuda_with_index_returns_float16(self) -> None:
        self.assertEqual(self._compute_type("cuda:0"), "float16")

    def test_cpu_device_returns_int8(self) -> None:
        self.assertEqual(self._compute_type("cpu"), "int8")

    def test_override_takes_precedence_over_cuda(self) -> None:
        self.assertEqual(self._compute_type("cuda", override="int8"), "int8")

    def test_override_takes_precedence_over_cpu(self) -> None:
        self.assertEqual(self._compute_type("cpu", override="float16"), "float16")

    def test_empty_override_uses_device_default(self) -> None:
        # An empty string override is falsy, so device default is used.
        self.assertEqual(self._compute_type("cuda", override=""), "float16")


# ---------------------------------------------------------------------------
# state_utils module: write_private_text
# ---------------------------------------------------------------------------

class WritePrivateTextTests(unittest.TestCase):
    def test_writes_content_to_file(self) -> None:
        from voice_controls.state_utils import write_private_text
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "state.json"
            write_private_text(target, '{"key": "value"}')
            self.assertEqual(target.read_text(encoding="utf-8"), '{"key": "value"}')

    def test_file_has_private_permissions(self) -> None:
        from voice_controls.state_utils import write_private_text
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "state.json"
            write_private_text(target, "secret")
            mode = target.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_overwrites_existing_file(self) -> None:
        from voice_controls.state_utils import write_private_text
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "state.json"
            write_private_text(target, "first")
            write_private_text(target, "second")
            self.assertEqual(target.read_text(encoding="utf-8"), "second")

    def test_creates_parent_directories(self) -> None:
        from voice_controls.state_utils import write_private_text
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "nested" / "dir" / "state.json"
            write_private_text(target, "data")
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "data")

    def test_write_is_atomic_no_partial_file_on_success(self) -> None:
        """After a successful write, the temp file must not exist."""
        from voice_controls.state_utils import write_private_text
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "state.json"
            write_private_text(target, "content")
            tmp_files = list(Path(tmpdir).glob(".state.json.*.tmp"))
            self.assertEqual(tmp_files, [], msg="Temp file should be cleaned up after successful write")


# ---------------------------------------------------------------------------
# app module: _recv_json_line
# ---------------------------------------------------------------------------

class RecvJsonLineTests(unittest.TestCase):
    """Tests for _recv_json_line using a real socket pair for correctness."""

    def _make_pair(self) -> tuple[socket.socket, socket.socket]:
        """Return a connected (server, client) UNIX socket pair."""
        server_sock, client_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        return server_sock, client_sock

    def _recv(self, data: bytes, timeout: float = 1.0, wall_deadline: float | None = None) -> dict:
        from voice_controls.app import _recv_json_line
        server, client = self._make_pair()
        try:
            client.sendall(data)
            client.shutdown(socket.SHUT_WR)
            server.settimeout(timeout)
            return _recv_json_line(server, wall_deadline=wall_deadline)
        finally:
            server.close()
            client.close()

    def test_parses_valid_json_line(self) -> None:
        result = self._recv(b'{"input": "dictate-start"}\n')
        self.assertEqual(result, {"input": "dictate-start"})

    def test_parses_json_with_trailing_data_ignored(self) -> None:
        # Only bytes up to first newline should be parsed.
        result = self._recv(b'{"input": "dictate-start"}\nextra garbage\n')
        self.assertEqual(result, {"input": "dictate-start"})

    def test_raises_on_empty_input(self) -> None:
        from voice_controls.app import _recv_json_line
        server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.close()  # EOF immediately
            server.settimeout(1.0)
            with self.assertRaises(ValueError) as ctx:
                _recv_json_line(server)
            self.assertIn("empty_request", str(ctx.exception))
        finally:
            server.close()

    def test_raises_on_request_too_large(self) -> None:
        from voice_controls import app as app_mod
        from voice_controls.app import _recv_json_line
        original = app_mod.DAEMON_MAX_REQUEST_BYTES
        app_mod.DAEMON_MAX_REQUEST_BYTES = 10
        try:
            server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.sendall(b"x" * 20 + b"\n")
                client.shutdown(socket.SHUT_WR)
                server.settimeout(1.0)
                with self.assertRaises(ValueError) as ctx:
                    _recv_json_line(server)
                self.assertIn("request_too_large", str(ctx.exception))
            finally:
                server.close()
                client.close()
        finally:
            app_mod.DAEMON_MAX_REQUEST_BYTES = original

    def test_raises_on_invalid_json(self) -> None:
        import json
        with self.assertRaises(json.JSONDecodeError):
            self._recv(b"not valid json\n")

    def test_raises_on_wall_clock_timeout(self) -> None:
        from voice_controls.app import _recv_json_line
        server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # Send no data — deadline already expired.
            server.settimeout(0.1)
            past_deadline = time.time() - 1.0
            with self.assertRaises(ValueError) as ctx:
                _recv_json_line(server, wall_deadline=past_deadline)
            self.assertIn("wall_clock_timeout", str(ctx.exception))
        finally:
            server.close()
            client.close()

    def test_whitespace_only_line_raises_empty_request(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self._recv(b"   \n")
        self.assertIn("empty_request", str(ctx.exception))


# ---------------------------------------------------------------------------
# app module: _reap_capture_proc
# ---------------------------------------------------------------------------

class ReapCaptureProcTests(unittest.TestCase):
    def setUp(self) -> None:
        # Isolate each test by saving and restoring the module-level dict.
        import voice_controls.app as app_mod
        self._app = app_mod
        self._original = dict(app_mod._ACTIVE_CAPTURE_PROCS)
        app_mod._ACTIVE_CAPTURE_PROCS.clear()

    def tearDown(self) -> None:
        self._app._ACTIVE_CAPTURE_PROCS.clear()
        self._app._ACTIVE_CAPTURE_PROCS.update(self._original)

    def test_noop_when_pid_not_tracked(self) -> None:
        from voice_controls.app import _reap_capture_proc
        # Should not raise even if pid is unknown.
        _reap_capture_proc(99999)

    def test_calls_wait_on_tracked_proc(self) -> None:
        from voice_controls.app import _reap_capture_proc
        mock_proc = Mock(spec=subprocess.Popen)
        self._app._ACTIVE_CAPTURE_PROCS[42] = mock_proc
        _reap_capture_proc(42)
        mock_proc.wait.assert_called_once_with(timeout=2)
        self.assertNotIn(42, self._app._ACTIVE_CAPTURE_PROCS)

    def test_kills_and_reaps_on_timeout(self) -> None:
        from voice_controls.app import _reap_capture_proc
        mock_proc = Mock(spec=subprocess.Popen)
        mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="ffmpeg", timeout=2), None]
        self._app._ACTIVE_CAPTURE_PROCS[43] = mock_proc
        _reap_capture_proc(43)
        mock_proc.kill.assert_called_once()
        # wait() called twice: once raising TimeoutExpired, once after kill()
        self.assertEqual(mock_proc.wait.call_count, 2)
        self.assertNotIn(43, self._app._ACTIVE_CAPTURE_PROCS)

    def test_removes_entry_from_dict(self) -> None:
        from voice_controls.app import _reap_capture_proc
        mock_proc = Mock(spec=subprocess.Popen)
        self._app._ACTIVE_CAPTURE_PROCS[44] = mock_proc
        _reap_capture_proc(44)
        self.assertNotIn(44, self._app._ACTIVE_CAPTURE_PROCS)


# ---------------------------------------------------------------------------
# app module: _stop_session tmpdir path validation
# ---------------------------------------------------------------------------

class StopSessionPathValidationTests(unittest.TestCase):
    """Verify that _stop_session rejects unsafe tmpdir paths from state files."""

    def _make_fake_state_path(self, state: dict) -> Mock:
        """Return a Mock that acts like DICTATE_STATE_PATH with the given state."""
        import json
        fake_path = Mock()
        fake_path.read_text.return_value = json.dumps(state)
        fake_path.exists.return_value = True
        fake_path.unlink = Mock()
        return fake_path

    def _run_stop_with_state(self, state: dict) -> int:
        from voice_controls import app as app_mod
        fake_path = self._make_fake_state_path(state)
        with patch.object(app_mod, "DICTATE_STATE_PATH", fake_path):
            return app_mod._stop_session()

    def test_rejects_path_traversal_with_dotdot(self) -> None:
        tmp = tempfile.gettempdir()
        state = {
            "pid": 0,
            "tmpdir": f"{tmp}/../etc",
            "audio_path": f"{tmp}/../etc/passwd",
            "started_at": time.time(),
        }
        rc = self._run_stop_with_state(state)
        self.assertEqual(rc, 1)

    def test_rejects_tmp_prefix_bypass(self) -> None:
        # /tmp-evil starts with /tmp but is not under /tmp/
        state = {
            "pid": 0,
            "tmpdir": "/tmp-evil/payload",
            "audio_path": "/tmp-evil/payload/capture.wav",
            "started_at": time.time(),
        }
        rc = self._run_stop_with_state(state)
        self.assertEqual(rc, 1)

    def test_rejects_missing_paths(self) -> None:
        state = {"pid": 0, "tmpdir": "", "audio_path": "", "started_at": time.time()}
        rc = self._run_stop_with_state(state)
        self.assertEqual(rc, 1)

    def test_rejects_audio_path_outside_tmpdir(self) -> None:
        tmp = tempfile.gettempdir()
        valid_tmpdir = os.path.join(tmp, "voice-dictate-hold-test")
        state = {
            "pid": 0,
            "tmpdir": valid_tmpdir,
            "audio_path": "/etc/passwd",
            "started_at": time.time(),
        }
        rc = self._run_stop_with_state(state)
        self.assertEqual(rc, 1)

    def test_accepts_valid_tmp_path(self) -> None:
        tmp = tempfile.gettempdir()
        valid_tmpdir = os.path.join(tmp, "voice-dictate-hold-test")
        state = {
            "pid": 0,
            "tmpdir": valid_tmpdir,
            "audio_path": os.path.join(valid_tmpdir, "capture.wav"),
            "started_at": time.time(),
        }
        from voice_controls import app as app_mod
        fake_path = self._make_fake_state_path(state)
        with patch.object(app_mod, "DICTATE_STATE_PATH", fake_path), \
             patch("voice_controls.app.notify"), \
             patch("voice_controls.app._wait_for_captured_audio"), \
             patch("pathlib.Path.exists", return_value=False):
            rc = app_mod._stop_session()
        # rc=0 means "no speech captured" — path validation passed
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# app module: request_daemon fast-fail branches
# ---------------------------------------------------------------------------

class RequestDaemonFastFailTests(unittest.TestCase):
    def test_returns_immediately_when_daemon_spawn_fails(self) -> None:
        from voice_controls import app as app_mod

        mock_client = Mock()
        mock_client.connect.side_effect = FileNotFoundError()
        mock_socket_ctx = Mock()
        mock_socket_ctx.__enter__ = Mock(return_value=mock_client)
        mock_socket_ctx.__exit__ = Mock(return_value=False)

        with patch("voice_controls.app.socket.socket", return_value=mock_socket_ctx), \
             patch("voice_controls.app.start_daemon", return_value=None) as mock_start_daemon, \
             patch("voice_controls.app.notify") as mock_notify, \
             patch("voice_controls.app.time.sleep") as mock_sleep:
            rc = app_mod.request_daemon("dictate-start")

        self.assertEqual(rc, 1)
        mock_start_daemon.assert_called_once_with()
        mock_notify.assert_called_once_with("Voice", "Voice daemon unavailable")
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
