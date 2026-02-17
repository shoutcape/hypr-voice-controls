import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_hotkey import commands, integrations


class Phase4CommandsIntegrationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_path = commands.USER_COMMANDS_PATH
        self._original_cache = commands._USER_COMMANDS_CACHE
        self._original_compiled = commands._USER_COMPILED_CACHE
        self._original_mtime = commands._USER_COMMANDS_MTIME_NS
        commands._USER_COMMANDS_CACHE = []
        commands._USER_COMPILED_CACHE = []
        commands._USER_COMMANDS_MTIME_NS = None

    def tearDown(self) -> None:
        commands.USER_COMMANDS_PATH = self._original_path
        commands._USER_COMMANDS_CACHE = self._original_cache
        commands._USER_COMPILED_CACHE = self._original_compiled
        commands._USER_COMMANDS_MTIME_NS = self._original_mtime

    def test_match_command_ignores_invalid_and_disabled_entries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="voice-commands-test-") as tmpdir:
            path = Path(tmpdir) / "voice-commands.json"
            path.write_text(
                json.dumps(
                    [
                        {"label": "Disabled", "pattern": "^disabled$", "argv": ["false"], "enabled": False},
                        {"label": "BadRegex", "pattern": "(", "argv": ["echo", "bad"]},
                        {"label": "BadArgv", "pattern": "^bad$", "argv": ["", "ok"]},
                        {"label": "Lock", "pattern": "^lock screen$", "argv": ["loginctl", "lock-session"]},
                    ]
                ),
                encoding="utf-8",
            )
            commands.USER_COMMANDS_PATH = path

            argv, label = commands.match_command("lock screen")

        self.assertEqual(label, "Lock")
        self.assertEqual(argv, ["loginctl", "lock-session"])

    def test_get_user_commands_resets_cache_when_file_removed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="voice-commands-test-") as tmpdir:
            path = Path(tmpdir) / "voice-commands.json"
            path.write_text(
                json.dumps([
                    {"label": "Lock", "pattern": "^lock screen$", "argv": ["loginctl", "lock-session"]}
                ]),
                encoding="utf-8",
            )
            commands.USER_COMMANDS_PATH = path

            self.assertEqual(len(commands.get_user_commands()), 1)
            path.unlink(missing_ok=True)
            self.assertEqual(commands.get_user_commands(), [])
            self.assertEqual(commands.get_user_compiled_commands(), [])

    def test_get_user_compiled_commands_returns_snapshot_copy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="voice-commands-test-") as tmpdir:
            path = Path(tmpdir) / "voice-commands.json"
            path.write_text(
                json.dumps([
                    {"label": "Lock", "pattern": "^lock screen$", "argv": ["loginctl", "lock-session"]}
                ]),
                encoding="utf-8",
            )
            commands.USER_COMMANDS_PATH = path

            first = commands.get_user_compiled_commands()
            self.assertEqual(len(first), 1)
            first.clear()
            second = commands.get_user_compiled_commands()

        self.assertEqual(len(second), 1)

    def test_normalize_collapses_noise_and_prefix_words(self) -> None:
        normalized = commands.normalize("Please,   lock   screen!!!")
        self.assertEqual(normalized, "lock screen")

    def test_sanitize_dictation_text_replaces_controls_and_tabs(self) -> None:
        with patch.object(integrations, "DICTATION_ALLOW_NEWLINES", False), patch.object(integrations, "DICTATION_STRICT_TEXT", True):
            sanitized = integrations._sanitize_dictation_text("  hi\tthere\x00\nfriend  ")
        self.assertEqual(sanitized, "hi there friend")

    def test_sanitize_dictation_text_preserves_newlines_when_enabled(self) -> None:
        with patch.object(integrations, "DICTATION_ALLOW_NEWLINES", True), patch.object(integrations, "DICTATION_STRICT_TEXT", True):
            sanitized = integrations._sanitize_dictation_text("line1\r\nline2\t\n")
        self.assertEqual(sanitized, "line1\nline2")

    def test_inject_text_via_clipboard_returns_false_when_wlcopy_missing(self) -> None:
        with patch("voice_hotkey.integrations.has_tool", return_value=False):
            self.assertFalse(integrations._inject_text_via_clipboard("hello"))

    def test_run_command_returns_false_on_nonzero_exit(self) -> None:
        class _Proc:
            returncode = 1
            stdout = ""
            stderr = "failed"

        with patch("voice_hotkey.integrations.subprocess.run", return_value=_Proc()):
            ok = integrations.run_command(["false"])
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
