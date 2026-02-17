import json
import socket
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from voice_hotkey.orchestrator import CANCELLED_EXIT_CODE
from unittest.mock import patch

from voice_hotkey import app
from voice_hotkey.runtime.state_machine import (
    STATE_COMMAND_HOLD,
    STATE_DICTATE_HOLD,
    STATE_IDLE,
    STATE_TRANSCRIBING,
    STATE_WAKE_SESSION,
    RuntimeStateMachine,
)


class Phase2StateMachineTests(unittest.TestCase):
    def test_command_start_transitions_idle_to_command_hold(self) -> None:
        machine = RuntimeStateMachine()
        result = machine.transition("command-start")
        self.assertTrue(result.allowed)
        self.assertEqual(result.previous_state, STATE_IDLE)
        self.assertEqual(result.next_state, STATE_COMMAND_HOLD)
        self.assertEqual(machine.get_state(), STATE_COMMAND_HOLD)

    def test_command_stop_transitions_to_transcribing_then_idle_on_complete(self) -> None:
        machine = RuntimeStateMachine()
        machine.transition("command-start")

        stop_result = machine.transition("command-stop")
        self.assertTrue(stop_result.allowed)
        self.assertEqual(stop_result.next_state, STATE_TRANSCRIBING)

        complete_result = machine.transition("command-stop-complete")
        self.assertTrue(complete_result.allowed)
        self.assertEqual(complete_result.next_state, STATE_IDLE)
        self.assertEqual(machine.get_state(), STATE_IDLE)

    def test_command_stop_from_idle_is_allowed_noop(self) -> None:
        machine = RuntimeStateMachine()
        result = machine.transition("command-stop")
        self.assertTrue(result.allowed)
        self.assertEqual(result.previous_state, STATE_IDLE)
        self.assertEqual(result.next_state, STATE_IDLE)

    def test_command_start_rejected_while_transcribing(self) -> None:
        machine = RuntimeStateMachine()
        machine.transition("command-start")
        machine.transition("command-stop")

        result = machine.transition("command-start")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "runtime_busy")
        self.assertEqual(machine.get_state(), STATE_TRANSCRIBING)

    def test_dictate_start_transitions_idle_to_dictate_hold(self) -> None:
        machine = RuntimeStateMachine()
        result = machine.transition("dictate-start")
        self.assertTrue(result.allowed)
        self.assertEqual(result.previous_state, STATE_IDLE)
        self.assertEqual(result.next_state, STATE_DICTATE_HOLD)
        self.assertEqual(machine.get_state(), STATE_DICTATE_HOLD)

    def test_dictate_stop_transitions_to_transcribing_then_idle_on_complete(self) -> None:
        machine = RuntimeStateMachine()
        machine.transition("dictate-start")

        stop_result = machine.transition("dictate-stop")
        self.assertTrue(stop_result.allowed)
        self.assertEqual(stop_result.next_state, STATE_TRANSCRIBING)

        complete_result = machine.transition("dictate-stop-complete")
        self.assertTrue(complete_result.allowed)
        self.assertEqual(complete_result.next_state, STATE_IDLE)
        self.assertEqual(machine.get_state(), STATE_IDLE)

    def test_command_start_rejected_while_dictate_hold_active(self) -> None:
        machine = RuntimeStateMachine()
        machine.transition("dictate-start")
        result = machine.transition("command-start")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "runtime_busy")

    def test_wake_start_transitions_idle_to_wake_session(self) -> None:
        machine = RuntimeStateMachine()
        result = machine.transition("wake-start")
        self.assertTrue(result.allowed)
        self.assertEqual(result.next_state, STATE_WAKE_SESSION)

        complete = machine.transition("wake-complete")
        self.assertTrue(complete.allowed)
        self.assertEqual(complete.next_state, STATE_IDLE)

    def test_wake_start_rejected_while_command_hold_active(self) -> None:
        machine = RuntimeStateMachine()
        machine.transition("command-start")
        result = machine.transition("wake-start")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "runtime_busy")

    def test_handle_hold_input_routes_command_modes_to_v2_wrappers(self) -> None:
        with patch.object(app, "RUNTIME_V2_ENABLED", True):
            with patch("voice_hotkey.app._run_command_start_v2", return_value=11) as mock_start:
                self.assertEqual(app._handle_hold_input("command-start"), 11)
            mock_start.assert_called_once_with()

            with patch("voice_hotkey.app._run_command_stop_v2", return_value=12) as mock_stop:
                self.assertEqual(app._handle_hold_input("command-stop"), 12)
            mock_stop.assert_called_once_with()

    def test_handle_hold_input_routes_dictate_modes_to_v2_wrappers(self) -> None:
        with patch.object(app, "RUNTIME_V2_ENABLED", True):
            with patch("voice_hotkey.app._run_dictate_start_v2", return_value=21) as mock_start:
                self.assertEqual(app._handle_hold_input("dictate-start"), 21)
            mock_start.assert_called_once_with()

            with patch("voice_hotkey.app._run_dictate_stop_v2", return_value=22) as mock_stop:
                self.assertEqual(app._handle_hold_input("dictate-stop"), 22)
            mock_stop.assert_called_once_with()

    def test_handle_input_routes_wake_start_to_v2_wrapper(self) -> None:
        with patch.object(app, "RUNTIME_V2_ENABLED", True):
            with patch("voice_hotkey.app.read_wakeword_enabled", return_value=True):
                with patch("voice_hotkey.app._run_wake_start_v2", return_value=31) as mock_wake_v2:
                    self.assertEqual(app.handle_input("wake-start"), 31)
                mock_wake_v2.assert_called_once_with()

    def test_handle_input_routes_runtime_status(self) -> None:
        with patch("voice_hotkey.app._run_runtime_status", return_value=51) as mock_runtime_status:
            self.assertEqual(app.handle_input("runtime-status"), 51)
        mock_runtime_status.assert_called_once_with()

    def test_handle_input_routes_runtime_status_json(self) -> None:
        with patch("voice_hotkey.app._run_runtime_status", return_value=52) as mock_runtime_status:
            self.assertEqual(app.handle_input("runtime-status-json"), 52)
        mock_runtime_status.assert_called_once_with(notify_user=False)

    def test_wake_start_v2_uses_execution_queue_wrapper(self) -> None:
        machine = RuntimeStateMachine()
        with patch.object(app, "RUNTIME_STATE_MACHINE", machine):
            with patch("voice_hotkey.app._run_v2_queued_call", return_value=0) as mock_queued:
                rc = app._run_wake_start_v2()
        self.assertEqual(rc, 0)
        self.assertEqual(mock_queued.call_count, 1)
        args = mock_queued.call_args[0]
        self.assertEqual(args[0], "wake-start")
        self.assertTrue(callable(args[1]))
        self.assertEqual(machine.get_state(), STATE_IDLE)

    def test_handle_input_routes_command_auto_to_v2_wrapper(self) -> None:
        with patch.object(app, "RUNTIME_V2_ENABLED", True):
            with patch("voice_hotkey.app._run_command_auto_v2", return_value=41) as mock_auto_v2:
                self.assertEqual(app.handle_input("command-auto"), 41)
            mock_auto_v2.assert_called_once_with()

    def test_handle_input_routes_dictate_to_v2_wrapper(self) -> None:
        with patch.object(app, "RUNTIME_V2_ENABLED", True):
            with patch("voice_hotkey.app._run_dictate_v2", return_value=43) as mock_dictate_v2:
                self.assertEqual(app.handle_input("dictate"), 43)
            mock_dictate_v2.assert_called_once_with()

    def test_handle_input_routes_voice_to_v2_wrapper(self) -> None:
        with patch.object(app, "RUNTIME_V2_ENABLED", True):
            with patch("voice_hotkey.app._run_voice_capture_v2", return_value=44) as mock_voice_v2:
                self.assertEqual(app.handle_input("voice"), 44)
            mock_voice_v2.assert_called_once_with()

    def test_command_auto_v2_uses_execution_queue_wrapper(self) -> None:
        with patch("voice_hotkey.app._run_v2_queued_call", return_value=0) as mock_queued:
            rc = app._run_command_auto_v2()
        self.assertEqual(rc, 0)
        self.assertEqual(mock_queued.call_count, 1)
        args = mock_queued.call_args[0]
        self.assertEqual(args[0], "command-auto")
        self.assertTrue(callable(args[1]))

    def test_command_stop_v2_uses_execution_queue_wrapper(self) -> None:
        machine = RuntimeStateMachine()
        machine.transition("command-start")

        with patch.object(app, "RUNTIME_STATE_MACHINE", machine):
            with patch("voice_hotkey.app._cancel_v2_long_running_jobs", return_value=False):
                with patch("voice_hotkey.app._run_v2_queued_call", return_value=0) as mock_queued:
                    rc = app._run_command_stop_v2()

        self.assertEqual(rc, 0)
        self.assertEqual(mock_queued.call_count, 1)
        args = mock_queued.call_args[0]
        self.assertEqual(args[0], "command-stop")
        self.assertTrue(callable(args[1]))

    def test_dictate_stop_v2_uses_execution_queue_wrapper(self) -> None:
        machine = RuntimeStateMachine()
        machine.transition("dictate-start")

        with patch.object(app, "RUNTIME_STATE_MACHINE", machine):
            with patch("voice_hotkey.app._cancel_v2_long_running_jobs", return_value=False):
                with patch("voice_hotkey.app._run_v2_queued_call", return_value=0) as mock_queued:
                    rc = app._run_dictate_stop_v2()

        self.assertEqual(rc, 0)
        self.assertEqual(mock_queued.call_count, 1)
        args = mock_queued.call_args[0]
        self.assertEqual(args[0], "dictate-stop")
        self.assertTrue(callable(args[1]))

    def test_resolve_admission_class_for_v2_modes(self) -> None:
        with patch.object(app, "RUNTIME_V2_ENABLED", True):
            self.assertEqual(app._resolve_admission_class("runtime-status"), "direct")
            self.assertEqual(app._resolve_admission_class("command-auto"), "queued")
            self.assertEqual(app._resolve_admission_class("dictate-stop"), "queued")

    def test_run_v2_queued_call_returns_busy_when_queue_full(self) -> None:
        class _FullQueue:
            @staticmethod
            def submit(_name, _fn):
                return None

            @staticmethod
            def pending() -> int:
                return 8

        with patch.object(app, "RUNTIME_EXECUTION_QUEUE", _FullQueue()):
            rc = app._run_v2_queued_call("wake-start", lambda _cancel_event: 0)
        self.assertEqual(rc, 1)

    def test_run_v2_queued_call_returns_job_result(self) -> None:
        future: Future[int] = Future()
        future.set_result(42)

        class _OkQueue:
            @staticmethod
            def submit(_name, _fn):
                return future

            @staticmethod
            def pending() -> int:
                return 0

        with patch.object(app, "RUNTIME_EXECUTION_QUEUE", _OkQueue()):
            rc = app._run_v2_queued_call("wake-start", lambda _cancel_event: 0)
        self.assertEqual(rc, 42)

    def test_run_v2_queued_call_returns_cancelled_code_when_future_cancelled(self) -> None:
        future: Future[int] = Future()
        future.cancel()

        class _CancelledQueue:
            @staticmethod
            def submit(_name, _fn):
                return future

            @staticmethod
            def pending() -> int:
                return 0

        with patch.object(app, "RUNTIME_EXECUTION_QUEUE", _CancelledQueue()):
            rc = app._run_v2_queued_call("wake-start", lambda _cancel_event: 0)
        self.assertEqual(rc, CANCELLED_EXIT_CODE)

    def test_command_stop_v2_requests_command_auto_cancellation(self) -> None:
        machine = RuntimeStateMachine()

        class _QueueMock:
            def __init__(self) -> None:
                self.cancelled_jobs: list[str] = []

            def cancel_by_name(self, name: str) -> bool:
                self.cancelled_jobs.append(name)
                return True

        queue_mock = _QueueMock()

        with patch.object(app, "RUNTIME_STATE_MACHINE", machine):
            with patch.object(app, "RUNTIME_EXECUTION_QUEUE", queue_mock):
                with patch("voice_hotkey.app._run_v2_queued_call", return_value=0):
                    with patch.object(app, "COMMAND_STATE_PATH", SimpleNamespace(exists=lambda: True)):
                        rc = app._run_command_stop_v2()

        self.assertEqual(rc, 0)
        self.assertEqual(queue_mock.cancelled_jobs, ["command-auto", "wake-start"])

    def test_command_stop_v2_returns_0_when_only_cancelling_wake_or_auto(self) -> None:
        machine = RuntimeStateMachine()

        class _QueueMock:
            @staticmethod
            def cancel_by_name(_name: str) -> bool:
                return True

        with patch.object(app, "RUNTIME_STATE_MACHINE", machine):
            with patch.object(app, "RUNTIME_EXECUTION_QUEUE", _QueueMock()):
                with patch.object(app, "COMMAND_STATE_PATH", SimpleNamespace(exists=lambda: False)):
                    with patch("voice_hotkey.app.stop_press_hold_command") as mock_stop:
                        rc = app._run_command_stop_v2()

        self.assertEqual(rc, 0)
        mock_stop.assert_not_called()

    def test_dictate_stop_v2_returns_0_when_only_cancelling_wake_or_auto(self) -> None:
        machine = RuntimeStateMachine()

        class _QueueMock:
            @staticmethod
            def cancel_by_name(_name: str) -> bool:
                return True

        with patch.object(app, "RUNTIME_STATE_MACHINE", machine):
            with patch.object(app, "RUNTIME_EXECUTION_QUEUE", _QueueMock()):
                with patch.object(app, "DICTATE_STATE_PATH", SimpleNamespace(exists=lambda: False)):
                    with patch("voice_hotkey.app.stop_press_hold_dictation") as mock_stop:
                        rc = app._run_dictate_stop_v2()

        self.assertEqual(rc, 0)
        mock_stop.assert_not_called()

    def test_run_runtime_status_reports_queue_snapshot(self) -> None:
        class _QueueMock:
            @staticmethod
            def snapshot():
                return SimpleNamespace(
                    pending=2,
                    running_job_name="command-auto",
                    running_age_ms=123,
                    worker_alive=True,
                    worker_restarts=0,
                )

        class _StateMachineMock:
            @staticmethod
            def get_state() -> str:
                return "idle"

        with patch.object(app, "RUNTIME_EXECUTION_QUEUE", _QueueMock()):
            with patch.object(app, "RUNTIME_STATE_MACHINE", _StateMachineMock()):
                with patch("voice_hotkey.app.notify") as mock_notify:
                    rc = app._run_runtime_status()

        self.assertEqual(rc, 0)
        self.assertEqual(mock_notify.call_count, 1)
        args = mock_notify.call_args[0]
        self.assertEqual(args[0], "Voice")
        self.assertIn("pending=2", args[1])
        self.assertIn("running=command-auto", args[1])

    def test_runtime_status_payload_contains_required_fields(self) -> None:
        class _QueueMock:
            @staticmethod
            def snapshot():
                return SimpleNamespace(
                    pending=0,
                    running_job_id=None,
                    running_job_name=None,
                    running_age_ms=None,
                    worker_alive=True,
                    worker_restarts=0,
                )

        class _StateMachineMock:
            @staticmethod
            def get_state() -> str:
                return "idle"

        with patch.object(app, "RUNTIME_EXECUTION_QUEUE", _QueueMock()):
            with patch.object(app, "RUNTIME_STATE_MACHINE", _StateMachineMock()):
                payload = app._runtime_status_payload()

        self.assertEqual(payload["state"], "idle")
        self.assertIn("pending", payload)
        self.assertIn("worker_alive", payload)

    def test_daemon_runtime_status_json_response_includes_status_payload(self) -> None:
        server, client = socket.socketpair()
        try:
            client.sendall(b'{"input":"runtime-status-json"}\n')
            with patch("voice_hotkey.app._execute_daemon_request", return_value=0):
                with patch("voice_hotkey.app._runtime_status_payload", return_value={"state": "idle", "pending": 0}):
                    app._handle_daemon_connection(server)

            data = b""
            while not data.endswith(b"\n"):
                chunk = client.recv(4096)
                if not chunk:
                    break
                data += chunk
            response = json.loads(data.decode("utf-8"))
            self.assertEqual(response.get("rc"), 0)
            self.assertEqual(response.get("status", {}).get("state"), "idle")
        finally:
            server.close()
            client.close()


if __name__ == "__main__":
    unittest.main()
