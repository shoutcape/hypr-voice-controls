import unittest
from unittest.mock import patch

from voice_hotkey import app
from voice_hotkey.orchestrator import NO_SPEECH_EXIT_CODE


class Phase0GuardrailTests(unittest.TestCase):
    def test_allowed_input_modes_include_core_contract(self) -> None:
        expected = {
            "command-start",
            "command-stop",
            "dictate-start",
            "dictate-stop",
            "command-auto",
            "wake-start",
            "wakeword-enable",
            "wakeword-disable",
            "wakeword-toggle",
            "wakeword-status",
            "runtime-status",
            "runtime-status-json",
        }
        self.assertTrue(expected.issubset(app.ALLOWED_INPUT_MODES))

    def test_no_speech_exit_code_contract(self) -> None:
        self.assertEqual(NO_SPEECH_EXIT_CODE, 3)

    def test_execute_daemon_request_returns_2_for_invalid_input(self) -> None:
        rc = app._execute_daemon_request({"input": "definitely-not-valid"})
        self.assertEqual(rc, 2)

    def test_execute_daemon_request_normalizes_alias_before_handling(self) -> None:
        with patch("voice_hotkey.app.handle_input", return_value=0) as mock_handle_input:
            rc = app._execute_daemon_request({"input": "text"})

        self.assertEqual(rc, 0)
        mock_handle_input.assert_called_once_with("dictate")

    def test_execute_daemon_request_returns_1_on_handler_exception(self) -> None:
        with patch("voice_hotkey.app.handle_input", side_effect=RuntimeError("boom")):
            rc = app._execute_daemon_request({"input": "wakeword-status"})
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
