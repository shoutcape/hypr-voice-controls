#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


REQUIRED_STATUS_FIELDS = {
    "state",
    "pending",
    "running_job_id",
    "running_job_name",
    "running_age_ms",
    "worker_alive",
    "worker_restarts",
}


def _is_active(unit: str) -> bool:
    return subprocess.run(
        ["systemctl", "--user", "is-active", unit],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def _run(cmd: list[str], *, env: dict[str, str], capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        env=env,
        check=False,
        text=True,
        capture_output=capture,
    )


def _wait_for_socket(path: Path, timeout_s: float = 5.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def main() -> int:
    repo_dir = Path(__file__).resolve().parents[1]
    hvc = repo_dir / "hvc"
    socket_path = Path.home() / ".local" / "state" / "voice-hotkey.sock"

    if not hvc.exists():
        print(f"FAIL: missing CLI entrypoint: {hvc}")
        return 2

    voice_was_active = _is_active("voice-hotkey.service")
    wake_was_active = _is_active("wakeword.service")
    daemon_proc: subprocess.Popen[str] | None = None

    env = dict(os.environ)
    env["VOICE_RUNTIME_V2"] = "true"

    checks: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append((name, ok, detail))
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}: {detail}")

    try:
        if wake_was_active:
            subprocess.run(["systemctl", "--user", "stop", "wakeword.service"], check=False)
        if voice_was_active:
            subprocess.run(["systemctl", "--user", "stop", "voice-hotkey.service"], check=False)

        socket_path.unlink(missing_ok=True)

        daemon_proc = subprocess.Popen(
            [str(hvc), "--daemon"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        if not _wait_for_socket(socket_path):
            record("daemon_socket", False, f"socket not ready: {socket_path}")
            return 1
        record("daemon_socket", True, f"ready at {socket_path}")

        status_rc = _run([str(hvc), "--input", "wakeword-status"], env=env).returncode
        record("wakeword_status_rc", status_rc == 0, f"rc={status_rc}")

        auto_proc = subprocess.Popen([str(hvc), "--input", "command-auto"], env=env, text=True)
        time.sleep(0.9)
        stop_rc = _run([str(hvc), "--input", "command-stop"], env=env).returncode
        record("command_stop_cancel_rc", stop_rc == 0, f"rc={stop_rc}")

        try:
            auto_rc = auto_proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            auto_proc.kill()
            auto_rc = 124
        record("command_auto_cancelled_rc", auto_rc == 4, f"rc={auto_rc} expected=4")

        status_json = _run([str(hvc), "--input", "runtime-status-json"], env=env, capture=True)
        if status_json.returncode != 0:
            record("runtime_status_json_rc", False, f"rc={status_json.returncode}")
        else:
            try:
                payload = json.loads(status_json.stdout.strip() or "{}")
            except json.JSONDecodeError as exc:
                record("runtime_status_json_parse", False, f"invalid json: {exc}")
            else:
                missing = sorted(REQUIRED_STATUS_FIELDS - set(payload.keys()))
                record(
                    "runtime_status_json_fields",
                    not missing,
                    "all required fields present" if not missing else f"missing={','.join(missing)}",
                )

        health_rc = _run([str(repo_dir / "scripts" / "runtime-health-check.sh")], env=env).returncode
        record("runtime_health_check_rc", health_rc == 0, f"rc={health_rc}")

    finally:
        if daemon_proc is not None and daemon_proc.poll() is None:
            daemon_proc.terminate()
            try:
                daemon_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon_proc.kill()

        if voice_was_active:
            subprocess.run(["systemctl", "--user", "start", "voice-hotkey.service"], check=False)
        if wake_was_active:
            subprocess.run(["systemctl", "--user", "start", "wakeword.service"], check=False)

    failed = [name for (name, ok, _detail) in checks if not ok]
    if failed:
        print(f"\nResult: FAIL ({len(failed)} checks failed)")
        return 1

    print(f"\nResult: PASS ({len(checks)} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
