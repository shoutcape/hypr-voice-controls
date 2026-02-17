import unittest
from threading import Event
import time

from voice_hotkey.runtime.job_queue import RuntimeJobQueue


class Phase2JobQueueTests(unittest.TestCase):
    def test_queue_runs_job_and_returns_result(self) -> None:
        queue = RuntimeJobQueue(max_size=1, worker_name="test-runtime-job-1")
        future = queue.submit("ok-job", lambda _cancel_event: 7)
        self.assertIsNotNone(future)
        assert future is not None
        self.assertEqual(future.result(timeout=1.0), 7)

    def test_queue_returns_none_when_full(self) -> None:
        queue = RuntimeJobQueue(max_size=1, worker_name="test-runtime-job-2")
        release = Event()

        def blocking_job(_cancel_event: Event) -> int:
            release.wait(timeout=1.0)
            return 5

        first = queue.submit("blocking-job", blocking_job)
        self.assertIsNotNone(first)

        second = queue.submit("full-job", lambda _cancel_event: 6)
        self.assertIsNone(second)

        release.set()
        assert first is not None
        self.assertEqual(first.result(timeout=1.0), 5)

    def test_cancel_by_name_cancels_pending_jobs(self) -> None:
        queue = RuntimeJobQueue(max_size=2, worker_name="test-runtime-job-3")
        release = Event()
        started = Event()

        def blocking_job(_cancel_event: Event) -> int:
            started.set()
            release.wait(timeout=1.0)
            return 1

        running = queue.submit("same-job", blocking_job)
        pending = queue.submit("same-job", lambda _cancel_event: 2)
        assert running is not None
        assert pending is not None
        self.assertTrue(started.wait(timeout=1.0))

        cancelled = queue.cancel_by_name("same-job")
        self.assertTrue(cancelled)
        self.assertTrue(pending.cancelled())

        release.set()
        self.assertIn(running.result(timeout=1.0), {1, 4})

    def test_cancel_by_name_signals_running_job_event(self) -> None:
        queue = RuntimeJobQueue(max_size=1, worker_name="test-runtime-job-4")
        started = Event()

        def cancellable_job(cancel_event: Event) -> int:
            started.set()
            deadline = time.time() + 1.0
            while time.time() < deadline:
                if cancel_event.is_set():
                    return 4
                time.sleep(0.01)
            return 9

        future = queue.submit("long-job", cancellable_job)
        assert future is not None
        self.assertTrue(started.wait(timeout=1.0))

        cancelled = queue.cancel_by_name("long-job")
        self.assertTrue(cancelled)
        self.assertEqual(future.result(timeout=1.0), 4)

    def test_snapshot_reports_running_job(self) -> None:
        queue = RuntimeJobQueue(max_size=1, worker_name="test-runtime-job-5")
        started = Event()
        release = Event()

        def slow_job(_cancel_event: Event) -> int:
            started.set()
            release.wait(timeout=1.0)
            return 0

        future = queue.submit("snapshot-job", slow_job)
        assert future is not None
        self.assertTrue(started.wait(timeout=1.0))

        snapshot = queue.snapshot()
        self.assertEqual(snapshot.pending, 0)
        self.assertIsNotNone(snapshot.running_job_id)
        self.assertEqual(snapshot.running_job_name, "snapshot-job")
        self.assertIsNotNone(snapshot.running_age_ms)

        release.set()
        self.assertEqual(future.result(timeout=1.0), 0)


if __name__ == "__main__":
    unittest.main()
