from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

RuntimeState = str

STATE_IDLE: RuntimeState = "idle"
STATE_COMMAND_HOLD: RuntimeState = "command_hold"
STATE_DICTATE_HOLD: RuntimeState = "dictate_hold"
STATE_WAKE_SESSION: RuntimeState = "wake_session"
STATE_TRANSCRIBING: RuntimeState = "transcribing"


@dataclass(frozen=True)
class TransitionResult:
    allowed: bool
    action: str
    previous_state: RuntimeState
    next_state: RuntimeState
    reason: str | None = None


class RuntimeStateMachine:
    def __init__(self) -> None:
        self._state: RuntimeState = STATE_IDLE
        self._lock = Lock()

    def get_state(self) -> RuntimeState:
        with self._lock:
            return self._state

    def transition(self, action: str) -> TransitionResult:
        with self._lock:
            previous = self._state
            allowed, next_state, reason = _resolve_transition(previous, action)
            if allowed:
                self._state = next_state
            else:
                next_state = previous
            return TransitionResult(
                allowed=allowed,
                action=action,
                previous_state=previous,
                next_state=next_state,
                reason=reason,
            )


def _resolve_transition(state: RuntimeState, action: str) -> tuple[bool, RuntimeState, str | None]:
    if action == "command-start":
        if state in {STATE_IDLE, STATE_COMMAND_HOLD}:
            return True, STATE_COMMAND_HOLD, None
        return False, state, "runtime_busy"

    if action == "command-stop":
        if state == STATE_COMMAND_HOLD:
            return True, STATE_TRANSCRIBING, None
        if state == STATE_IDLE:
            return True, STATE_IDLE, None
        return False, state, "invalid_transition"

    if action in {"command-stop-complete", "command-start-failed"}:
        if state == STATE_TRANSCRIBING or action == "command-start-failed":
            return True, STATE_IDLE, None
        return False, state, "invalid_transition"

    if action == "dictate-start":
        if state in {STATE_IDLE, STATE_DICTATE_HOLD}:
            return True, STATE_DICTATE_HOLD, None
        return False, state, "runtime_busy"

    if action == "dictate-stop":
        if state == STATE_DICTATE_HOLD:
            return True, STATE_TRANSCRIBING, None
        if state == STATE_IDLE:
            return True, STATE_IDLE, None
        return False, state, "invalid_transition"

    if action in {"dictate-stop-complete", "dictate-start-failed"}:
        if state == STATE_TRANSCRIBING or action == "dictate-start-failed":
            return True, STATE_IDLE, None
        return False, state, "invalid_transition"

    if action == "wake-start":
        if state == STATE_IDLE:
            return True, STATE_WAKE_SESSION, None
        return False, state, "runtime_busy"

    if action in {"wake-complete", "wake-failed"}:
        if state == STATE_WAKE_SESSION:
            return True, STATE_IDLE, None
        return False, state, "invalid_transition"

    return False, state, "unknown_action"
