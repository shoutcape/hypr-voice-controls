from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
from logging import Logger
from typing import Callable


JobCallable = Callable[[threading.Event], int]


@dataclass
class _QueuedJob:
    job_id: int
    name: str
    fn: JobCallable
    future: Future[int]
    cancel_event: threading.Event
    queued_at_monotonic: float
    started_at_monotonic: float | None = None


@dataclass(frozen=True)
class QueueSnapshot:
    pending: int
    running_job_id: int | None
    running_job_name: str | None
    running_age_ms: int | None
    worker_alive: bool
    worker_restarts: int


class RuntimeJobQueue:
    def __init__(
        self,
        *,
        max_size: int = 8,
        worker_name: str = "voice-runtime-exec",
        logger: Logger | None = None,
    ) -> None:
        self._max_size = max_size
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._pending_jobs: deque[_QueuedJob] = deque()
        self._running_job: _QueuedJob | None = None
        self._job_ids = itertools.count(1)
        self._logger = logger
        self._worker_name = worker_name
        self._worker_restarts = 0
        self._worker = threading.Thread(target=self._worker_loop, name=worker_name, daemon=True)
        self._worker.start()
        self._monitor = threading.Thread(target=self._monitor_loop, name=f"{worker_name}-monitor", daemon=True)
        self._monitor.start()

    def submit(self, name: str, fn: JobCallable) -> Future[int] | None:
        future: Future[int] = Future()
        queued = _QueuedJob(
            job_id=next(self._job_ids),
            name=name,
            fn=fn,
            future=future,
            cancel_event=threading.Event(),
            queued_at_monotonic=time.monotonic(),
        )
        with self._condition:
            if len(self._pending_jobs) >= self._max_size:
                self._log("warning", "Runtime queue full job=%s pending=%s", name, len(self._pending_jobs))
                return None
            self._pending_jobs.append(queued)
            self._log(
                "info",
                "Runtime job queued id=%s name=%s pending=%s",
                queued.job_id,
                queued.name,
                len(self._pending_jobs),
            )
            self._condition.notify()
        return future

    def pending(self) -> int:
        with self._lock:
            return len(self._pending_jobs)

    def snapshot(self) -> QueueSnapshot:
        with self._lock:
            running_id: int | None = None
            running_name: str | None = None
            running_age_ms: int | None = None
            if self._running_job is not None:
                running_id = self._running_job.job_id
                running_name = self._running_job.name
                if self._running_job.started_at_monotonic is not None:
                    running_age_ms = int((time.monotonic() - self._running_job.started_at_monotonic) * 1000)

            return QueueSnapshot(
                pending=len(self._pending_jobs),
                running_job_id=running_id,
                running_job_name=running_name,
                running_age_ms=running_age_ms,
                worker_alive=self._worker.is_alive(),
                worker_restarts=self._worker_restarts,
            )

    def cancel_by_name(self, name: str) -> bool:
        cancelled = False
        with self._condition:
            if self._running_job is not None and self._running_job.name == name:
                self._running_job.cancel_event.set()
                self._log(
                    "info",
                    "Runtime job cancellation signaled id=%s name=%s",
                    self._running_job.job_id,
                    self._running_job.name,
                )
                cancelled = True

            remaining_jobs: deque[_QueuedJob] = deque()
            while self._pending_jobs:
                job = self._pending_jobs.popleft()
                if job.name == name:
                    job.future.cancel()
                    self._log("info", "Runtime job cancelled queued id=%s name=%s", job.job_id, job.name)
                    cancelled = True
                    continue
                remaining_jobs.append(job)
            self._pending_jobs = remaining_jobs
        return cancelled

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._pending_jobs:
                    self._condition.wait()
                job = self._pending_jobs.popleft()
                started_at = time.monotonic()
                job.started_at_monotonic = started_at
                self._running_job = job
                wait_ms = int((started_at - job.queued_at_monotonic) * 1000)
                self._log(
                    "info",
                    "Runtime job started id=%s name=%s pending=%s wait_ms=%s",
                    job.job_id,
                    job.name,
                    len(self._pending_jobs),
                    wait_ms,
                )

            try:
                if job.future.cancelled():
                    run_ms = 0
                    if job.started_at_monotonic is not None:
                        run_ms = int((time.monotonic() - job.started_at_monotonic) * 1000)
                    self._log("info", "Runtime job skipped cancelled id=%s name=%s run_ms=%s", job.job_id, job.name, run_ms)
                    continue
                result = int(job.fn(job.cancel_event))
                job.future.set_result(result)
                run_ms = 0
                if job.started_at_monotonic is not None:
                    run_ms = int((time.monotonic() - job.started_at_monotonic) * 1000)
                self._log("info", "Runtime job completed id=%s name=%s rc=%s run_ms=%s", job.job_id, job.name, result, run_ms)
            except Exception as exc:
                job.future.set_exception(exc)
                self._log("exception", "Runtime job failed id=%s name=%s: %s", job.job_id, job.name, exc)
            finally:
                with self._condition:
                    if self._running_job is job:
                        self._running_job = None

    def _log(self, level: str, message: str, *args: object) -> None:
        if self._logger is None:
            return
        getattr(self._logger, level)(message, *args)

    def _monitor_loop(self) -> None:
        while True:
            time.sleep(1.0)
            with self._condition:
                if self._worker.is_alive():
                    continue
                self._worker_restarts += 1
                self._log("error", "Runtime worker thread died; restarting restart_count=%s", self._worker_restarts)
                self._worker = threading.Thread(target=self._worker_loop, name=self._worker_name, daemon=True)
                self._worker.start()
