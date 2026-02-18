import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from voice_controls import app


class Phase0GuardrailTests(unittest.TestCase):
    def test_input_handlers_include_core_contract(self) -> None:
        expected = {
            "command-start",
            "command-stop",
            "dictate-start",
            "dictate-stop",
        }
        self.assertTrue(expected.issubset(app.HOLD_INPUT_HANDLERS))

    def test_execute_daemon_request_returns_2_for_invalid_input(self) -> None:
        rc = app._execute_daemon_request({"input": "definitely-not-valid"})
        self.assertEqual(rc, 2)

    def test_execute_daemon_request_forwards_supported_input(self) -> None:
        mock_handler = Mock(return_value=0)
        with patch.dict(app.HOLD_INPUT_HANDLERS, {"dictate-start": mock_handler}, clear=True):
            rc = app._execute_daemon_request({"input": "dictate-start"})

        self.assertEqual(rc, 0)
        mock_handler.assert_called_once_with()

    def test_execute_daemon_request_returns_1_on_handler_exception(self) -> None:
        mock_handler = Mock(side_effect=RuntimeError("boom"))
        with patch.dict(app.HOLD_INPUT_HANDLERS, {"command-start": mock_handler}, clear=True):
            rc = app._execute_daemon_request({"input": "command-start"})
        self.assertEqual(rc, 1)

    def test_execute_daemon_request_returns_2_when_input_missing(self) -> None:
        rc = app._execute_daemon_request({})
        self.assertEqual(rc, 2)

    def test_main_forwards_input_mode_to_request_daemon(self) -> None:
        entry_script = Path("/tmp/hvc")
        with patch("sys.argv", ["hvc", "--input", "dictate-stop"]), patch(
            "voice_controls.app.request_daemon", return_value=7
        ) as mock_request_daemon:
            rc = app.main(entry_script=entry_script)

        self.assertEqual(rc, 7)
        mock_request_daemon.assert_called_once_with("dictate-stop", entry_script=entry_script)

    def test_main_daemon_flag_routes_to_run_daemon(self) -> None:
        with patch("sys.argv", ["hvc", "--daemon"]), patch("voice_controls.app.run_daemon", return_value=0) as mock_run_daemon:
            rc = app.main(entry_script=Path("/tmp/hvc"))

        self.assertEqual(rc, 0)
        mock_run_daemon.assert_called_once_with()

    def test_hvc_launcher_exists(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        launcher = repo_root / "hvc"
        self.assertTrue(launcher.exists())
        self.assertTrue(launcher.is_file())


if __name__ == "__main__":
    unittest.main()
