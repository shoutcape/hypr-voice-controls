import unittest
from collections import deque
from typing import Any, cast
from unittest.mock import patch

from voice_hotkey import wakeword
from voice_hotkey.orchestrator import CANCELLED_EXIT_CODE, NO_SPEECH_EXIT_CODE


class _FakeStream:
    def __init__(self) -> None:
        self.stopped = 0
        self.started = 0

    def stop(self) -> None:
        self.stopped += 1

    def start(self) -> None:
        self.started += 1


class Phase3WakewordResponsivenessTests(unittest.TestCase):
    def test_handle_wake_trigger_uses_short_daemon_timeouts(self) -> None:
        stream = _FakeStream()
        ring = deque([b"a", b"b"], maxlen=4)
        captured: dict[str, object] = {}

        def _fake_request_daemon(_input_mode: str, **kwargs: object) -> int:
            captured.update(kwargs)
            return 0

        with patch("voice_hotkey.wakeword._write_wake_preroll"):
            rc = wakeword._handle_wake_trigger(
                stream=cast(Any, stream),
                ring=ring,
                request_daemon=_fake_request_daemon,
            )

        self.assertEqual(rc, 0)
        self.assertEqual(stream.stopped, 1)
        self.assertEqual(stream.started, 1)
        self.assertEqual(captured["auto_start"], False)
        self.assertEqual(captured["retries"], wakeword.WAKE_DAEMON_RETRIES)
        self.assertEqual(captured["connect_timeout"], wakeword.WAKE_DAEMON_CONNECT_TIMEOUT_SECONDS)
        self.assertEqual(captured["response_timeout"], wakeword.WAKE_DAEMON_RESPONSE_TIMEOUT_SECONDS)

    def test_apply_wake_trigger_result_sets_no_speech_rearm(self) -> None:
        last, rearm = wakeword._apply_wake_trigger_result(
            now=100.0,
            rc=NO_SPEECH_EXIT_CODE,
            last_trigger_at=10.0,
            rearm_until=0.0,
        )
        self.assertEqual(last, 100.0)
        self.assertAlmostEqual(rearm, 100.0 + wakeword.WAKEWORD_NO_SPEECH_REARM_MS / 1000.0)

    def test_apply_wake_trigger_result_sets_error_rearm_on_cancelled(self) -> None:
        last, rearm = wakeword._apply_wake_trigger_result(
            now=50.0,
            rc=CANCELLED_EXIT_CODE,
            last_trigger_at=11.0,
            rearm_until=0.0,
        )
        self.assertEqual(last, 11.0)
        self.assertAlmostEqual(rearm, 50.0 + wakeword.WAKEWORD_ERROR_REARM_MS / 1000.0)

    def test_apply_wake_trigger_result_sets_error_rearm_on_failure(self) -> None:
        last, rearm = wakeword._apply_wake_trigger_result(
            now=75.0,
            rc=1,
            last_trigger_at=12.0,
            rearm_until=0.0,
        )
        self.assertEqual(last, 12.0)
        self.assertAlmostEqual(rearm, 75.0 + wakeword.WAKEWORD_ERROR_REARM_MS / 1000.0)

    def test_classify_wake_trigger_result_tags(self) -> None:
        self.assertEqual(wakeword._classify_wake_trigger_result(0), "ok")
        self.assertEqual(wakeword._classify_wake_trigger_result(NO_SPEECH_EXIT_CODE), "no_speech")
        self.assertEqual(wakeword._classify_wake_trigger_result(CANCELLED_EXIT_CODE), "cancelled")
        self.assertEqual(wakeword._classify_wake_trigger_result(2), "stale_daemon")
        self.assertEqual(wakeword._classify_wake_trigger_result(1), "busy_or_error")

    def test_record_wake_trigger_outcome_increments_counter(self) -> None:
        with patch.dict("voice_hotkey.wakeword._WAKE_TRIGGER_OUTCOME_COUNTS", {}, clear=True):
            wakeword._record_wake_trigger_outcome(reason="ok", rc=0, rearm_ms=0)
            wakeword._record_wake_trigger_outcome(reason="ok", rc=0, rearm_ms=0)
            self.assertEqual(wakeword._WAKE_TRIGGER_OUTCOME_COUNTS["ok"], 2)


if __name__ == "__main__":
    unittest.main()
