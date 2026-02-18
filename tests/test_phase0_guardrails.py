import unittest
from unittest.mock import patch

from voice_controls import app


class Phase0GuardrailTests(unittest.TestCase):
    def test_allowed_input_modes_include_core_contract(self) -> None:
        expected = {
            "command-start",
            "command-stop",
            "dictate-start",
            "dictate-stop",
        }
        self.assertTrue(expected.issubset(app.ALLOWED_INPUT_MODES))

    def test_execute_daemon_request_returns_2_for_invalid_input(self) -> None:
        rc = app._execute_daemon_request({"input": "definitely-not-valid"})
        self.assertEqual(rc, 2)

    def test_execute_daemon_request_forwards_supported_input(self) -> None:
        with patch("voice_controls.app.handle_input", return_value=0) as mock_handle_input:
            rc = app._execute_daemon_request({"input": "dictate-start"})

        self.assertEqual(rc, 0)
        mock_handle_input.assert_called_once_with("dictate-start")

    def test_execute_daemon_request_returns_1_on_handler_exception(self) -> None:
        with patch("voice_controls.app.handle_input", side_effect=RuntimeError("boom")):
            rc = app._execute_daemon_request({"input": "command-start"})
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
