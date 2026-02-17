import threading
import unittest
from unittest.mock import patch

from voice_hotkey.orchestrator import CANCELLED_EXIT_CODE, _EndpointCaptureResult, run_endpointed_command_session


class Phase2OrchestratorCancelTests(unittest.TestCase):
    def test_endpointed_session_returns_cancelled_code_when_cancelled(self) -> None:
        cancel_event = threading.Event()
        cancel_event.set()

        with patch("voice_hotkey.orchestrator.notify"):
            with patch(
                "voice_hotkey.orchestrator._capture_endpointed_audio",
                return_value=_EndpointCaptureResult(audio_bytes=b"", peak_rms=0, had_preroll_speech=False),
            ):
                rc = run_endpointed_command_session(
                    language="en",
                    source="command_auto",
                    command_handler=lambda *_args, **_kwargs: 0,
                    cancel_event=cancel_event,
                )

        self.assertEqual(rc, CANCELLED_EXIT_CODE)


if __name__ == "__main__":
    unittest.main()
