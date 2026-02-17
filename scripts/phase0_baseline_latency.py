#!/usr/bin/env python3
from __future__ import annotations

import statistics
import subprocess
import sys
import time
from pathlib import Path


def _run_samples(cmd: list[str], iterations: int) -> list[int]:
    samples_ms: list[int] = []
    for _ in range(iterations):
        started = time.perf_counter()
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        elapsed_ms = int(round((time.perf_counter() - started) * 1000.0))
        samples_ms.append(elapsed_ms)
    return samples_ms


def _summarize(label: str, samples: list[int]) -> None:
    sorted_samples = sorted(samples)
    p95_index = max(0, int(len(sorted_samples) * 0.95) - 1)
    print(f"== {label} ==")
    print(f"samples={len(sorted_samples)}")
    print(f"min_ms={sorted_samples[0]}")
    print(f"max_ms={sorted_samples[-1]}")
    print(f"avg_ms={int(round(statistics.mean(sorted_samples)))}")
    print(f"p95_ms={sorted_samples[p95_index]}")


def main() -> int:
    iterations = 10
    if len(sys.argv) > 1:
        try:
            iterations = int(sys.argv[1])
        except ValueError:
            print(f"Invalid iterations value: {sys.argv[1]}", file=sys.stderr)
            return 2
    if iterations < 1:
        print("Iterations must be >= 1", file=sys.stderr)
        return 2

    repo_dir = Path(__file__).resolve().parents[1]
    hvc = repo_dir / "hvc"

    daemon_cmd = [str(hvc), "--input", "wakeword-status"]
    local_cmd = [str(hvc), "--input", "wakeword-status", "--local"]

    daemon_samples = _run_samples(daemon_cmd, iterations)
    local_samples = _run_samples(local_cmd, iterations)

    _summarize("daemon_roundtrip_wakeword_status", daemon_samples)
    _summarize("local_wakeword_status", local_samples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
